# MyTrakin — Security

Threat model: cross-tenant access (IDOR/BOLA), privilege escalation, financial
manipulation, PII/TIN leakage, AI data leakage, forged webhooks, XSS.

## Controls

- AuthN: Supabase Auth (passwords/OAuth/MFA/sessions); service-role key is
  server-only, rejected as a user token, never in `NEXT_PUBLIC_*`.
- AuthZ: RBAC per request + RLS using the same predicate (defense in depth).
- Tenant isolation: company_id on every tenant row; unknown ids are "no
  context"; membership lists never exposed.
- Money: server-side decimals, derived totals, idempotency, append-only
  ledgers, `ck_payment_ref`, segregation of duties.
- Secrets: `.env` (gitignored) vs `.env.example` (placeholders); CI secret
  scan (`security.yml`); logs/audit scrubbed; TIN last-4 only.
- Browser: strict CSP (no inline-script placeholders), `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `frame-ancestors 'none'`, explicit CORS origins,
  `Cache-Control: no-store` on `/api/*`.
- Uploads: extension/MIME/size/magic-byte validation, checksums, ClamAV scan
  when configured (else PENDING, never fake-clean), INFECTED quarantined and
  unservable, signed URLs with 300s TTL.
- Webhooks: HMAC verified before storage; idempotent persist + outbox fan-out.
- AI: permission-first retrieval, approval gates, capability tokens, prompt
  versioning, anomaly language without accusations.

## Reporting

Do not open a public issue for a vulnerability. Contact the maintainers
privately with: affected endpoint/version, reproduction steps, and impact.
Rotate any exposed credential before reporting.
