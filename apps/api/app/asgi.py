"""ASGI entry point.

uvicorn creates its own event loop, and on Windows the default is the proactor
loop, which the async PostgreSQL driver cannot use. Setting the policy here — in
the module uvicorn actually loads — is what makes the service work on Windows
without any per-request workaround.

Run:  uvicorn app.asgi:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    # Must happen before the loop is created and before the engine is imported.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.core.event_loop import ensure_compatible_event_loop

ensure_compatible_event_loop()

from app.main import app  # noqa: E402

__all__ = ["app"]
