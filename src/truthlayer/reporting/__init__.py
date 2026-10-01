"""Reporting: Report DTO + JSON/HTML renderers (Sprint 5, #32, #33, #56).

Detectors never produce HTML. Everything funnels through one ReportDTO so
JSON, HTML and the future HTTP API share a single source of truth.
"""
