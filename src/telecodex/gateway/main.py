from __future__ import annotations

import argparse

from telecodex.gateway.service import GatewayService
from telecodex.gateway.telegram import TelegramClient
from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.config import load_gateway_config


def main() -> None:
    parser = argparse.ArgumentParser(description="telecodex telegram gateway")
    parser.add_argument("--config", default="config/gateway.example.yaml")
    args = parser.parse_args()

    cfg = load_gateway_config(args.config)
    service = GatewayService(
        cfg=cfg,
        telegram=TelegramClient(cfg.telegram_token, timeout_sec=cfg.request_timeout_sec),
        worker=WorkerClient(cfg.worker_base_url, worker_token=cfg.worker_token, timeout_sec=cfg.request_timeout_sec),
    )
    service.poll_forever()


if __name__ == "__main__":
    main()
