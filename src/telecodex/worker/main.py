from __future__ import annotations

import argparse

import uvicorn

from telecodex.shared.config import load_worker_config
from telecodex.worker.service import create_worker_app


def main() -> None:
    parser = argparse.ArgumentParser(description="telecodex private worker")
    parser.add_argument("--config", default="config/worker.example.yaml")
    args = parser.parse_args()

    cfg = load_worker_config(args.config)
    app = create_worker_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()
