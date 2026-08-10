"""Measure claim_next_contact latency with parallel workers."""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import jwt

ORG_ID = UUID("00000000-0000-4000-8000-000000000001")
CAMPAIGN_ID = UUID("00000000-0000-4000-8000-000000000010")


def make_token(secret: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": "load-worker",
            "org_id": str(ORG_ID),
            "role": "worker",
            "iat": now,
            "exp": now + timedelta(hours=2),
        },
        secret,
        algorithm="HS256",
    )


async def worker(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    n: int,
    latencies: list[float],
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    body = {"campaign_id": str(CAMPAIGN_ID)}
    for _ in range(n):
        t0 = time.perf_counter()
        r = await client.post(url, json=body, headers=headers)
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        if r.status_code != 200:
            print(f"error status={r.status_code} body={r.text[:200]}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8080")
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--requests", type=int, default=200)
    args = parser.parse_args()

    secret = os.environ.get("JWT_SECRET", "dev-jwt-secret-change-me")
    token = make_token(secret)
    url = f"{args.base.rstrip('/')}/rpc/claim_next_contact"
    per_worker = max(1, args.requests // args.workers)
    latencies: list[float] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        await asyncio.gather(
            *[
                worker(client, url, token, per_worker, latencies)
                for _ in range(args.workers)
            ]
        )

    latencies.sort()
    def pct(p: float) -> float:
        if not latencies:
            return float("nan")
        idx = min(len(latencies) - 1, int(round((p / 100) * (len(latencies) - 1))))
        return latencies[idx]

    print(
        {
            "n": len(latencies),
            "p50_ms": round(pct(50), 2),
            "p95_ms": round(pct(95), 2),
            "p99_ms": round(pct(99), 2),
            "mean_ms": round(statistics.fmean(latencies), 2) if latencies else None,
        }
    )


if __name__ == "__main__":
    asyncio.run(main())
