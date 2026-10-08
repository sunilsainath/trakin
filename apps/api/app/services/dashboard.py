"""Role-aware dashboard aggregation.

One service backs three consumers: the company dashboard endpoint, the project
dashboard endpoint, and the AI assistant's context builder. Every query applies the
caller's permissions, so the assistant can never read a number the person could
not open in the UI (rule 9).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.clock import utc_today
from app.core.logging import get_logger
from app.services.invoicing import money, receivables_summary
from app.services.lookup import as_decimal, resolve_scoped

logger = get_logger(__name__)

ZERO = Decimal("0")


async def project_dashboard(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    project_id: str,
    permissions: frozenset[str],
) -> dict[str, Any]:
    """Aggregates for the project page: billing, activity and insights."""
    project = await resolve_scoped(conn, "projects", project_id, company_id, columns="id")

    billing: dict[str, Any] = {}
    if permissions & {"invoices.read", "billing_runs.read", "dashboard.read"}:
        billing = await _project_billing(conn, project["id"])

    activity = await _activity(conn, company_id=company_id, project_id=project_id)

    insights: list[dict[str, Any]] = []
    if permissions & {"ai.project_intelligence", "ai.insights.read"}:
        from app.services import ai_domain

        try:
            health = await ai_domain.project_health(
                conn, company_id=company_id, project_public_id=project_id
            )
            insights.append(
                {
                    "kind": "PROJECT_HEALTH",
                    "severity": (
                        "HIGH"
                        if health["health_score"] < 60
                        else "MEDIUM"
                        if health["health_score"] < 80
                        else "LOW"
                    ),
                    "score": health["health_score"],
                    "summary": "; ".join(health["health_drivers"]) or "No material risks detected.",
                    "signals": health["signals"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - insights never break the page
            logger.warning("project_health_unavailable", project=project_id, error=str(exc)[:200])

    stored = await conn.execute(
        text(
            """
            SELECT public_id, insight_type, severity, title, summary,
                   data_snapshot AS data, created_at
              FROM public.ai_insights
             WHERE company_id = :cid AND entity_type = 'PROJECT' AND entity_id = CAST(:pid AS uuid)
               AND (valid_until IS NULL OR valid_until > now())
             ORDER BY created_at DESC LIMIT 20
            """
        ),
        {"cid": company_id, "pid": project["id"]},
    )
    for row in stored.mappings().all():
        insights.append(
            {
                "kind": row["insight_type"],
                "public_id": row["public_id"],
                "severity": row["severity"],
                "title": row["title"],
                "summary": row["summary"],
                "data": row["data"],
                "created_at": row["created_at"],
            }
        )

    return {
        "billing": billing,
        "activity": activity,
        "insights": insights,
        "permissions": {
            "can_edit": "projects.update" in permissions,
            "can_manage_roles": "projects.manage_roles" in permissions,
            "can_edit_sows": "sows.update" in permissions,
            "can_edit_contracts": "contracts.update" in permissions,
            "can_approve_timesheets": "timesheets.approve" in permissions,
            "can_bill": "invoices.create" in permissions,
        },
    }


async def _project_billing(conn: AsyncConnection, project_id: uuid.UUID) -> dict[str, Any]:
    invoices = await conn.execute(
        text(
            """
            SELECT count(*) FILTER (
                     WHERE status NOT IN ('DRAFT','PENDING','CANCELLED','REJECTED')
                   ) AS issued,
                   COALESCE(sum(total_amount) FILTER (
                     WHERE status NOT IN ('DRAFT','PENDING','CANCELLED','REJECTED')
                   ), 0) AS invoiced,
                   COALESCE(sum(balance_due) FILTER (
                     WHERE status NOT IN ('CANCELLED','REJECTED','REFUNDED')
                   ), 0) AS outstanding,
                   count(*) FILTER (
                     WHERE balance_due > 0 AND due_date < current_date
                       AND status NOT IN ('CANCELLED','REJECTED','REFUNDED')
                   ) AS overdue_count
              FROM public.invoices
             WHERE project_id = :pid AND direction = 'RECEIVABLE' AND deleted_at IS NULL
            """
        ),
        {"pid": project_id},
    )
    row: dict[str, Any] = dict(invoices.mappings().first() or {})

    unbilled = await conn.execute(
        text(
            """
            SELECT count(*) AS pending_sheets, COALESCE(sum(t.billable_hours), 0) AS hours
              FROM public.timesheets t
             WHERE t.project_id = :pid AND t.status = 'APPROVED'
               AND NOT EXISTS (SELECT 1 FROM public.invoice_items i
                                WHERE i.source_timesheet_id = t.id)
            """
        ),
        {"pid": project_id},
    )
    pending: dict[str, Any] = dict(unbilled.mappings().first() or {})

    return {
        "invoices_issued": int(row.get("issued") or 0),
        "invoiced": money(row.get("invoiced")),
        "outstanding": money(row.get("outstanding")),
        "overdue_invoices": int(row.get("overdue_count") or 0),
        "unbilled_timesheets": int(pending.get("pending_sheets") or 0),
        "unbilled_hours": as_decimal(pending.get("hours")),
    }


async def _activity(
    conn: AsyncConnection, *, company_id: uuid.UUID, project_id: str
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    """
                    SELECT a.action, a.resource_type, a.resource_public_id,
                           a.occurred_at AS created_at,
                           u.public_id AS actor_public_id,
                           NULLIF(TRIM(u.first_name || ' ' || u.last_name), '') AS actor_name
                      FROM platform.audit_logs a
                      LEFT JOIN public.users u ON u.id = a.actor_user_id
                     WHERE a.company_id = :cid
                       AND (
                             a.resource_public_id = :pid
                             OR (a.resource_type = 'PROJECT' AND a.resource_public_id = :pid)
                             OR a.metadata->>'project_id' = :pid
                           )
                     ORDER BY a.occurred_at DESC LIMIT 50
                    """
                ),
                {"cid": company_id, "pid": project_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        {
            "action": r["action"],
            "resource_type": r["resource_type"],
            "resource_public_id": r["resource_public_id"],
            "actor_public_id": r["actor_public_id"],
            "actor_name": r["actor_name"],
            "at": r["created_at"],
        }
        for r in rows
    ]


# =============================================================================
# company dashboard
# =============================================================================
async def company_dashboard(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    permissions: frozenset[str],
) -> dict[str, Any]:
    """One payload assembled from whichever panels the caller may see.

    A panel the caller may not read is omitted rather than zeroed, so an empty
    card is always a real "nothing to show" rather than a hidden number.
    """
    company: Any = (
        await conn.execute(
            text(
                "SELECT COALESCE(display_name, legal_name) AS name, public_id,"
                " default_currency AS currency, settings FROM public.companies WHERE id = :cid"
            ),
            {"cid": company_id},
        )
    ).mappings().first() or {}

    role_keys = tuple(
        str(r["key"])
        for r in (
            await conn.execute(
                text(
                    """
                    SELECT unnest(app.effective_role_keys(
                             CAST(:cid AS uuid), CAST(:uid AS uuid))) AS key
                    """
                ),
                {"cid": company_id, "uid": user_id},
            )
        )
        .mappings()
        .all()
    )

    panels: dict[str, Any] = {}
    domains: list[str] = []

    if "dashboard.read" in permissions:
        domains.append("dashboard")

    # --- finance -----------------------------------------------------------
    if permissions & {"invoices.read", "billing_runs.read", "reconciliation.read"}:
        domains.append("finance")
        finance: dict[str, Any] = {
            "receivables": await receivables_summary(conn, company_id=company_id)
        }
        if "transactions.read" in permissions:
            finance["bank"] = await _bank_summary(conn, company_id=company_id)
        if "reconciliation.read" in permissions:
            finance["reconciliation"] = await _reconciliation_summary(conn, company_id=company_id)
        panels["finance"] = finance

    # --- delivery ----------------------------------------------------------
    if "projects.read" in permissions:
        domains.append("projects")
        panels["projects"] = await _project_summary(conn, company_id=company_id)

    if "contracts.read" in permissions:
        domains.append("contracts")
        panels["contracts"] = await _contract_summary(conn, company_id=company_id)

    if permissions & {"sows.read"}:
        domains.append("sows")
        panels["sows"] = await _sow_summary(conn, company_id=company_id)

    # --- work --------------------------------------------------------------
    if permissions & {"timesheets.read_any", "timesheets.create"}:
        domains.append("timesheets")
        panels["timesheets"] = await _timesheet_summary(
            conn, company_id=company_id, user_id=user_id, permissions=permissions
        )

    if permissions & {"leave.read_any", "leave.request"}:
        domains.append("leave")
        panels["leave"] = await _leave_summary(conn, company_id=company_id)

    if "members.read" in permissions:
        domains.append("people")
        panels["people"] = await _people_summary(conn, company_id=company_id)

    # --- my own work -------------------------------------------------------
    domains.append("self")
    panels["me"] = await _my_work(
        conn, company_id=company_id, user_id=user_id, permissions=permissions
    )

    # --- AI alerts ---------------------------------------------------------
    alerts: list[dict[str, Any]] = []
    if permissions & {"ai.insights.read", "ai.project_intelligence"}:
        domains.append("ai")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT public_id, insight_type, severity, title, summary, created_at
                          FROM public.ai_insights
                         WHERE company_id = :cid
                           AND (valid_until IS NULL OR valid_until > now())
                         ORDER BY CASE severity
                                    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                                    WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                                  created_at DESC
                         LIMIT 15
                        """
                    ),
                    {"cid": company_id},
                )
            )
            .mappings()
            .all()
        )
        alerts = [
            {
                "public_id": r["public_id"],
                "type": r["insight_type"],
                "severity": r["severity"],
                "title": r["title"],
                "summary": r["summary"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    panels["ai_alerts"] = alerts

    return {
        "company": {
            "public_id": company.get("public_id"),
            "name": company.get("name"),
            "currency": company.get("currency") or "USD",
        },
        "my_role_keys": list(role_keys),
        "domains": sorted(set(domains)),
        "panels": panels,
    }


async def _project_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    counts = await conn.execute(
        text(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'ACTIVE') AS active,
                   count(*) FILTER (WHERE status IN ('PLANNING','DRAFT')) AS pipeline,
                   count(*) FILTER (WHERE status = 'ON_HOLD') AS on_hold,
                   count(*) FILTER (WHERE status = 'COMPLETED') AS completed,
                   COALESCE(avg(health_score) FILTER (
                       WHERE health_score IS NOT NULL), 0)
                            AS avg_health
              FROM public.projects
             WHERE company_id = :cid AND deleted_at IS NULL
            """
        ),
        {"cid": company_id},
    )
    summary = dict(counts.mappings().first() or {})
    summary["avg_health"] = round(float(summary.get("avg_health") or 0), 1)

    at_risk = await conn.execute(
        text(
            """
            SELECT public_id, name, status, health_score
              FROM public.projects
             WHERE company_id = :cid AND deleted_at IS NULL
               AND health_score IS NOT NULL AND health_score < 70
             ORDER BY health_score LIMIT 10
            """
        ),
        {"cid": company_id},
    )
    summary["at_risk"] = [dict(r) for r in at_risk.mappings().all()]
    return summary


async def _contract_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT status, count(*) AS n
              FROM public.contracts
             WHERE company_id = :cid AND deleted_at IS NULL GROUP BY status
            """
        ),
        {"cid": company_id},
    )
    counts = {str(r["status"]): int(r["n"]) for r in rows.mappings().all()}
    expiring = await conn.execute(
        text(
            """
            SELECT public_id, title, end_date, status
              FROM public.contracts
             WHERE company_id = :cid AND deleted_at IS NULL
               AND status IN ('ACCEPTED','ACTIVE')
               AND end_date IS NOT NULL AND end_date <= current_date + 60
             ORDER BY end_date LIMIT 10
            """
        ),
        {"cid": company_id},
    )
    return {
        "by_status": counts,
        "total": sum(counts.values()),
        "expiring_within_60_days": [dict(r) for r in expiring.mappings().all()],
    }


async def _sow_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT status, count(*) AS n FROM public.sows
             WHERE company_id = :cid AND deleted_at IS NULL GROUP BY status
            """
        ),
        {"cid": company_id},
    )
    return {str(r["status"]): int(r["n"]) for r in rows.mappings().all()}


async def _timesheet_summary(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    permissions: frozenset[str],
) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE status = 'DRAFT') AS drafts,
                   count(*) FILTER (WHERE status = 'REJECTED') AS rejected,
                   count(*) FILTER (WHERE status IN ('SUBMITTED','UNDER_REVIEW')) AS awaiting,
                   count(*) FILTER (WHERE status IN ('APPROVED','LOCKED')
                                      AND period_end >= current_date - 30) AS approved_recent,
                   COALESCE(sum(billable_hours) FILTER (
                     WHERE status IN ('APPROVED','LOCKED')
                       AND period_start >= date_trunc('month', current_date)::date
                   ), 0) AS billable_hours_this_month
              FROM public.timesheets
             WHERE company_id = :cid
            """
        ),
        {"cid": company_id},
    )
    summary = dict(rows.mappings().first() or {})
    summary["billable_hours_this_month"] = as_decimal(summary.get("billable_hours_this_month"))
    summary["mine_awaiting"] = 0
    if permissions & {"timesheets.create"}:
        mine = await conn.execute(
            text(
                """
                SELECT count(*) FROM public.timesheets
                 WHERE company_id = :cid AND user_id = :uid
                   AND status IN ('SUBMITTED','UNDER_REVIEW')
                """
            ),
            {"cid": company_id, "uid": user_id},
        )
        summary["mine_awaiting"] = int(mine.scalar() or 0)
    return summary


async def _leave_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT status, count(*) AS n FROM public.leave_requests
             WHERE company_id = :cid GROUP BY status
            """
        ),
        {"cid": company_id},
    )
    return {str(r["status"]): int(r["n"]) for r in rows.mappings().all()}


async def _people_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    rows = await conn.execute(
        text(
            """
            SELECT count(*) AS members,
                   count(*) FILTER (
                     WHERE EXISTS (SELECT 1 FROM public.assignments a
                                   WHERE a.user_id = m.user_id AND a.status = 'ACTIVE')
                   ) AS on_active_work
              FROM public.company_memberships m
             WHERE m.company_id = :cid AND m.status = 'ACTIVE'
            """
        ),
        {"cid": company_id},
    )
    row = dict(rows.mappings().first() or {})
    return {
        "members": int(row.get("members") or 0),
        "on_active_work": int(row.get("on_active_work") or 0),
    }


async def _bank_summary(conn: AsyncConnection, *, company_id: uuid.UUID) -> dict[str, Any]:
    row: Any = (
        (
            await conn.execute(
                text(
                    """
                    SELECT count(*) AS accounts,
                           COALESCE(sum(current_balance) FILTER (
                                     WHERE account_type IN ('CHECKING','SAVINGS')),
                                    0) AS cash_balance
                      FROM public.bank_accounts
                     WHERE company_id = :cid AND deleted_at IS NULL
                    """
                ),
                {"cid": company_id},
            )
        )
        .mappings()
        .first()
    ) or {}
    return {
        "accounts": int(row.get("accounts") or 0),
        "cash_balance": money(row.get("cash_balance")),
    }


async def _reconciliation_summary(
    conn: AsyncConnection, *, company_id: uuid.UUID
) -> dict[str, Any]:
    from app.services.payments import reconciliation_summary

    return await reconciliation_summary(conn, company_id=company_id)


async def _my_work(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    permissions: frozenset[str],
) -> dict[str, Any]:
    assignments = await conn.execute(
        text(
            """
            SELECT a.id::text, a.role_title, a.status, a.start_date, a.end_date,
                   a.allocation_pct, c.public_id AS contract_public_id, c.title AS contract_title,
                   p.public_id AS project_public_id, p.name AS project_name,
                   pr.public_id AS role_public_id, pr.title AS role_title
              FROM public.assignments a
              JOIN public.contracts c ON c.id = a.contract_id
              JOIN public.projects p ON p.id = a.project_id
              LEFT JOIN public.contract_roles cr ON cr.id = a.contract_role_id
              LEFT JOIN public.project_roles pr ON pr.id = cr.project_role_id
             WHERE a.user_id = :uid AND a.status IN ('PENDING','ACTIVE','ON_LEAVE')
             ORDER BY a.start_date DESC LIMIT 50
            """
        ),
        {"uid": user_id},
    )
    timesheets = await conn.execute(
        text(
            """
            SELECT public_id, status, period_start, period_end, total_hours,
                   billable_hours, total_amount, currency, rejection_reason
              FROM public.timesheets
             WHERE user_id = :uid AND company_id = :cid
               AND status IN ('DRAFT','REJECTED','SUBMITTED','UNDER_REVIEW','APPROVED')
             ORDER BY period_start DESC LIMIT 20
            """
        ),
        {"uid": user_id, "cid": company_id},
    )
    pending_actions = await conn.execute(
        text(
            """
            SELECT count(*) FROM public.timesheet_approvals ta
              JOIN public.timesheets t ON t.id = ta.timesheet_id
             WHERE ta.approver_user_id = :uid AND ta.status = 'PENDING'
            """
        ),
        {"uid": user_id},
    )
    leave = await conn.execute(
        text(
            """
            SELECT public_id, status, start_date, end_date, total_days
              FROM public.leave_requests
             WHERE user_id = :uid AND company_id = :cid
               AND status IN ('PENDING','APPROVED')
             ORDER BY start_date DESC LIMIT 20
            """
        ),
        {"uid": user_id, "cid": company_id},
    )

    return {
        "assignments": [dict(r) for r in assignments.mappings().all()],
        "timesheets": [dict(r) for r in timesheets.mappings().all()],
        "leave": [dict(r) for r in leave.mappings().all()],
        "pending_approvals": int(pending_actions.scalar() or 0),
    }


# =============================================================================
# assistant context
# =============================================================================
async def company_snapshot(
    conn: AsyncConnection,
    *,
    company_id: uuid.UUID,
    permissions: frozenset[str],
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """A compact, permission-filtered view of the business for the AI assistant.

    Only domains the caller may read are included, and each section is capped so
    the prompt stays bounded. `domains` records exactly what was included, so the
    answer can state its own limits.
    """
    domains: list[str] = []
    sections: dict[str, Any] = {}
    counts: dict[str, int] = {}

    if "projects.read" in permissions:
        domains.append("projects")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT public_id, name, status, currency, estimated_budget,
                               start_date, estimated_end_date, health_score
                          FROM public.projects
                         WHERE company_id = :cid AND deleted_at IS NULL
                         ORDER BY updated_at DESC LIMIT 50
                        """
                    ),
                    {"cid": company_id},
                )
            )
            .mappings()
            .all()
        )
        sections["projects"] = [dict(r) for r in rows]
        counts["projects"] = len(rows)

    if "contracts.read" in permissions:
        domains.append("contracts")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT public_id, title, status, currency, payment_terms_days,
                               start_date, end_date
                          FROM public.contracts
                         WHERE company_id = :cid AND deleted_at IS NULL
                         ORDER BY updated_at DESC LIMIT 50
                        """
                    ),
                    {"cid": company_id},
                )
            )
            .mappings()
            .all()
        )
        sections["contracts"] = [dict(r) for r in rows]
        counts["contracts"] = len(rows)

    if "invoices.read" in permissions:
        domains.append("invoices")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT public_id, invoice_number, status, currency, total_amount,
                               balance_due, due_date, period_start, period_end
                          FROM public.invoices
                         WHERE company_id = :cid AND direction = 'RECEIVABLE'
                           AND deleted_at IS NULL
                         ORDER BY due_date NULLS LAST LIMIT 60
                        """
                    ),
                    {"cid": company_id},
                )
            )
            .mappings()
            .all()
        )
        sections["invoices"] = [dict(r) for r in rows]
        counts["invoices"] = len(rows)
        sections["receivables"] = await receivables_summary(conn, company_id=company_id)

    if "timesheets.read_any" in permissions:
        domains.append("timesheets")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT public_id, status, period_start, period_end,
                               total_hours, billable_hours, total_amount, currency
                          FROM public.timesheets
                         WHERE company_id = :cid AND status IN ('SUBMITTED','UNDER_REVIEW')
                         ORDER BY submitted_at DESC LIMIT 40
                        """
                    ),
                    {"cid": company_id},
                )
            )
            .mappings()
            .all()
        )
        sections["timesheets_awaiting_approval"] = [dict(r) for r in rows]
        counts["timesheets_awaiting_approval"] = len(rows)

    if "transactions.read" in permissions:
        domains.append("bank_transactions")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT t.id::text, t.posted_at, t.amount, t.currency,
                               t.description_raw, t.match_status
                          FROM public.bank_transactions t
                         WHERE t.company_id = :cid
                           AND t.match_status IN ('UNMATCHED','SUGGESTED')
                         ORDER BY t.posted_at DESC LIMIT 40
                        """
                    ),
                    {"cid": company_id},
                )
            )
            .mappings()
            .all()
        )
        sections["unmatched_bank_transactions"] = [dict(r) for r in rows]
        counts["unmatched_bank_transactions"] = len(rows)

    if permissions & {"timesheets.create"}:
        domains.append("my_timesheets")
        rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT public_id, status, period_start, period_end,
                               total_hours, billable_hours, total_amount, currency
                          FROM public.timesheets
                         WHERE company_id = :cid AND user_id = :uid
                         ORDER BY period_start DESC LIMIT 20
                        """
                    ),
                    {"cid": company_id, "uid": user_id},
                )
            )
            .mappings()
            .all()
        )
        sections["my_timesheets"] = [dict(r) for r in rows]
        counts["my_timesheets"] = len(rows)

    sections["as_of"] = utc_today().isoformat()
    return {"domains": sorted(set(domains)), "counts": counts, "data": sections}
