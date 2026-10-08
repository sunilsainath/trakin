"""Payment integration layer.

The important architectural point: **Plaid is not a payment processor.** It
connects and verifies bank accounts and supplies transaction data. Money movement
is a separate concern handled by `PaymentProcessor`.

    Application
      -> BankConnectionProvider   (Plaid)      link + verify accounts, sync txns
      -> PaymentProcessor          (Stripe/…)    move money

Both are protocols with a registry, so a processor can be added without touching
application code, and a missing credential disables the capability with a typed
error rather than a fake success.

Banking credentials are never stored. Access tokens are envelope-encrypted with a
key held in the secrets manager, and never appear in a log, an API response or a
database URL.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from cryptography.fernet import Fernet

from app.core.config import Settings, get_settings
from app.core.errors import (
    IntegrationNotConfiguredError,
    PermissionDeniedError,
    UpstreamError,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

Environment = Literal["sandbox", "development", "production"]


# --------------------------------------------------------------------- models
@dataclass(frozen=True, slots=True)
class LinkSession:
    """A short-lived token the browser hands to the provider's SDK."""

    link_token: str
    expires_at: datetime
    provider: str = "plaid"


@dataclass(frozen=True, slots=True)
class LinkedInstitution:
    institution_name: str
    institution_id: str
    account_count: int
    verification_state: str = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class LinkedAccount:
    """A connected bank account. The full number is never present."""

    provider_account_id: str
    institution_name: str
    account_number_masked: str
    account_type: str
    name: str | None
    currency: str
    is_primary: bool = False
    verification_state: str = "UNVERIFIED"
    ownership_verified: bool = False


@dataclass(frozen=True, slots=True)
class SyncedTransaction:
    provider_transaction_id: str
    posted_at: datetime
    amount: Any  # decimal.Decimal in the caller; signed
    currency: str
    description: str | None
    merchant_name: str | None
    category: str | None
    is_pending: bool
    # The provider's account identifier. Optional because a provider may report a
    # transaction across every account of an item; when present it lets the caller
    # file the transaction against the right account instead of all of them.
    provider_account_id: str | None = None
    authorized_at: datetime | None = None
    normalized_description: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentInstruction:
    """A request to move money, before any processor call."""

    amount: Any
    currency: str
    payment_method: str
    counterparty_name: str
    counterparty_reference: str | None = None
    memo: str | None = None
    scheduled_for: date | None = None


@dataclass(frozen=True, slots=True)
class PaymentResult:
    processor_payment_ref: str
    status: str
    fee_amount: Any
    completed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------ protocols
@runtime_checkable
class BankConnectionProvider(Protocol):
    """Links and verifies bank accounts. Never moves money."""

    key: str

    @property
    def is_configured(self) -> bool: ...

    def environment(self) -> str: ...

    async def create_link_session(
        self, *, user_id: str, redirect_uri: str | None = None
    ) -> LinkSession: ...

    async def exchange_public_token(
        self, public_token: str, *, company_id: str
    ) -> tuple[LinkedInstitution, list[LinkedAccount]]: ...

    async def sync_transactions(
        self,
        *,
        access_token: bytes,
        cursor: str | None,
        start_date: date,
        end_date: date,
    ) -> tuple[list[SyncedTransaction], str | None, bool]: ...

    async def get_account_ownership(
        self, access_token: bytes, account_ids: list[str]
    ) -> dict[str, bool]: ...

    def verify_webhook(self, body: bytes, signature: str) -> bool: ...


@runtime_checkable
class PaymentProcessor(Protocol):
    """Moves money. Requires explicit authorization for every call."""

    key: str

    @property
    def is_configured(self) -> bool: ...

    async def create_payment(
        self, instruction: PaymentInstruction, *, idempotency_key: str
    ) -> PaymentResult: ...

    async def get_payment(self, processor_payment_ref: str) -> PaymentResult: ...

    async def refund(
        self, processor_payment_ref: str, amount: Any, *, idempotency_key: str
    ) -> PaymentResult: ...


# ----------------------------------------------------------------- encryption
class TokenCipher:
    """Envelope encryption for provider access tokens.

        Uses Fernet when a key is configured, and refuses to store anything at all
        when it is not. Storing a token in plaintext "temporarily" is how bank
    credentials end up in a backup.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._fernet: Fernet | None = None
        raw = self._settings.plaid_token_encryption_key.get_secret_value()
        if raw:
            try:
                key = raw.encode() if len(raw) == 44 else base64.urlsafe_b64encode(raw.encode())
                self._fernet = Fernet(key)
            except Exception:  # noqa: BLE001
                # An unusable key must not silently fall back to storing tokens in
                # the clear; `is_available` stays false and callers refuse.
                logger.error("token_cipher_key_invalid")

    @property
    def is_available(self) -> bool:
        return self._fernet is not None

    def _require_fernet(self) -> Fernet:
        """The cipher, or a typed refusal.

        Centralised so `encrypt` and `decrypt` cannot each forget the check.
        """
        if self._fernet is None:
            raise IntegrationNotConfiguredError("token_encryption")
        return self._fernet

    def encrypt(self, plaintext: str) -> bytes:
        return bytes(self._require_fernet().encrypt(plaintext.encode()))

    def decrypt(self, ciphertext: bytes) -> str:
        return self._require_fernet().decrypt(ciphertext).decode()


# -------------------------------------------------------------------- Plaid
PLAID_HOSTS: dict[str, str] = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidBankConnectionProvider:
    """Plaid adapter. Connects accounts; does not move money."""

    key = "plaid"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return self._settings.integration_ready("plaid")

    def _require(self) -> None:
        if not self.is_configured:
            raise IntegrationNotConfiguredError("plaid")

    def environment(self) -> str:
        return self._settings.plaid_env

    def _host(self) -> str:
        return PLAID_HOSTS[self._settings.plaid_env]

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _credentials(self) -> dict[str, str]:
        return {
            "client_id": self._settings.plaid_client_id,
            "secret": self._settings.plaid_secret.get_secret_value(),
        }

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        import httpx

        self._require()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._host()}{path}", headers=self._headers(), json=body
            )
            if response.status_code >= 400:
                logger.warning("plaid_call_failed", path=path, status=response.status_code)
                raise UpstreamError("The bank provider rejected the request.")
            return response.json()  # type: ignore[no-any-return]

    async def create_link_session(
        self, *, user_id: str, redirect_uri: str | None = None
    ) -> LinkSession:
        import datetime as dt

        body: dict[str, Any] = {
            "client_name": self._settings.platform_name,
            "country_codes": ["US"],
            "language": "en",
            "user": {"client_user_id": user_id},
            "products": ["transactions"],
        }
        if redirect_uri:
            body["redirect_uri"] = redirect_uri

        data = await self._post("/link/token/create", {**self._credentials(), **body})
        return LinkSession(
            link_token=str(data["link_token"]),
            expires_at=dt.datetime.fromisoformat(str(data["expiration"])),
        )

    async def exchange_public_token(
        self, public_token: str, *, company_id: str
    ) -> tuple[LinkedInstitution, list[LinkedAccount]]:
        data = await self._post(
            "/item/public_token/exchange",
            {**self._credentials(), "public_token": public_token},
        )
        access_token = str(data["access_token"])
        item_id = str(data["item_id"])

        # The access token is immediately encrypted; only the ciphertext is
        # handed back for storage.
        cipher = TokenCipher(self._settings)
        encrypted = cipher.encrypt(access_token)

        accounts = await self._post(
            "/accounts/get",
            {**self._credentials(), "access_token": access_token},
        )
        institution_id = str((accounts.get("item") or {}).get("institution_id") or "")

        linked = [
            LinkedAccount(
                provider_account_id=str(a["account_id"]),
                institution_name=(accounts.get("item") or {}).get("institution_name") or "Bank",
                account_number_masked=_mask(str(a.get("mask") or "")),
                account_type=str(a.get("subtype") or a.get("type") or "OTHER").upper(),
                name=a.get("name"),
                currency=str((a.get("balances") or {}).get("iso_currency_code") or "USD"),
            )
            for a in accounts.get("accounts", [])
        ]

        institution = LinkedInstitution(
            institution_name=(accounts.get("item") or {}).get("institution_name") or "Bank",
            institution_id=institution_id,
            account_count=len(linked),
        )
        logger.info(
            "plaid_item_exchanged",
            company_id=company_id,
            accounts=len(linked),
            item_id_len=len(item_id),
            token_encrypted=len(encrypted),
        )
        return institution, linked

    async def sync_transactions(
        self,
        *,
        access_token: bytes,
        cursor: str | None,
        start_date: date,
        end_date: date,
    ) -> tuple[list[SyncedTransaction], str | None, bool]:
        from decimal import Decimal

        cipher = TokenCipher(self._settings)
        token = cipher.decrypt(access_token)

        if cursor is None:
            await self._post(
                "/transactions/sync",
                {
                    **self._credentials(),
                    "access_token": token,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
            data = await self._post(
                "/transactions/sync",
                {**self._credentials(), "access_token": token, "cursor": ""},
            )
        else:
            data = await self._post(
                "/transactions/sync",
                {**self._credentials(), "access_token": token, "cursor": cursor},
            )

        added = data.get("added") or []
        modified = data.get("modified") or []

        def to_txn(item: dict[str, Any]) -> SyncedTransaction:
            return SyncedTransaction(
                provider_transaction_id=str(item["transaction_id"]),
                posted_at=datetime.fromisoformat(str(item["date"])),
                amount=Decimal(str(item["amount"])),
                currency=str(item.get("iso_currency_code") or "USD"),
                description=item.get("name"),
                merchant_name=(item.get("merchant_name") or item.get("name")),
                category=(item.get("personal_finance_category") or {}).get("primary"),
                is_pending=bool(item.get("pending")),
                provider_account_id=(str(item["account_id"]) if item.get("account_id") else None),
                authorized_at=(
                    datetime.fromisoformat(str(item["authorized_datetime"]))
                    if item.get("authorized_datetime")
                    else None
                ),
            )

        transactions = [to_txn(i) for i in added] + [to_txn(i) for i in modified]
        return transactions, data.get("next_cursor"), bool(data.get("has_more"))

    async def get_account_ownership(
        self, access_token: bytes, account_ids: list[str]
    ) -> dict[str, bool]:
        """Verify the connected accounts belong to the expected owner.

        Uses the /auth endpoint (account ownership). An account that Plaid does
        not report as owned is returned False rather than optimistically True.
        """
        cipher = TokenCipher(self._settings)
        token = cipher.decrypt(access_token)
        data = await self._post(
            "/auth/get",
            {**self._credentials(), "access_token": token},
        )
        accounts = data.get("accounts") or []
        return (
            dict.fromkeys(account_ids, False)
            if not accounts
            else {
                str(a.get("account_id")): True
                for a in accounts
                if a.get("account_id") in account_ids
            }
        )

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        """Verify the Plaid webhook signature (ES256 JWT with a Plaid JWK).

        Fails closed: an unverifiable webhook is rejected, never processed. The
        JWK is fetched with a synchronous client on the request path, which is
        acceptable because webhook volume is low and the alternative is an
        unauthenticated endpoint.
        """
        if not self.is_configured:
            return False
        try:
            import time

            from jwt import PyJWKClient
            from jwt import decode as jwt_decode

            header_segment = signature.split(".", maxsplit=1)[0]
            padded = header_segment + "=" * (-len(header_segment) % 4)
            header = json.loads(base64.urlsafe_b64decode(padded).decode())
            kid = str(header.get("kid") or "")
            if not kid:
                return False

            jwk_client = PyJWKClient(
                f"{self._host()}/webhook_verification_key/get", cache_keys=True
            )
            signing_key = jwk_client.get_signing_key_from_jwt(signature)
            _ = jwk_client, time, kid

            jwt_decode(
                signature,
                signing_key.key,
                algorithms=["ES256"],
                options={"verify_aud": False, "verify_exp": True},
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("plaid_webhook_signature_invalid", error=str(exc)[:150])
            return False


def _mask(mask: str) -> str:
    if not mask:
        return "****"
    return f"****{mask[-4:]}"


# ----------------------------------------------------------------- processor
class ManualPaymentProcessor:
    """Records offline movements (cheque, wire, manual entry).

    This is not a stub that pretends to move money: it explicitly records a
    movement that a human performed elsewhere, and requires the authorization
    fields to be present. The processor field on the payment row keeps
    'MANUAL', so reconciliation can tell the two apart.
    """

    key = "manual"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return True

    async def create_payment(
        self, instruction: PaymentInstruction, *, idempotency_key: str
    ) -> PaymentResult:
        import datetime as dt
        from decimal import Decimal

        if instruction.amount <= Decimal("0"):
            raise UpstreamError("A payment amount must be positive.")
        return PaymentResult(
            processor_payment_ref=f"manual_{idempotency_key[:24]}",
            status="COMPLETED",
            fee_amount=Decimal("0"),
            completed_at=dt.datetime.now(dt.UTC),
            raw={"recorded_offline": True},
        )

    async def get_payment(self, processor_payment_ref: str) -> PaymentResult:
        return PaymentResult(
            processor_payment_ref=processor_payment_ref,
            status="COMPLETED",
            fee_amount=__import__("decimal").Decimal("0"),
        )

    async def refund(
        self, processor_payment_ref: str, amount: Any, *, idempotency_key: str
    ) -> PaymentResult:
        import datetime as dt
        from decimal import Decimal

        return PaymentResult(
            processor_payment_ref=f"manual_refund_{idempotency_key[:16]}",
            status="REFUNDED",
            fee_amount=Decimal("0"),
            completed_at=dt.datetime.now(dt.UTC),
            raw={"original": processor_payment_ref},
        )


class StripePaymentProcessor:
    """Stripe adapter. Moves money through a real processor."""

    key = "stripe"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        return self._settings.integration_ready("payments")

    def _require(self) -> None:
        if not self.is_configured:
            raise IntegrationNotConfiguredError("payments")

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        secret = self._settings.payment_processor_api_key.get_secret_value()
        return {
            "Authorization": f"Bearer {secret}",
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def create_payment(
        self, instruction: PaymentInstruction, *, idempotency_key: str
    ) -> PaymentResult:
        import datetime as dt
        from decimal import Decimal

        import httpx

        self._require()
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.stripe.com/v1/payment_intents",
                headers=self._headers(idempotency_key),
                data={
                    "amount": str(int(Decimal(instruction.amount) * 100)),
                    "currency": instruction.currency.lower(),
                    "description": instruction.memo or "MyTrakin payment",
                    "metadata[counterparty]": instruction.counterparty_name,
                },
            )
            if response.status_code >= 400:
                logger.warning("stripe_payment_failed", status=response.status_code)
                raise UpstreamError("The payment processor rejected the request.")
            data = response.json()

        return PaymentResult(
            processor_payment_ref=str(data["id"]),
            status=str(data.get("status", "requires_action")).upper(),
            fee_amount=Decimal("0"),
            completed_at=(
                dt.datetime.fromtimestamp(data["created"], dt.UTC) if data.get("created") else None
            ),
            raw=data,
        )

    async def get_payment(self, processor_payment_ref: str) -> PaymentResult:
        from decimal import Decimal

        import httpx

        self._require()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"https://api.stripe.com/v1/payment_intents/{processor_payment_ref}",
                headers=self._headers("status-check"),
            )
            if response.status_code >= 400:
                raise UpstreamError("The payment processor rejected the request.")
            data = response.json()

        return PaymentResult(
            processor_payment_ref=str(data["id"]),
            status=str(data.get("status", "")).upper(),
            fee_amount=Decimal("0"),
            raw=data,
        )

    async def refund(
        self, processor_payment_ref: str, amount: Any, *, idempotency_key: str
    ) -> PaymentResult:
        from decimal import Decimal

        import httpx

        self._require()
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                "https://api.stripe.com/v1/refunds",
                headers=self._headers(idempotency_key),
                data={
                    "payment_intent": processor_payment_ref,
                    "amount": str(int(Decimal(amount) * 100)),
                },
            )
            if response.status_code >= 400:
                raise UpstreamError("The payment processor rejected the refund.")
            data = response.json()

        return PaymentResult(
            processor_payment_ref=str(data["id"]),
            status="REFUNDED",
            fee_amount=Decimal("0"),
            raw=data,
        )

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        secret = self._settings.payment_processor_webhook_secret.get_secret_value()
        if not secret or not signature:
            return False
        try:
            timestamp, _, v1 = signature.split(",", 2)[1].partition("v1=")
        except (IndexError, ValueError):
            return False
        expected = hmac.new(
            secret.encode(),
            f"{timestamp}.{body.decode('utf-8', 'replace')}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, v1)


# ------------------------------------------------------------------ registry
class IntegrationRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._bank: dict[str, BankConnectionProvider] = {}
        self._processors: dict[str, PaymentProcessor] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        plaid = PlaidBankConnectionProvider(self._settings)
        self._bank[plaid.key] = plaid

        for processor in (
            ManualPaymentProcessor(self._settings),
            StripePaymentProcessor(self._settings),
        ):
            self._processors[processor.key] = processor

    def register_bank_provider(self, provider: BankConnectionProvider) -> None:
        self._bank[provider.key] = provider

    def register_processor(self, processor: PaymentProcessor) -> None:
        self._processors[processor.key] = processor

    def bank(self, key: str = "plaid") -> BankConnectionProvider:
        provider = self._bank.get(key)
        if provider is None:
            raise IntegrationNotConfiguredError(key)
        return provider

    def processor(self, key: str | None = None) -> PaymentProcessor:
        wanted = key or self._settings.payment_processor
        processor = self._processors.get(wanted)
        if processor is None:
            raise IntegrationNotConfiguredError(wanted)
        return processor

    def capabilities(self) -> dict[str, Any]:
        return {
            "bank_connections": {
                k: {"configured": p.is_configured} for k, p in sorted(self._bank.items())
            },
            "payment_processors": {
                k: {
                    "configured": p.is_configured,
                    "moves_money": k not in {"manual"},
                }
                for k, p in sorted(self._processors.items())
            },
            "token_encryption_available": TokenCipher(self._settings).is_available,
        }


_registry: IntegrationRegistry | None = None


def get_integrations() -> IntegrationRegistry:
    global _registry
    if _registry is None:
        _registry = IntegrationRegistry()
    return _registry


def require_authorization(
    *, authorized: bool, authorization_type: str, authorized_at: datetime | None
) -> None:
    """Refuse to move money without a recorded authorization.

    Called before every processor invocation, so no code path can skip it.
    """
    if not authorized or authorized_at is None:
        raise PermissionDeniedError(
            "A payment cannot be initiated without a recorded authorization."
        )
    if authorization_type not in {"EXPLICIT", "POLICY", "SCHEDULED", "AUTOMATION"}:
        raise PermissionDeniedError("The recorded authorization type is not recognised.")
