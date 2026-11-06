"""Disabled gateway pending durable request and budget integration.

The historical transport is deliberately absent: it had only per-invocation
one-attempt semantics and could resend uncertain requests across invocations.
No runtime switch enables dispatch from this module.
"""
from __future__ import annotations

import os
from pathlib import Path


class GatewayClient:
    def __init__(self, credential_config: str | Path | None = None,
                 cache_dir: str | Path | None = None, timeout_s: float = 1200,
                 retries: int = 3, routes: dict | None = None,
                 channels: list[dict] | None = None):
        # Preserve constructor compatibility without reading credentials or cache.
        self.credential_config = Path(credential_config or os.environ.get(
            "NL2SQL_GATEWAY_CONFIG", "~/.config/nl2sql/deepseek_gateway_current.json"
        )).expanduser()
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout_s = timeout_s
        self.retries = retries
        self.routes = routes or {}
        self.channels = channels or []

    def complete(self, request: dict) -> dict:
        raise RuntimeError("Gateway inference is disabled")
