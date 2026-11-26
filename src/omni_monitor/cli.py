"""CLI entrypoint for serving the FastAPI app or running a one-shot monitor sweep."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

import uvicorn

from omni_monitor.api.app import create_app
from omni_monitor.config import Settings
from omni_monitor.engine import MonitorEngine
from omni_monitor.notifiers.fcm import FcmNotifier
from omni_monitor.registry import registry
from omni_monitor.seed import seed_default_tasks
from omni_monitor.storage import Storage


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="omni-monitor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve_p = sub.add_parser("serve", help="Start FastAPI server + background scheduler")
    serve_p.add_argument("--host", default=None)
    serve_p.add_argument("--port", type=int, default=None)

    run_p = sub.add_parser("run-once", help="Run all (or one) enabled tasks once and print results")
    run_p.add_argument("task_id", nargs="?", default=None)
    run_p.add_argument("--force", action="store_true", help="Ignore market-hours should_run check")

    args = parser.parse_args()
    settings = Settings()

    if args.cmd == "serve":
        app = create_app(settings)
        uvicorn.run(
            app,
            host=args.host or settings.host,
            port=args.port or settings.port,
        )
    elif args.cmd == "run-once":
        registry.discover_plugins()
        storage = Storage(settings.db_path)
        if settings.seed_defaults:
            seed_default_tasks(storage)
        notifier = FcmNotifier(settings.fcm_credentials_path)
        engine = MonitorEngine(storage, registry, notifier, settings)

        async def _run() -> None:
            if args.task_id:
                outcomes = [await engine.run_task_by_id(args.task_id, force=args.force or True)]
            else:
                tasks = storage.list_tasks(enabled_only=True)
                outcomes = await engine.run_tasks(tasks, force=args.force)
            for o in outcomes:
                print(json.dumps(o.__dict__, ensure_ascii=False))

        try:
            asyncio.run(_run())
        finally:
            storage.close()


if __name__ == "__main__":
    main()
