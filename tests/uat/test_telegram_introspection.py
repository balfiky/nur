from __future__ import annotations

import asyncio
import time

import pytest

from core.dual_process.generator import MockLLMBackend
from runtime.channels.telegram import TelegramChannel, TelegramConfig
from runtime.config import RuntimeConfig
from runtime.sessions.manager import SessionManager

pytestmark = pytest.mark.uat


class TelegramProbeClient:
    def __init__(self) -> None:
        self.sent_messages: list[dict] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def send_message(self, chat_id: int, text: str) -> dict:
        self.sent_messages.append({"chat_id": chat_id, "text": text})
        return {"message_id": len(self.sent_messages)}

    async def send_typing(self, chat_id: int) -> None:
        pass


def _update(text: str, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": 100, "is_bot": False, "first_name": "UAT"},
            "chat": {"id": 100, "type": "private"},
            "date": int(time.time()),
            "text": text,
        },
    }


def test_state_command_without_session_does_not_create_session(tmp_path, monkeypatch):
    async def run() -> None:
        manager = SessionManager(
            RuntimeConfig(data_dir=str(tmp_path)),
            backend_factory=lambda: MockLLMBackend(),
        )
        client = TelegramProbeClient()
        channel = TelegramChannel(client, manager, TelegramConfig(token="123:abc"))

        async def fail_ensure_session(*_args, **_kwargs):
            raise AssertionError("introspection command created a session")

        monkeypatch.setattr(manager, "ensure_session", fail_ensure_session)
        await channel.handle_update(_update("/state"))

        assert manager.active_sessions == {}
        assert "No active session" in client.sent_messages[-1]["text"]
        await manager.shutdown()

    asyncio.run(run())


def test_introspection_commands_use_last_debug_without_processing(tmp_path, monkeypatch):
    async def run() -> None:
        manager = SessionManager(
            RuntimeConfig(data_dir=str(tmp_path)),
            backend_factory=lambda: MockLLMBackend(),
        )
        client = TelegramProbeClient()
        channel = TelegramChannel(client, manager, TelegramConfig(token="123:abc"))

        await manager.handle_message("telegram", "100", "100", "hello, I need help")
        session = manager.active_sessions["telegram:100:100"]

        def fail_process(*_args, **_kwargs):
            raise AssertionError("introspection command called pipeline.process")

        monkeypatch.setattr(session.pipeline, "process", fail_process)
        commands = ["/state", "/why", "/memory", "/loops", "/repair"]
        for idx, command in enumerate(commands, start=2):
            await channel.handle_update(_update(command, update_id=idx))

        sent = "\n\n".join(item["text"] for item in client.sent_messages)
        assert "Nūr state:" in sent
        assert "Why this response:" in sent
        assert "What I remember right now:" in sent
        assert "No active relationship loops." in sent or "Active loops:" in sent
        assert "Repair context:" in sent
        await manager.shutdown()

    asyncio.run(run())
