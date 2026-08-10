from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import orjson

NEXT_STEPS = frozenset({"call_back", "send_proposal", "disqualify", "escalate"})


def empty_buffers() -> dict[str, str]:
    return {
        "summary": "",
        "objections": "",
        "next_step": "",
        "lead_score": "",
        "confidence": "",
    }


def apply_delta(buffers: dict[str, str], field: str, delta: str) -> None:
    if field not in buffers:
        buffers[field] = ""
    buffers[field] += delta


def _parse_summary(raw: str) -> str | None:
    return raw or None


def _parse_objections(raw: str) -> list[str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
        return parsed
    return None


def _parse_next_step(raw: str) -> str | None:
    return raw if raw in NEXT_STEPS else None


def _parse_int_range(raw: str, lo: int, hi: int) -> int | None:
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(val, int) and lo <= val <= hi:
        return val
    return None


def _parse_confidence(raw: str) -> float | None:
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(val, (int, float)) and 0 <= float(val) <= 1:
        return float(val)
    return None


_PARSERS: dict[str, Callable[[str], Any | None]] = {
    "summary": _parse_summary,
    "objections": _parse_objections,
    "next_step": _parse_next_step,
    "lead_score": lambda r: _parse_int_range(r, 0, 100),
    "confidence": _parse_confidence,
}


def buffers_to_partial(buffers: dict[str, str]) -> dict[str, Any]:
    partial: dict[str, Any] = {}
    for field, parse in _PARSERS.items():
        val = parse(buffers.get(field, ""))
        if val is not None:
            partial[field] = val
    return partial


def merge_partial(existing: dict[str, Any] | None, field: str, delta: str) -> dict[str, Any]:
    """Convenience for single-delta update when buffers aren't kept in memory."""
    buffers = empty_buffers()
    if existing:
        # Reconstruct approximate buffers from partial for summary/next_step only.
        # Prefer keeping full buffers in the consumer.
        for k, v in existing.items():
            if k == "summary" and isinstance(v, str):
                buffers[k] = v
            elif k == "next_step" and isinstance(v, str):
                buffers[k] = v
            elif k in ("objections", "lead_score", "confidence"):
                buffers[k] = json.dumps(v, ensure_ascii=False)
    apply_delta(buffers, field, delta)
    return buffers_to_partial(buffers)


def row_to_analysis(row: Any) -> dict[str, Any]:
    def _json(val: Any) -> dict[str, Any] | None:
        if val is None:
            return None
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            return orjson.loads(val)
        return dict(val)

    return {
        "id": row["id"],
        "call_attempt_id": row["call_attempt_id"],
        "status": row["status"],
        "result": _json(row["result"]),
        "partial": _json(row["partial"]),
        "error": row["error"],
        "created_at": row["created_at"],
    }
