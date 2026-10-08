"""Celery application and background workers.

Why the API never does this work inline: OCR, embedding generation, invoice
generation, bank synchronisation and report building all take longer than a
request should hold a connection. Each is a Celery task with retries, backoff,
an idempotency key and a durable row in `platform.tasks`.

A task never receives end-user credentials. It connects as `mytrakin_worker`,
which has BYPASSRLS but is not given a user's identity, so a compromised worker
cannot act as a specific person.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, overload

from celery import Celery, Task, signals
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

settings = get_settings()

celery_app = Celery(
    "mytrakin",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # At-least-once delivery: every task must be idempotent.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=15 * 60,
    task_soft_time_limit=14 * 60,
    result_expires=3600 * 24,
    broker_connection_retry_on_startup=True,
    task_default_retry_delay=10,
    task_default_queue="mytrakin",
    task_queues=(
        # Real-time and user-facing work on their own queue.
        ("critical", {"exchange": "direct", "routing_key": "critical"}),
        ("default", {"exchange": "direct", "routing_key": "default"}),
        # Bulk and long-running work must not starve interactive requests.
        ("bulk", {"exchange": "direct", "routing_key": "bulk"}),
        ("scheduled", {"exchange": "direct", "routing_key": "scheduled"}),
    ),
    beat_schedule={
        "outbox-dispatch": {
            "task": "app.workers.tasks.dispatch_outbox",
            "schedule": 10.0,
            "options": {"queue": "critical"},
        },
        "bank-sync": {
            "task": "app.workers.tasks.sync_due_bank_accounts",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "scheduled"},
        },
        "mark-invoices-overdue": {
            "task": "app.workers.tasks.mark_invoices_overdue",
            "schedule": crontab(minute=7),
            "options": {"queue": "scheduled"},
        },
        "refresh-dashboard-stats": {
            "task": "app.workers.tasks.refresh_dashboard_stats",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "bulk"},
        },
        "advance-payment-schedules": {
            "task": "app.workers.tasks.advance_payment_schedules",
            "schedule": crontab(minute=0),
            "options": {"queue": "scheduled"},
        },
        "contract-expiry-scan": {
            "task": "app.workers.tasks.scan_contract_expiries",
            "schedule": crontab(minute=0, hour=6),
            "options": {"queue": "bulk"},
        },
        "purge-expired-idempotency-keys": {
            "task": "app.workers.tasks.purge_expired_idempotency_keys",
            "schedule": crontab(minute=30),
            "options": {"queue": "bulk"},
        },
    },
)


@signals.task_failure.connect  # type: ignore[untyped-decorator]
def on_task_failure(
    sender: Task, exc: Exception, args: tuple[Any, ...], kwargs: dict[str, Any], **_extra: Any
) -> None:
    logger.error("celery_task_failed", task=sender.name, error=str(exc)[:300])


@overload
def async_task(func: Callable[..., Awaitable[Any]]) -> Callable[..., Any]: ...


@overload
def async_task(
    *,
    name: str | None = ...,
    max_retries: int = ...,
    base_backoff: int = ...,
    queue: str = ...,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Any]]: ...


def async_task(
    func: Callable[..., Awaitable[Any]] | None = None,
    *,
    name: str | None = None,
    max_retries: int = 5,
    base_backoff: int = 10,
    queue: str = "default",
) -> Any:
    """Run an async function as a Celery task with production retry policy.

    Usable bare or with arguments:

        @async_task
        async def work(): ...

        @async_task(queue="bulk", max_retries=3)
        async def heavy(): ...

    Retries use exponential backoff with jitter, and the task is marked DEAD in
    `platform.tasks` once retries are exhausted so the failure is inspectable
    rather than lost.
    """

    def decorate(target: Callable[..., Awaitable[Any]]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return celery_app.task(
                name=name or f"{target.__module__}.{target.__qualname__}",
                bind=True,
                max_retries=max_retries,
                queue=queue,
                autoretry_for=(Exception,),
                retry_backoff=base_backoff,
                retry_backoff_max=600,
                retry_jitter=True,
            )(lambda self: _run(self, target, *args, **kwargs))

        wrapper.__name__ = target.__name__
        wrapper.__doc__ = target.__doc__
        return wrapper

    # Bare usage: `@async_task`.
    if func is not None:
        return decorate(func)
    return decorate


def _run(task: Task, func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
    # A selector loop is required by the async database driver on Windows.
    from app.core.event_loop import new_event_loop

    loop = new_event_loop()
    try:
        return loop.run_until_complete(func(*args, **kwargs))
    finally:
        loop.close()


# ------------------------------------------------------------------ utilities
def new_task_id() -> str:
    return uuid.uuid4().hex


def new_company_id() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value
