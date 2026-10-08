# MyTrakin — Authorization (RBAC)

Chain: company → membership → role → permission (`app/api/deps.py`,
`services/lookup.py`). Catalogue in `public.permissions`
(`0013_seed_reference.sql`, extended by `0018`); templates in
`public.role_templates`, copied per company by `app.bootstrap_company_roles`.

## Rules that matter

- Membership alone grants nothing: every business endpoint declares one
  permission via `require_permission(...)`, resolved per request through
  `app.my_permissions` + `app.effective_role_keys`.
- Roles are company-scoped; a user holds different roles per company. The
  active company arrives per request (`X-Company-Public-Id`).
- Never expose membership counts; unknown company ids behave as "no context"
  (no oracle for enumeration).
- Segregation of duties is enforced in SQL, not just the API: self-approval
  of timesheets/AI actions is refused; invoice approval requires a different
  author; the last SUPER_ADMIN cannot be removed.
- `require_platform_admin` (platform grant, not company role) gates the admin
  surface; company admins cannot reach it.
- Unknown feature flags default OFF (a typo must never enable a capability).

## Sensitive columns

`user_sensitive` / `tax_id_encrypted` / token ciphertexts are unreadable by the
API role (REVOKE + masked-only grants, `0012`); UIs show last-4 only
(`test_t17` in the RLS suite). Logs and audit payloads are scrubbed
(`core/logging.py`, `services/audit.py`).
