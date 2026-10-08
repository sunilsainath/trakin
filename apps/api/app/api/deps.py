"""Authorization dependencies.

`require_user` verifies the token and resolves the platform user.
`require_company` additionally resolves and validates company context.
`require_permission` is the single gate for a business action.

Nothing here trusts the client. The company id comes from a *public* id in a
header, is resolved through the membership table, and is then set as the session
identity so RLS applies to every downstream query.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import (
    CompanyContextError,
    NotAMemberError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from app.core.logging import company_id_var, get_logger, user_id_var
from app.core.security import AuthenticatedUser, TokenVerifier, assert_account_active
from app.db.session import request_session, set_identity

logger = get_logger(__name__)


@dataclass(slots=True)
class RequestContext:
    """Everything a handler needs to know about the caller."""

    user_id: uuid.UUID
    auth: AuthenticatedUser
    company_id: uuid.UUID | None = None
    company_public_id: str | None = None
    request_id: str = ""
    ip_address: str | None = None
    user_agent: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    role_keys: tuple[str, ...] = ()

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def can_any(self, *permissions: str) -> bool:
        return any(p in self.permissions for p in permissions)

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise PermissionDeniedError(
                f"This action requires the {permission} permission.",
                request_id=self.request_id,
            )


# --------------------------------------------------------------------- helpers
def _client_ip(request: Request) -> str | None:
    # Azure Front Door sets X-Forwarded-For; the left-most entry is the original
    # client. Recorded for the audit trail only, never used for authorization.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


_token_verifier: TokenVerifier | None = None


def get_token_verifier() -> TokenVerifier:
    global _token_verifier
    if _token_verifier is None:
        _token_verifier = TokenVerifier()
    return _token_verifier


async def _load_permissions(
    conn: AsyncConnection, company_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[frozenset[str], tuple[str, ...]]:
    # app.my_permissions is SETOF text. Selecting it bare and calling .scalar()
    # returns only the first row, which would silently reduce the caller to a
    # single permission. A set-returning function cannot appear inside an
    # aggregate, so it is moved into a FROM subquery and aggregated there.
    perms = await conn.execute(
        text(
            """
            SELECT COALESCE(array_agg(perm), '{}')
              FROM app.my_permissions(:company_id, :user_id) AS perm
            """
        ),
        {"company_id": company_id, "user_id": user_id},
    )
    roles = await conn.execute(
        text("SELECT app.effective_role_keys(:company_id, :user_id)"),
        {"company_id": company_id, "user_id": user_id},
    )
    return frozenset(perms.scalar() or []), tuple(roles.scalar() or [])


# ------------------------------------------------------------------ user level
async def require_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AsyncIterator[tuple[RequestContext, AsyncConnection]]:
    """Authenticate and yield a transaction carrying the caller's identity."""
    verifier = get_token_verifier()
    auth = verifier.authenticate(authorization)

    request_id = getattr(request.state, "request_id", "")

    async with request_session(user_id=None, company_id=None, request_id=request_id) as conn:
        user_id = uuid.UUID(await assert_account_active(conn, auth.auth_id, email=auth.email))
        await set_identity(conn, user_id=user_id, company_id=None, request_id=request_id)

        user_id_var.set(str(user_id))
        ctx = RequestContext(
            user_id=user_id,
            auth=auth,
            request_id=request_id,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        yield ctx, conn


# --------------------------------------------------------------- company level
async def require_company(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_company_id: Annotated[str | None, Header(alias="X-Company-Public-Id")] = None,
) -> AsyncIterator[tuple[RequestContext, AsyncConnection]]:
    """Authenticate, resolve company context, and load effective permissions.

    An unknown or non-member `X-Company-Public-Id` is treated as no context at
    all. Guessing another company's id therefore yields zero rows downstream
    rather than a distinguishable error.
    """
    verifier = get_token_verifier()
    auth = verifier.authenticate(authorization)
    request_id = getattr(request.state, "request_id", "")

    async with request_session(user_id=None, company_id=None, request_id=request_id) as conn:
        user_id = uuid.UUID(await assert_account_active(conn, auth.auth_id, email=auth.email))

        if not x_company_id:
            await set_identity(conn, user_id=user_id, company_id=None, request_id=request_id)
            user_id_var.set(str(user_id))
            yield (
                RequestContext(
                    user_id=user_id,
                    auth=auth,
                    request_id=request_id,
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn,
            )
            return

        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT c.id::text AS company_id, c.public_id
                      FROM public.companies c
                      JOIN public.company_memberships m
                        ON m.company_id = c.id AND m.user_id = :user_id
                     WHERE c.public_id = :public_id
                       AND m.status = 'ACTIVE'
                       AND c.deleted_at IS NULL
                    """
                    ),
                    {"user_id": user_id, "public_id": x_company_id},
                )
            )
            .mappings()
            .first()
        )

        if row is None:
            # Deliberately ambiguous: "no company" rather than "not a member of
            # that company", so a valid-but-foreign id is indistinguishable from
            # an invented one.
            await set_identity(conn, user_id=user_id, company_id=None, request_id=request_id)
            user_id_var.set(str(user_id))
            yield (
                RequestContext(
                    user_id=user_id,
                    auth=auth,
                    request_id=request_id,
                    ip_address=_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                ),
                conn,
            )
            return

        company_id = uuid.UUID(str(row["company_id"]))
        await set_identity(conn, user_id=user_id, company_id=company_id, request_id=request_id)
        user_id_var.set(str(user_id))
        company_id_var.set(str(company_id))

        perms, roles = await _load_permissions(conn, company_id, user_id)

        yield (
            RequestContext(
                user_id=user_id,
                auth=auth,
                company_id=company_id,
                company_public_id=str(row["public_id"]),
                request_id=request_id,
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                permissions=perms,
                role_keys=roles,
            ),
            conn,
        )


async def require_company_member(
    ctx_and_conn: Annotated[tuple[RequestContext, AsyncConnection], Depends(require_company)],
) -> tuple[RequestContext, AsyncConnection]:
    """Company context is mandatory for this endpoint."""
    ctx, conn = ctx_and_conn
    if ctx.company_id is None:
        raise CompanyContextError(
            "This endpoint requires a valid X-Company-Public-Id header.",
            request_id=ctx.request_id,
        )
    if not ctx.can_any("dashboard.read", "members.read", "projects.read"):
        raise NotAMemberError(request_id=ctx.request_id)
    return ctx, conn


# ------------------------------------------------------------------ permissions
PermissionDependency = Callable[..., Awaitable[tuple[RequestContext, AsyncConnection]]]


def require_permission(permission: str) -> PermissionDependency:
    """Dependency factory gating an endpoint on one permission.

        @router.post("/contracts/{public_id}/send")
        async def send(...):
            _ = Depends(require_permission("contracts.send"))

    The check reads the same `app.has_permission` the RLS policies use, so the
    application and the database cannot disagree.
    """

    async def dependency(
        ctx_and_conn: Annotated[
            tuple[RequestContext, AsyncConnection], Depends(require_company_member)
        ],
    ) -> tuple[RequestContext, AsyncConnection]:
        ctx, conn = ctx_and_conn
        if permission not in ctx.permissions:
            logger.info(
                "permission_denied",
                permission=permission,
                company_id=str(ctx.company_id or ""),
            )
            raise PermissionDeniedError(
                f"This action requires the {permission} permission.",
                request_id=ctx.request_id,
            )
        return ctx, conn

    dependency.__name__ = f"require_{permission.replace('.', '_')}"
    return dependency


def require_any_permission(*permissions: str) -> PermissionDependency:
    """Dependency factory for endpoints reachable through any one of several keys."""

    async def dependency(
        ctx_and_conn: Annotated[
            tuple[RequestContext, AsyncConnection], Depends(require_company_member)
        ],
    ) -> tuple[RequestContext, AsyncConnection]:
        ctx, conn = ctx_and_conn
        if not any(p in ctx.permissions for p in permissions):
            raise PermissionDeniedError(request_id=ctx.request_id)
        return ctx, conn

    dependency.__name__ = "require_any_permission"
    return dependency


def require_user_or_permission(permission: str) -> PermissionDependency:
    """Company permission when a company is in context, plain identity otherwise.

    Social surfaces are user namespaces, not company resources: a person
    without a company (or browsing outside it) still reads their own feed,
    posts and connects, governed by RLS and the service visibility rules
    rather than company role keys. When a company IS in context, the
    permission is enforced exactly as `require_permission` enforces it, so
    company controls lose nothing. An invalid or foreign company header
    degrades to the user scope (fewer rights, never more), never to an error
    that would strand a legitimate user.
    """

    async def dependency(
        ctx_and_conn: Annotated[tuple[RequestContext, AsyncConnection], Depends(require_company)],
    ) -> tuple[RequestContext, AsyncConnection]:
        ctx, conn = ctx_and_conn
        if ctx.company_id is None:
            return ctx, conn
        if permission not in ctx.permissions:
            logger.info(
                "permission_denied",
                permission=permission,
                company_id=str(ctx.company_id or ""),
            )
            raise PermissionDeniedError(
                f"This action requires the {permission} permission.",
                request_id=ctx.request_id,
            )
        return ctx, conn

    dependency.__name__ = f"require_user_or_{permission.replace('.', '_')}"
    return dependency


def company_scope(ctx: RequestContext) -> uuid.UUID:
    """The caller's company id, narrowed to a value that cannot be missing.

    `require_company_member` has already rejected a missing context, so reaching
    a handler through a company-scoped dependency means the id is present. This
    states that for the type checker instead of asserting it at every call site.
    """
    if ctx.company_id is None:
        raise CompanyContextError(
            "This endpoint requires a valid X-Company-Public-Id header.",
            request_id=ctx.request_id,
        )
    return ctx.company_id


async def require_platform_admin(
    ctx_and_conn: Annotated[tuple[RequestContext, AsyncConnection], Depends(require_user)],
) -> tuple[RequestContext, AsyncConnection]:
    """Platform administration is never reachable from a company role.

    Company administrators must not be able to escalate into the platform console,
    so this checks an explicit platform grant rather than any company permission.
    """
    ctx, conn = ctx_and_conn
    row = (
        (
            await conn.execute(
                text(
                    """
                SELECT EXISTS (
                    SELECT 1 FROM platform.feature_flag_overrides f
                     WHERE f.scope_type = 'USER'
                       AND f.scope_id = :user_id
                       AND f.flag_key = 'platform.admin_console'
                       AND f.enabled
                ) AS allowed
                """
                ),
                {"user_id": ctx.user_id},
            )
        )
        .mappings()
        .first()
    )

    if not (row and row["allowed"]):
        raise PermissionDeniedError(
            "Platform administration requires a platform grant.",
            request_id=ctx.request_id,
        )
    return ctx, conn


# ------------------------------------------------------------------- resolvers
async def resolve_public_id(
    conn: AsyncConnection,
    table: str,
    public_id: str,
    *,
    column: str = "id",
) -> Any:
    """Resolve a public id to its internal uuid, or raise ResourceNotFoundError.

    `table` is restricted to a module-level allowlist so this helper can never be
    pointed at an arbitrary relation by a caller-supplied value.
    """
    if table not in _RESOLVABLE_TABLES:
        raise ResourceNotFoundError("Unknown resource type.")

    row = (
        (
            await conn.execute(
                text(f"SELECT {column}::text AS id FROM public.{table} WHERE public_id = :pid"),  # noqa: S608 - allowlisted
                {"pid": public_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        raise ResourceNotFoundError()
    return uuid.UUID(str(row["id"]))


_RESOLVABLE_TABLES = frozenset(
    {
        "users",
        "user_profiles",
        "companies",
        "company_roles",
        "projects",
        "project_roles",
        "sows",
        "contracts",
        "timesheets",
        "invoices",
        "payments",
        "documents",
        "msas",
        "leave_requests",
        "ai_actions",
        "ai_automations",
        "ai_extractions",
    }
)
