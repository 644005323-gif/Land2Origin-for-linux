from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ChannelMessage:
    channel: str
    account_id: str
    conversation_id: str
    message_id: str
    text: str
    attachments: tuple[Path, ...] = field(default_factory=tuple)


class DeliveryChannel(Protocol):
    """The narrow boundary every WeChat/Xianyu-like adapter must implement."""

    def send_text(self, conversation_id: str, text: str) -> None: ...

    def send_file(self, conversation_id: str, path: Path) -> None: ...
