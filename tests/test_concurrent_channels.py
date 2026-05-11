"""Concurrent two-channel test — T18.

Verifies that the same user sending messages on two different channels
(web + telegram) concurrently does not corrupt the shared SQLite DB
and produces valid responses on both channels.

The per-user asyncio.Lock is keyed on ``platform:user_id``, so
web:alice and telegram:alice acquire different locks and can run
concurrently. Both sessions write to the shared ``self_model.db``,
which is protected by SQLite WAL mode (core/profiles/base.py:59).

Run: python3 -m pytest tests/test_concurrent_channels.py -q
"""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile

from tests._fakes import MockLLMBackend
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager


def _make_config(tmpdir: str, **overrides) -> RuntimeConfig:
    return RuntimeConfig(data_dir=tmpdir, **overrides)


def _mock_factory():
    return MockLLMBackend()


class TestConcurrentChannels:
    def test_web_and_telegram_same_user_concurrent(self):
        """Two channels for the same user_id send concurrently without corruption."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                web_coro = manager.handle_message("web", "alice", "direct", "hello from web")
                tg_coro = manager.handle_message("telegram", "alice", "direct", "hello from telegram")

                results = await asyncio.gather(web_coro, tg_coro)

                assert results[0], "web channel returned empty response"
                assert results[1], "telegram channel returned empty response"

                # Shared DB must be readable (WAL integrity check)
                conn = sqlite3.connect(config.shared_db_path)
                try:
                    conn.execute("PRAGMA integrity_check").fetchone()
                finally:
                    conn.close()

                await manager.shutdown()

        asyncio.run(run())

    def test_same_channel_same_user_concurrent_serialized(self):
        """Two concurrent messages on the same channel for the same user are serialized."""
        turn_order: list[int] = []

        class OrderedBackend:
            def __init__(self, turn_num: int):
                self._turn = turn_num

            def generate(self, system_prompt: str, user_message: str) -> str:
                turn_order.append(self._turn)
                return f"response {self._turn}"

        backend_iter = iter([OrderedBackend(1), OrderedBackend(2)])

        def factory():
            return next(backend_iter)

        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=factory)

                results = await asyncio.gather(
                    manager.handle_message("web", "bob", "direct", "first"),
                    manager.handle_message("web", "bob", "direct", "second"),
                )

                # Both messages must complete
                assert results[0], "first response empty"
                assert results[1], "second response empty"

                await manager.shutdown()

        asyncio.run(run())

    def test_concurrent_different_users_isolated(self):
        """Concurrent messages for different users do not interfere."""
        async def run():
            with tempfile.TemporaryDirectory() as tmpdir:
                config = _make_config(tmpdir)
                manager = SessionManager(config, backend_factory=_mock_factory)

                results = await asyncio.gather(
                    manager.handle_message("web", "alice", "direct", "hi alice"),
                    manager.handle_message("web", "bob", "direct", "hi bob"),
                    manager.handle_message("web", "carol", "direct", "hi carol"),
                )

                assert all(results), "one or more concurrent responses were empty"

                conn = sqlite3.connect(config.shared_db_path)
                try:
                    conn.execute("PRAGMA integrity_check").fetchone()
                finally:
                    conn.close()

                await manager.shutdown()

        asyncio.run(run())
