'use client'

import Link from 'next/link'
import {
  Building2,
  ChevronRight,
  CreditCard,
  FileStack,
  Handshake,
  Landmark,
  Settings,
  ShieldCheck,
  Users,
} from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import { formatCurrency, formatDate } from '@/lib/utils'
import { statusLabel, statusTone } from '@/lib/status'
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
} from '@/components/ui'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'

/**
 * Workspace settings.
 *
 * Company policy is the only settings surface the API exposes in this build, so
 * this screen shows it read-only and links to the modules that do accept changes.
 * It does not pretend to save anything.
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
              <CardTitle className="flex items-center gap-2">
                <Building2 aria-hidden className="size-4 text-primary" />
                This company
              </CardTitle>
              <CardDescription>
                The company you are currently working in.
              </CardDescription>
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
              <CardTitle className="flex items-center gap-2">
                <Settings aria-hidden className="size-4 text-primary" />
                Company policy
              </CardTitle>
              <CardDescription>
                Stored by the server and read here. Policy changes are made through
                the API in this build, so nothing on this screen is editable.
              </CardDescription>
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