"""Unit tests for chunk-level transient retry in extraction.

Motivation: e2e runs against deepseek-flash showed a server-side
``response_format type is unavailable now`` 400 striking a different random
chunk on every run; without retry that one chunk zeroes the whole document
and silently removes downstream detector inputs. Retry must be bounded,
exponential, transient-only, and never fire on auth/deterministic errors.
"""

from __future__ import annotations

import pytest

from truthlayer.domain.errors import ProviderError
from truthlayer.extraction.service import (
    call_with_transient_retry,
    is_transient_provider_error,
)


def _flaky(failures, *, message="400 - This response_format type is unavailable now"):
    """Build a callable that raises ProviderError for the first N calls."""
    state = {"attempts": 0}

    def call():
        state["attempts"] += 1
        if state["attempts"] <= failures:
            raise ProviderError(message)
        return "envelope"

    return call, state


def test_succeeds_first_time_without_sleep_or_callback():
    slept: list[float] = []
    retries: list[int] = []
    call, state = _flaky(0)

    out = call_with_transient_retry(
        call, sleeper=slept.append,
        on_retry=lambda attempt, wait, exc: retries.append(attempt),
    )

    assert out == "envelope"
    assert state["attempts"] == 1
    assert slept == []
    assert retries == []


def test_retries_with_backoff_then_succeeds():
    slept: list[float] = []
    retries: list[tuple[int, float]] = []
    call, state = _flaky(2)

    out = call_with_transient_retry(
        call,
        backoff_seconds=(5.0, 15.0),
        sleeper=slept.append,
        on_retry=lambda attempt, wait, exc: retries.append((attempt, wait)),
    )

    assert out == "envelope"
    assert state["attempts"] == 3
    assert slept == [5.0, 15.0]
    assert retries == [(1, 5.0), (2, 15.0)]


def test_non_transient_error_propagates_without_waiting():
    slept: list[float] = []
    call, state = _flaky(
        1, message="401 - invalid api key: authentication failed"
    )

    with pytest.raises(ProviderError, match="invalid api key"):
        call_with_transient_retry(call, sleeper=slept.append)

    assert state["attempts"] == 1
    assert slept == []


def test_exhausted_transient_retries_raise_last_error():
    slept: list[float] = []
    call, state = _flaky(5)

    with pytest.raises(ProviderError, match="unavailable now"):
        call_with_transient_retry(
            call, max_attempts=3,
            backoff_seconds=(5.0, 15.0),
            sleeper=slept.append,
        )

    assert state["attempts"] == 3
    assert slept == [5.0, 15.0]


def test_non_provider_error_propagates_immediately():
    slept: list[float] = []

    def call():
        raise ValueError("not a provider error")

    with pytest.raises(ValueError, match="not a provider"):
        call_with_transient_retry(call, sleeper=slept.append)
    assert slept == []


def test_max_attempts_one_means_no_retry():
    slept: list[float] = []
    call, state = _flaky(1)

    with pytest.raises(ProviderError):
        call_with_transient_retry(
            call, max_attempts=1, sleeper=slept.append
        )
    assert state["attempts"] == 1
    assert slept == []


def test_invalid_max_attempts_rejected():
    with pytest.raises(ValueError):
        call_with_transient_retry(lambda: None, max_attempts=0)


def test_backoff_tuple_shorter_than_retries_uses_last_value():
    slept: list[float] = []
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise ProviderError("503 service unavailable")

    with pytest.raises(ProviderError):
        call_with_transient_retry(
            always_fails,
            max_attempts=4,
            backoff_seconds=(2.0,),
            sleeper=slept.append,
        )
    assert calls["n"] == 4
    assert slept == [2.0, 2.0, 2.0]


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 400 - {'error': {'message': "
        "'This response_format type is unavailable now'}}",
        "429 rate limit exceeded",
        "503 service unavailable",
        "502 Bad Gateway",
        "Request timed out after 30s",
        "Connection reset by peer",
        "The server is temporarily overloaded, try again",
        "500 Internal Server Error",
    ],
)
def test_transient_signatures_detected(message):
    assert is_transient_provider_error(ProviderError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "401 - invalid authentication token",
        "403 - forbidden",
        "400 - json schema validation failed: missing field 'entities'",
        "model not found: does-not-exist",
    ],
)
def test_permanent_signatures_not_retried(message):
    assert is_transient_provider_error(ProviderError(message)) is False
