'use client'

import * as React from 'react'
import Link from 'next/link'
import {
  Building2,
  ChevronRight,
  CreditCard,
  FileStack,
  Handshake,
  Landmark,
  Pencil,
  Settings,
  ShieldCheck,
  Users,
} from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate } from '@/lib/utils'
import { statusLabel, statusTone } from '@/lib/status'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  EmptyState,
} from '@/components/ui'
import { CurrencySelect, Field } from '@/components/forms'
import { Input } from '@/components/ui'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Workspace settings.
 *
 * Account details are informational. The company profile and company policy
 * are editable for callers holding companies.update and settings.update; the
 * server re-checks both, and every change is audited with its previous state.
 */
export default function SettingsPage() {
  const { activeCompany, activeCompanyPublicId, loading, error, refresh, me, permissions } =
    useCompany()

  const policies = useCompanyQuery<Record<string, unknown>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['company', 'settings'],
    path: '/companies/current/settings',
    enabled: Boolean(activeCompanyPublicId) && permissions.includes('settings.read'),
  })

  if (error) {
    return (
      <PageShell>
        <ErrorState error={error} onRetry={() => void refresh()} />
      </PageShell>
    )
  }

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Settings' }]}
          title="Settings"
          description="Your account, this company's details, and the modules that hold the rest of the configuration."
        />

        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Users aria-hidden className="size-4 text-primary" />
                Your account
              </CardTitle>
              <CardDescription>
                Contact details and preferences, held on your account rather than on
                any one company.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {loading ? (
                <LoadingBlock rows={5} />
              ) : me ? (
                <dl className="space-y-3">
                  <Row label="Name" value={`${me.first_name} ${me.last_name}`} />
                  <Row label="Email" value={me.email} />
                  <Row
                    label="Email verified"
                    value={
                      <Badge tone={me.email_verified ? 'success' : 'warning'}>
                        {me.email_verified ? 'Verified' : 'Not verified'}
                      </Badge>
                    }
                  />
                  <Row label="User ID" value={<MonoId value={me.public_id} />} />
                  <Row label="Timezone" value={me.timezone} />
                  <Row
                    label="Location"
                    value={
                      [me.location_city, me.location_country].filter(Boolean).join(', ') || 'Not set'
                    }
                  />
                  <Row
                    label="Onboarding"
                    value={
                      <Badge tone={me.onboarding_completed ? 'success' : 'warning'}>
                        {me.onboarding_completed ? 'Complete' : 'Incomplete'}
                      </Badge>
                    }
                  />
                  <Row label="Member since" value={formatDate(me.created_at)} />
                </dl>
              ) : (
                <EmptyState title="No account loaded" description="Sign in to see your details." />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Building2 aria-hidden className="size-4 text-primary" />
                    This company
                  </CardTitle>
                  <CardDescription>
                    The company you are currently working in.
                  </CardDescription>
                </div>
                {permissions.includes('companies.update') && activeCompany ? (
                  <EditCompanyDialog />
                ) : null}
              </div>
            </CardHeader>
            <CardContent>
              {loading ? (
                <LoadingBlock rows={5} />
              ) : activeCompany ? (
                <dl className="space-y-3">
                  <Row label="Display name" value={activeCompany.display_name} />
                  <Row label="Legal name" value={activeCompany.legal_name ?? '—'} />
                  <Row label="Company ID" value={<MonoId value={activeCompany.public_id} />} />
                  <Row label="Country" value={activeCompany.country_code ?? '—'} />
                  <Row
                    label="Default currency"
                    value={formatCurrency(0, activeCompany.default_currency).replace(/[\d.,]/g, '').trim() || activeCompany.default_currency}
                  />
                  <Row
                    label="Verification"
                    value={
                      <Badge tone={statusTone(activeCompany.verification_state)}>
                        {statusLabel(activeCompany.verification_state)}
                      </Badge>
                    }
                  />
                  <Row label="Status" value={statusLabel(activeCompany.status)} />
                  <Row
                    label="Your roles"
                    value={
                      activeCompany.my_role_keys.length > 0
                        ? activeCompany.my_role_keys.join(', ')
                        : 'None'
                    }
                  />
                  <Row label="Permissions" value={`${activeCompany.my_permissions.length}`} />
                </dl>
              ) : (
                <EmptyState title="No active company" description="Pick a company first." />
              )}
            </CardContent>
          </Card>
        </div>

        {permissions.includes('settings.read') ? (
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Settings aria-hidden className="size-4 text-primary" />
                    Company policy
                  </CardTitle>
                  <CardDescription>
                    Stored by the server and merged on save. Every change keeps
                    its previous state in the settings history.
                  </CardDescription>
                </div>
                {permissions.includes('settings.update') ? (
                  <EditPolicyDialog
                    current={(policies.data ?? {}) as Record<string, unknown>}
                    onSaved={() => void policies.refetch()}
                  />
                ) : null}
              </div>
            </CardHeader>
            <CardContent>
              {policies.isPending ? (
                <LoadingBlock rows={4} />
              ) : policies.isError ? (
                <ErrorState error={policies.error} onRetry={() => void policies.refetch()} />
              ) : (
                <SettingsList value={policies.data ?? {}} />
              )}
            </CardContent>
          </Card>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>Configuration that lives in its own module</CardTitle>
            <CardDescription>
              These screens own their own settings rather than duplicating them here.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y divide-border/60">
              <SettingsLink
                href="/settings/permissions"
                icon={<ShieldCheck aria-hidden />}
                title="Roles and permissions"
                description="What each company role can do"
              />
              <SettingsLink
                href="/companies"
                icon={<Building2 aria-hidden />}
                title="Companies"
                description="Switch, create or archive a company"
              />
              <SettingsLink
                href="/msas"
                icon={<Handshake aria-hidden />}
                title="Master service agreements"
                description="Governing law, renewal terms and versions"
              />
              <SettingsLink
                href="/payments/accounts"
                icon={<Landmark aria-hidden />}
                title="Bank accounts"
                description="Connections, balances and verification"
              />
              <SettingsLink
                href="/invoices"
                icon={<CreditCard aria-hidden />}
                title="Invoices"
                description="Billing periods, terms and approvals"
              />
              <SettingsLink
                href="/documents"
                icon={<FileStack aria-hidden />}
                title="Documents"
                description="Retention, legal holds and access log"
              />
            </ul>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right text-sm font-medium">{value}</dd>
    </div>
  )
}

function MonoId({ value }: { value: string }) {
  return <span className="font-mono text-xs">{value}</span>
}

function SettingsLink({
  href,
  icon,
  title,
  description,
}: {
  href: string
  icon: React.ReactNode
  title: string
  description: string
}) {
  return (
    <li>
      <Link
        href={href}
        className="flex items-center gap-3 py-3 transition-colors hover:bg-primary-soft/40"
      >
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary-soft text-primary-strong [&_svg]:size-4">
          {icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium">{title}</span>
          <span className="block truncate text-xs text-muted-foreground">{description}</span>
        </span>
        <ChevronRight aria-hidden className="size-4 shrink-0 text-subtle-foreground" />
      </Link>
    </li>
  )
}

/**
 * Policy settings arrive as a free-form object, so they are read defensively:
 * each entry is rendered as either a boolean, a number or a string, and anything
 * nested is rendered as JSON rather than being asserted into a shape the server
 * never promised.
 */
function SettingsList({ value }: { value: Record<string, unknown> }) {
  const entries = Object.entries(value)

  if (entries.length === 0) {
    return (
      <EmptyState
        title="No policy overrides"
        description="This company has not customised any platform policy. The defaults apply."
      />
    )
  }

  return (
    <ul className="divide-y divide-border/60">
      {entries
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, raw]) => (
          <li key={key} className="flex items-center justify-between gap-4 py-2.5">
            <span className="font-mono text-2xs text-muted-foreground">{key}</span>
            <span className="min-w-0 truncate text-right text-sm font-medium">
              {formatPolicyValue(raw)}
            </span>
          </li>
        ))}
    </ul>
  )
}

function formatPolicyValue(raw: unknown): string {
  if (raw === null || raw === undefined) return '—'
  if (typeof raw === 'boolean') return raw ? 'Enabled' : 'Disabled'
  if (typeof raw === 'number') return String(raw)
  if (typeof raw === 'string') return raw
  return JSON.stringify(raw)
}

/* -------------------------------------------------------------------------- */
/* Company profile editing                                                    */
/* -------------------------------------------------------------------------- */

/**
 * Edit the company profile. The field list mirrors the server allowlist in
 * `update_company`: tax identifiers and status are never editable here, and
 * anything else sent is silently dropped server-side rather than trusted.
 */
function EditCompanyDialog() {
  const { activeCompanyPublicId, activeCompany, refresh } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [values, setValues] = React.useState({
    legal_name: '',
    display_name: '',
    dba: '',
    country_code: '',
    address_line1: '',
    address_line2: '',
    city: '',
    region: '',
    postal_code: '',
    default_currency: '',
  })
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open && activeCompany) {
      setValues({
        legal_name: activeCompany.legal_name ?? '',
        display_name: activeCompany.display_name ?? '',
        dba: activeCompany.dba ?? '',
        country_code: activeCompany.country_code ?? '',
        address_line1: activeCompany.address_line1 ?? '',
        address_line2: activeCompany.address_line2 ?? '',
        city: activeCompany.city ?? '',
        region: activeCompany.region ?? '',
        postal_code: activeCompany.postal_code ?? '',
        default_currency: activeCompany.default_currency ?? '',
      })
      setError(null)
    }
  }, [open, activeCompany])

  const save = useCompanyMutation<unknown, Record<string, string>>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.patch('/companies/current', body, { companyPublicId: activeCompanyPublicId }),
    invalidate: [],
    onSuccess: () => {
      notifySuccess('Company updated.', 'The new profile is live.')
      setOpen(false)
      void refresh()
    },
  })

  const set = (key: keyof typeof values, value: string) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (values.display_name.trim().length < 2) {
      setError('The display name needs at least two characters.')
      return
    }
    if (values.legal_name.trim().length < 2) {
      setError('The legal name needs at least two characters.')
      return
    }
    setError(null)
    const body: Record<string, string> = {
      legal_name: values.legal_name.trim(),
      display_name: values.display_name.trim(),
      country_code: values.country_code.trim().toUpperCase(),
      default_currency: values.default_currency.trim().toUpperCase(),
    }
    for (const key of ['dba', 'address_line1', 'address_line2', 'city', 'region', 'postal_code'] as const) {
      const trimmed = values[key].trim()
      if (trimmed) body[key] = trimmed
    }
    try {
      await save.mutateAsync(body)
    } catch (cause) {
      notifyError(cause, 'The company could not be updated.')
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Pencil aria-hidden />
        Edit
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Edit company profile"
        description="Tax identifiers, verification state and status are managed by the platform, never here."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={save.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="edit-company" loading={save.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        <form id="edit-company" onSubmit={submit} className="space-y-4">
          {error ? (
            <p role="alert" className="text-sm text-danger">
              {error}
            </p>
          ) : null}
          <Field label="Legal name" required>
            <Input
              id="edit-legal-name"
              value={values.legal_name}
              onChange={(event) => set('legal_name', event.target.value)}
            />
          </Field>
          <Field label="Display name" required>
            <Input
              id="edit-display-name"
              value={values.display_name}
              onChange={(event) => set('display_name', event.target.value)}
            />
          </Field>
          <Field label="DBA" hint="Doing business as, if different">
            <Input id="edit-dba" value={values.dba} onChange={(event) => set('dba', event.target.value)} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Country code" hint="Two-letter ISO code">
              <Input
                id="edit-country"
                value={values.country_code}
                maxLength={2}
                onChange={(event) => set('country_code', event.target.value.toUpperCase())}
                className="font-mono"
              />
            </Field>
            <Field label="Default currency">
              <CurrencySelect
                id="edit-currency"
                value={values.default_currency}
                onChange={(value) => set('default_currency', value)}
              />
            </Field>
            <Field label="Address line 1">
              <Input
                id="edit-address1"
                value={values.address_line1}
                onChange={(event) => set('address_line1', event.target.value)}
              />
            </Field>
            <Field label="Address line 2">
              <Input
                id="edit-address2"
                value={values.address_line2}
                onChange={(event) => set('address_line2', event.target.value)}
              />
            </Field>
            <Field label="City">
              <Input id="edit-city" value={values.city} onChange={(event) => set('city', event.target.value)} />
            </Field>
            <Field label="Region or state">
              <Input
                id="edit-region"
                value={values.region}
                onChange={(event) => set('region', event.target.value)}
              />
            </Field>
            <Field label="Postal code">
              <Input
                id="edit-postal"
                value={values.postal_code}
                onChange={(event) => set('postal_code', event.target.value)}
              />
            </Field>
          </div>
        </form>
      </Dialog>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Company policy editing                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Edit company policy as JSON. Policy is a free-form object server-side, so
 * the editor is a validated JSON document rather than a fixed form that would
 * drift from the keys the server actually honours. Save merges: keys absent
 * from the document keep their current values.
 */
function EditPolicyDialog({
  current,
  onSaved,
}: {
  current: Record<string, unknown>
  onSaved: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [text, setText] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open) {
      setText(JSON.stringify(current, null, 2))
      setError(null)
    }
  }, [open, current])

  const save = useCompanyMutation<unknown, Record<string, unknown>>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.put('/companies/current/settings', body, { companyPublicId: activeCompanyPublicId }),
    invalidate: [['company', 'settings']],
    onSuccess: () => {
      notifySuccess('Policy updated.', 'The previous values are kept in the settings history.')
      setOpen(false)
      onSaved()
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      setError('That is not valid JSON. Fix the syntax and try again.')
      return
    }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setError('Policy must be a JSON object, not an array or a bare value.')
      return
    }
    setError(null)
    try {
      await save.mutateAsync(parsed as Record<string, unknown>)
    } catch (cause) {
      notifyError(cause, 'The policy could not be saved.')
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Pencil aria-hidden />
        Edit
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Edit company policy"
        description="Keys you omit keep their current values. Financial and approval thresholds take effect immediately."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={save.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="edit-policy" loading={save.isPending}>
              Save policy
            </Button>
          </>
        }
      >
        <form id="edit-policy" onSubmit={submit} className="space-y-4">
          <Field label="Policy JSON" error={error ?? undefined} required>
            <textarea
              id="edit-policy-json"
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={12}
              spellCheck={false}
              aria-invalid={Boolean(error)}
              className="w-full rounded-md border border-border bg-surface p-3 font-mono text-xs leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/40"
            />
          </Field>
        </form>
      </Dialog>
    </>
  )
}