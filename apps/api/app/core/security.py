"""Supabase Auth JWT verification.

The API never trusts a client-supplied user id. It verifies the access token
against Supabase's signing keys, and derives identity from the verified claims.

Key management:
  * HS256 (shared secret) is supported because Supabase's anon and service keys
    are HMAC-signed. Verification is local: no network round trip per request.
  * RS256 is also supported for projects configured with asymmetric signing keys;
    the JWKS is fetched once and cached with refresh-on-unknown-kid.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.config import Settings, get_settings
from app.core.errors import (
    AccountDisabledError,
    AuthenticationError,
    EmailNotVerifiedError,
    InvalidTokenError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

JWKS_CACHE_TTL_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Identity derived from a verified Supabase Auth token."""

    auth_id: str
    email: str
    email_verified: bool
    session_id: str | None
    provider: str | None
    phone: str | None = None
    is_anonymous: bool = False


class TokenVerifier:
    """Verifies Supabase access tokens locally against cached signing keys."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._jwks_client: PyJWKClient | None = None
        self._jwks_fetched_at: float = 0.0

    # ------------------------------------------------------------------ keys
    def _client(self) -> PyJWKClient | None:
        if self._settings.supabase_url:
            if self._jwks_client is None or (
                time.monotonic() - self._jwks_fetched_at > JWKS_CACHE_TTL_SECONDS
            ):
                url = f"{self._settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
                self._jwks_client = PyJWKClient(url, cache_keys=True)
                self._jwks_fetched_at = time.monotonic()
            return self._jwks_client
        return None

    # ------------------------------------------------------------- verifying
    def verify(self, token: str) -> dict[str, Any]:
        """Return the verified claims, or raise a typed authentication error."""
        if not token:
            raise AuthenticationError("Missing bearer token.")

        if token.count(".") != 2:
            raise InvalidTokenError("Malformed token.")

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("Malformed token header.") from exc

        alg = header.get("alg", "HS256")
        secret = self._settings.supabase_jwt_secret.get_secret_value()

        try:
            if alg.startswith("HS"):
                if not secret:
                    raise AuthenticationError(
                        "Token verification is not configured on this server."
                    )
                claims = jwt.decode(
                    token,
                    secret,
                    algorithms=[alg],
                    audience="authenticated",
                    options={"require": ["sub", "exp", "role"]},
                )
            else:
                client = self._client()
                if client is None:
                    raise AuthenticationError("Asymmetric token verification is not configured.")
                signing_key = client.get_signing_key_from_jwt(token)
                claims = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=[alg],
                    audience="authenticated",
                    options={"require": ["sub", "exp", "role"]},
                )
        except AuthenticationError:
            raise
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Your session has expired.") from exc
        except jwt.PyJWTError as exc:
            logger.warning("token_verification_failed", alg=alg)
            raise InvalidTokenError() from exc

        verified: dict[str, Any] = claims
        return verified

    def authenticate(self, authorization_header: str | None) -> AuthenticatedUser:
        """Authenticate an `Authorization: Bearer <token>` header."""
        if not authorization_header:
            raise AuthenticationError("Missing Authorization header.")

        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("Expected a Bearer token.")

        claims = self.verify(token.strip())
        settings = self._settings

        if settings.require_email_verification and not claims.get("email_verified"):
            raise EmailNotVerifiedError()

        if claims.get("role") in {"service_role", "supabase_admin"}:
            # A service-role token must never be usable as an end-user session.
            logger.error("service_role_token_presented_to_user_endpoint")
            raise AuthenticationError("This token cannot be used to access the API.")

        email = str(claims.get("email") or "")
        if not email and not claims.get("is_anonymous"):
            raise InvalidTokenError("Token carries no email claim.")

        return AuthenticatedUser(
            auth_id=str(claims["sub"]),
            email=email,
            email_verified=bool(claims.get("email_verified")),
            session_id=claims.get("session_id"),
            provider=claims.get("provider"),
            phone=claims.get("phone"),
            is_anonymous=bool(claims.get("is_anonymous", False)),
        )


# --------------------------------------------------------------------- account
async def assert_account_active(
    db: Any, auth_id: str, *, email: str = "", first_name: str = ""
) -> str:
    """Resolve `auth_id` to a platform user id and confirm the account is usable.

    Raises rather than returning a partially-valid identity, so a suspended or
    deactivated account can never reach a business handler.
    """
    from sqlalchemy import text

    row = (
        (
            await db.execute(
                text(
                    """
            SELECT u.id::text, u.status
              FROM public.users u
             WHERE u.auth_id = :auth_id
            """
                ),
                {"auth_id": auth_id},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        # Provisioned on first authenticated request, mirroring auth.users.
        return await provision_user(db, auth_id, email=email, first_name=first_name)

    if row["status"] == "SUSPENDED":
        raise AccountDisabledError("This account has been suspended.")
    if row["status"] == "DEACTIVATED":
        raise AccountDisabledError("This account has been deactivated.")
    if row["status"] == "PENDING_VERIFICATION":
        raise EmailNotVerifiedError()

    return str(row["id"])


async def provision_user(db: Any, auth_id: str, *, email: str = "", first_name: str = "") -> str:
    """Create the platform user row for a first-time authenticated request.

    Runs inside the caller's transaction so a failure leaves nothing behind.
    The `public.users_public_id` trigger allocates the immutable public id.
    """
    from sqlalchemy import text

    from app.core.logging import get_logger as _log

    _log(__name__).info("provisioning_user", auth_id=auth_id)

    row = (
        (
            await db.execute(
                text(
                    """
            INSERT INTO public.users
              (auth_id, email, email_verified_at, first_name, last_name, status)
            VALUES
              (:auth_id, :email, now(), :first_name, '', 'ACTIVE')
            ON CONFLICT (auth_id) DO UPDATE SET auth_id = EXCLUDED.auth_id
            RETURNING id::text
            """
                ),
                {
                    "auth_id": auth_id,
                    "email": email or f"{auth_id}@pending.invalid",
                    "first_name": first_name or "Member",
                },
            )
        )
        .mappings()
        .first()
    )

    user_id = str(row["id"])
    await db.execute(
        text(
            """
            INSERT INTO public.user_profiles (user_id)
            VALUES (CAST(:user_id AS uuid))
            ON CONFLICT DO NOTHING
            """
        ),
        {"user_id": user_id},
    )
    # Default notification preferences, written once.
    await db.execute(
        text(
            """
        INSERT INTO platform.notification_preferences (user_id, category, in_app, email, push)
        SELECT CAST(:user_id AS uuid), c.category, true, (c.category NOT IN ('AI')), true
          FROM unnest(ARRAY['CONTRACT','PROJECT','SOW','MSA','TIMESHEET','INVOICE','PAYMENT',
                             'LEAVE','MESSAGE','CONNECTION','AI','SECURITY','COMPLIANCE','SYSTEM'])
               AS c(category)
        ON CONFLICT DO NOTHING
        """
        ),
        {"user_id": user_id},
    )
    return user_id
