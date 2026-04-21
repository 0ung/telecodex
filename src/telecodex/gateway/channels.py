from __future__ import annotations

from telecodex.gateway.interfaces import ChatAdapter
from telecodex.gateway.telegram import TelegramAdapter
from telecodex.shared.config import GatewayConfig


def build_chat_adapter(cfg: GatewayConfig) -> ChatAdapter:
    provider = cfg.channel_provider.lower()
    if provider == "telegram":
        if not cfg.telegram_token:
            raise ValueError("gateway config: telegram_token is required for telegram provider")
        return TelegramAdapter(cfg.telegram_token, timeout_sec=cfg.request_timeout_sec)
    if provider == "slack":
        raise NotImplementedError("slack adapter scaffold is ready, but the runtime client is not implemented yet")
    if provider == "discord":
        raise NotImplementedError("discord adapter scaffold is ready, but the runtime client is not implemented yet")
    raise ValueError(f"unsupported gateway channel_provider: {cfg.channel_provider}")
