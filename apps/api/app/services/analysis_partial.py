from __future__ import annotations

import json
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


def buffers_to_partial(buffers: dict[str, str]) -> dict[str, Any]:
    partial: dict[str, Any] = {}
    if buffers.get("summary"):
        partial["summary"] = buffers["summary"]

    raw_obj = buffers.get("objections", "")
    if raw_obj:
        try:
            parsed = json.loads(raw_obj)
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                partial["objections"] = parsed
        except json.JSONDecodeError:
            pass

    raw_ns = buffers.get("next_step", "")
    if raw_ns in NEXT_STEPS:
        partial["next_step"] = raw_ns

    raw_ls = buffers.get("lead_score", "")
    if raw_ls:
        try:
            val = json.loads(raw_ls)
            if isinstance(val, int) and 0 <= val <= 100:
                partial["lead_score"] = val
        except json.JSONDecodeError:
            pass

    raw_cf = buffers.get("confidence", "")
    if raw_cf:
        try:
            val = json.loads(raw_cf)
            if isinstance(val, (int, float)) and 0 <= float(val) <= 1:
                partial["confidence"] = float(val)
        except json.JSONDecodeError:
            pass

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
