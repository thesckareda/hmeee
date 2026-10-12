"""
Pre-warmed LiveKit worker pool manager.

Keeps N agent worker processes running to eliminate cold-start dispatch latency.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys

logger = logging.getLogger("inbound-voice-agent")

WORKER_COUNT = int(os.environ.get("LIVEKIT_WORKER_COUNT", "15"))
WORKER_COMMAND = [sys.executable, "agent.py", "start"]

_workers: list[subprocess.Popen] = []


def start_worker_pool() -> None:
    """Start N pre-warmed LiveKit agent workers at startup."""
    global _workers
    _workers = []
    for i in range(WORKER_COUNT):
        p = subprocess.Popen(
            WORKER_COMMAND,
            env={**os.environ, "WORKER_ID": str(i)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _workers.append(p)
    logger.info("[POOL] Started %d pre-warmed workers", WORKER_COUNT)


async def monitor_pool() -> None:
    """Restart crashed workers. Keep pool size constant."""
    while True:
        await asyncio.sleep(5)
        for i, worker in enumerate(_workers):
            if worker.poll() is not None:
                logger.warning("[POOL] Worker %d died — restarting", i)
                p = subprocess.Popen(
                    WORKER_COMMAND,
                    env={**os.environ, "WORKER_ID": str(i)},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                _workers[i] = p


async def run_pool_supervisor() -> None:
    """Entry point for supervisord: start pool then monitor forever."""
    logging.basicConfig(level=logging.INFO)
    start_worker_pool()
    await monitor_pool()


if __name__ == "__main__":
    asyncio.run(run_pool_supervisor())