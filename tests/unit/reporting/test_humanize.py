"""Per-type narratives used by CLI list/show and both report formats."""

from __future__ import annotations

from truthlayer.reporting.humanize import narrative, short_title


def test_conflict_title_uses_both_values_and_sources() -> None:
    title = short_title(
        "conflict",
        {
            "subject": "ACME CRM Pro",
            "predicate": "座位分区",
            "old_value": "A区",
            "new_value": "B区",
            "old_source": "org_2025.md",
            "new_source": "org_2026.md",
        },
    )
    assert "ACME CRM Pro" in title
    assert "A区" in title and "B区" in title
    assert "org_2025.md" in title


def test_stale_title_shows_age() -> None:
    title = short_title(
        "possibly_stale",
        {"subject": "S", "predicate": "促销价", "age_days": 402},
    )
    assert "402" in title

    confirmed = short_title(
        "confirmed_stale",
        {"subject": "S", "predicate": "价格", "reason": "valid_to_expired"},
    )
    assert "已确认过期" in confirmed


def test_superseded_and_duplicate_titles() -> None:
    assert (
        "price_2025.csv"
        in short_title(
            "superseded",
            {"old_source": "price_2025.csv", "new_source": "price_2026.csv"},
        )
    )
    dup = short_title(
        "duplicate",
        {
            "entity_a": "ACME CRM",
            "entity_b": "ACME CRM Pro",
            "predicate": "厂商",
            "object": "Acme",
        },
    )
    assert "ACME CRM" in dup and "疑似重复实体" in dup


def test_narrative_answers_why_and_action_for_every_type() -> None:
    for drift_type in (
        "conflict",
        "confirmed_stale",
        "possibly_stale",
        "superseded",
        "duplicate",
    ):
        story = narrative(drift_type, {})
        assert story.why and story.recommendation
        assert len(story.suggested_decisions) >= 1

    conflict = narrative("conflict", {})
    assert "false_positive" in conflict.suggested_decisions
    # recommendation is plain business language — no internal enum codes
    assert "accept_newer" not in conflict.recommendation
    assert "采用" in conflict.recommendation

    dup = narrative("duplicate", {})
    assert dup.suggested_decisions[0] == "accept_newer"


def test_unknown_type_is_graceful() -> None:
    story = narrative("future_type", {"subject": "x", "predicate": "y"})
    assert story.title and story.recommendation
