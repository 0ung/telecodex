from __future__ import annotations

import argparse

from telecodex.gateway.channels import build_chat_adapter
from telecodex.gateway.service import GatewayService
from telecodex.gateway.worker_client import WorkerClient
from telecodex.shared.config import load_gateway_config
from telecodex.shared.http_client import HttpTimeoutConfig, ResilientHttpClient


def main() -> None:
    parser = argparse.ArgumentParser(description="telecodex conversational gateway")
    parser.add_argument("--config", default="config/gateway.example.yaml")
    args = parser.parse_args()

    cfg = load_gateway_config(args.config)
    http_client = ResilientHttpClient(
        timeout=HttpTimeoutConfig(
            connect_sec=cfg.request_connect_timeout_sec,
            read_sec=cfg.request_read_timeout_sec,
            write_sec=cfg.request_write_timeout_sec,
            pool_sec=cfg.request_pool_timeout_sec,
        ),
        max_retries=cfg.request_max_retries,
        backoff_sec=cfg.request_retry_backoff_sec,
    )
    service = GatewayService(
        cfg=cfg,
        chat=build_chat_adapter(cfg, http_client),
        worker=WorkerClient(
            cfg.worker_base_url,
            worker_token=cfg.worker_token,
            timeout_sec=cfg.request_timeout_sec,
            http_client=http_client,
        ),
    )
    try:
        service.poll_forever()
    finally:
        service.stop()


if __name__ == "__main__":
    main()
