"""Server entry point.

Run with:
    python -m app.serve --host 0.0.0.0 --port 8000 --workers 4
    python -m app.serve --reload                    # local development

Why this exists instead of `uvicorn app.asgi:app`:

uvicorn's `loop="auto"` resolves to `asyncio.ProactorEventLoop` on Windows and to
uvloop when it is installed, both of which ignore the event loop policy. The async
PostgreSQL driver (psycopg) cannot run on a proactor loop and fails with an opaque
`InterfaceError` on the first query — a failure that appears in production but not in
tests, because the tests build their own loop.

`loop="none"` tells uvicorn to delegate to `asyncio.run`, which uses the current
policy. The policy is set to a selector loop on Windows below, and the default
elsewhere, so the driver works identically on every platform. No monkey-patching, and
uvloop can still be enabled explicitly in deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":
    # The async database driver cannot use the Windows proactor loop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.config import get_settings
from app.core.event_loop import ensure_compatible_event_loop
from app.core.logging import configure_logging, get_logger

ensure_compatible_event_loop()

logger = get_logger(__name__)

# Delegates loop creation to asyncio.run, which honours the policy above.
LOOP_SETTING = "none"


def _worker_count(requested: int, reload: bool) -> int:
    if reload or requested <= 1:
        return 1
    if sys.platform == "win32":
        # Multiprocessing on Windows uses spawn, and uvicorn's in-process worker
        # manager is not reliable there. Use a process supervisor in deployment.
        logger.info("single_worker_on_windows", requested=requested)
        workers = 1
    else:
        workers = requested
    return workers


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    parser = argparse.ArgumentParser(description="MyTrakin API server")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    parser.add_argument("--workers", type=int, default=settings.api_workers)
    parser.add_argument("--reload", action="store_true", help="development autoreload")
    parser.add_argument("--log-level", default=settings.log_level.lower())
    parser.add_argument(
        "--loop",
        default=LOOP_SETTING,
        help="uvicorn loop setting; 'none' honours the event loop policy",
    )
    args = parser.parse_args(argv)

    import uvicorn

    from app.main import app

    logger.info(
        "server_starting",
        host=args.host,
        port=args.port,
        workers=_worker_count(args.workers, args.reload),
        reload=args.reload,
        loop=args.loop,
        environment=settings.environment,
    )

    if args.reload:
        # The reloader re-imports this module in the child, re-applying the policy.
        uvicorn.run(
            "app.asgi:app",
            host=args.host,
            port=args.port,
            reload=True,
            log_level=args.log_level,
            loop=args.loop,
        )
        return 0

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        workers=_worker_count(args.workers, args.reload),
        loop=args.loop,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
