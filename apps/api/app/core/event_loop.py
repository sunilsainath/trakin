"""Event loop policy for Windows.

psycopg's async driver requires a selector-based event loop. Since Python 3.8 the
default on Windows is the proactor loop, which psycopg cannot use, and the failure
surfaces as an opaque `InterfaceError` on the first query rather than at startup.

Installing the selector policy here — before the engine or the Celery app is
created — keeps the fix in one place for the API, the workers and the tests.

On Linux and macOS the default policy is already selector-based, so this is a
no-op and importing it is always safe.
"""

from __future__ import annotations

import asyncio
import sys

_INITIALISED = False


def ensure_compatible_event_loop() -> None:
    """Switch to a selector event loop on Windows. Idempotent."""
    global _INITIALISED
    if _INITIALISED or sys.platform != "win32":
        return

    if type(asyncio.get_event_loop_policy()).__name__ == "WindowsProactorEventLoopPolicy":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    _INITIALISED = True


def new_event_loop() -> asyncio.AbstractEventLoop:
    """A fresh selector-compatible loop. Used by the Celery async bridge."""
    ensure_compatible_event_loop()
    return asyncio.new_event_loop()


ensure_compatible_event_loop()
