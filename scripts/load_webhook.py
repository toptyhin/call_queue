"""Measure webhook ingest latency at a target RPS."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import statistics
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import orjson


def sign(raw: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--rps", type=int, default=50)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()

    secret = os.environ.get("WEBHOOK_SECRET", "dev-webhook-secret")
    url = f"{args.base.rstrip('/')}/webhooks/calls"
    total = args.rps * args.seconds
    interval = 1.0 / args.rps
    latencies: list[float] = []
    errors = 0

    async with httpx.AsyncClient(timeout=5.0) as client:
        start = time.perf_counter()
        for i in range(total):
            scheduled = start + i * interval
            delay = scheduled - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            body = {
                "event_id": f"evt_load_{uuid4().hex}",
                "call_id": f"call_load_{i % 10_000}",
                "sequence": i + 1,
                "type": "call.unknown_load",
                "occurred_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "data": {},
            }
            raw = orjson.dumps(body)
            t0 = time.perf_counter()
            try:
                r = await client.post(
                    url,
                    content=raw,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature": sign(raw, secret),
                    },
                )
                latencies.append((time.perf_counter() - t0) * 1000)
                if r.status_code != 200:
                    errors += 1
            except Exception:  # noqa: BLE001
                errors += 1
                latencies.append((time.perf_counter() - t0) * 1000)

    latencies.sort()

    def pct(p: float) -> float:
        if not latencies:
            return float("nan")
        idx = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
        return latencies[idx]

    print(
        {
            "n": len(latencies),
            "errors": errors,
            "p50_ms": round(pct(50), 2),
            "p95_ms": round(pct(95), 2),
            "p99_ms": round(pct(99), 2),
            "mean_ms": round(statistics.fmean(latencies), 2) if latencies else None,
            "elapsed_s": round(time.perf_counter() - start, 2),
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
