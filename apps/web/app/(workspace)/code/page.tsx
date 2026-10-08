'use client'

import * as React from 'react'
import Link from 'next/link'

import { useCompany } from '@/hooks/use-company'
import { formatCurrency } from '@/lib/utils'
import type { Page as PageEnvelope } from '@/lib/types'
import type { ReceivablesPanel } from '@/lib/domain-types'
import { Card, CardContent, CardHeader, CardTitle, EmptyState, Skeleton } from '@/components/ui'
import { PageHeader, PageShell } from '@/components/page'
import { useCompanyQuery } from '@/components/query'

/**
 * CODE overview: Projects → SOW → Contracts → Invoices at a glance.
 *
 * Every card reads a real endpoint with `limit=1` and reports `meta.total`,
 * so the numbers always agree with the lists they link to. A card renders
 * only when the caller holds the matching read permission; anything else is
 * omitted rather than zeroed, so an absent card means "unreadable", never
 * "nothing there".
 */
export default function CodeOverviewPage() {
  const { activeCompanyPublicId } = useCompany()

  if (!activeCompanyPublicId) {
    return (
      <PageShell>
        <EmptyState
          title="No company selected"
          description="CODE lives inside a company. Create or join one to see its pipeline."
          action={
            <Link
              href="/onboarding"
              className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
            >
              Create a company
            </Link>
          }
        />
      </PageShell>
    )
  }

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'CODE' }]}
          title="CODE overview"
          description="Projects flow into SOWs, SOWs into contracts, contracts into invoices. Every number links to the list it was counted from."
        />

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <CountCard label="Active Projects" href="/projects?status=ACTIVE" path="/projects" query="?status=ACTIVE" permission="projects.read" />
          <CountCard label="Draft Projects" href="/projects?status=DRAFT" path="/projects" query="?status=DRAFT" permission="projects.read" />
          <CountCard label="Active SOWs" href="/sows?status=ACTIVE" path="/sows" query="?status=ACTIVE" permission="sows.read" />
          <CountCard label="Pending SOWs" href="/sows?status=PENDING_APPROVAL" path="/sows" query="?status=PENDING_APPROVAL" permission="sows.read" />
          <CountCard label="Active Contracts" href="/contracts?status=ACTIVE" path="/contracts" query="?status=ACTIVE" permission="contracts.read" />
          <CountCard label="Pending Acceptance" href="/contracts?status=PENDING_ACCEPTANCE" path="/contracts" query="?status=PENDING_ACCEPTANCE" permission="contracts.read" />
          <CountCard label="Draft Contracts" href="/contracts?status=DRAFT" path="/contracts" query="?status=DRAFT" permission="contracts.read" />
          <CountCard label="Expiring ≤ 30 days" href="/contracts?expiring_within_days=30" path="/contracts" query="?expiring_within_days=30" permission="contracts.read" />
          <CountCard label="Draft Invoices" href="/invoices?status=DRAFT" path="/invoices" query="?status=DRAFT" permission="invoices.read" />
          <CountCard label="Pending Invoices" href="/invoices?status=PENDING" path="/invoices" query="?status=PENDING" permission="invoices.read" />
          <CountCard label="Paid Invoices" href="/invoices?status=PAID" path="/invoices" query="?status=PAID" permission="invoices.read" />
          <CountCard label="Rejected Invoices" href="/invoices?status=REJECTED" path="/invoices" query="?status=REJECTED" permission="invoices.read" />
        </div>

        <ReceivablesCard />
        <ExpiringCard />
      </div>
    </PageShell>
  )
}

function CountCard({
  label,
  href,
  path,
  query,
  permission,
}: {
  label: string
  href: string
  path: string
  query: string
  permission: string
}) {
  const { activeCompanyPublicId, can } = useCompany()
  const result = useCompanyQuery<PageEnvelope<unknown>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['code-overview', path, query],
    path,
    queryParams: `${query}&limit=1`,
    enabled: can(permission),
  })

  if (!can(permission)) return null

  return (
    <Link href={href}>
      <Card className="transition-colors hover:border-primary/40">
        <CardContent className="pt-5">
          <p className="text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
            {label}
          </p>
          {result.isPending ? (
            <Skeleton className="mt-2 h-8 w-16" />
          ) : (
            <p className="mt-1 text-2xl font-semibold">{result.data?.meta.total ?? '—'}</p>
          )}
        </CardContent>
      </Card>
    </Link>
  )
}

function ReceivablesCard() {
  const { activeCompanyPublicId, activeCompany, can } = useCompany()
  const receivables = useCompanyQuery<ReceivablesPanel>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['code-overview', 'receivables'],
    path: '/billing/receivables',
    enabled: can('invoices.read'),
  })

  if (!can('invoices.read')) return null
  const currency = activeCompany?.default_currency ?? 'USD'

  return (
    <Card>
      <CardHeader>
        <CardTitle>Receivables</CardTitle>
      </CardHeader>
      <CardContent>
        {receivables.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : receivables.isError || !receivables.data ? (
          <p className="text-sm text-muted-foreground">Unavailable.</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <p className="text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                Outstanding
              </p>
              <p className="mt-1 text-xl font-semibold">
                {formatCurrency(receivables.data.outstanding, currency)}
              </p>
            </div>
            <div>
              <p className="text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                Overdue
              </p>
              <p className="mt-1 text-xl font-semibold">
                {formatCurrency(receivables.data.overdue, currency)}
              </p>
            </div>
            <div>
              <p className="text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                Open invoices
              </p>
              <p className="mt-1 text-xl font-semibold">{receivables.data.open_count}</p>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ExpiringCard() {
  const { activeCompanyPublicId, can } = useCompany()
  const expiring = useCompanyQuery<PageEnvelope<{ public_id: string; title: string }>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['code-overview', 'expiring'],
    path: '/contracts',
    queryParams: '?expiring_within_days=30&limit=5',
    enabled: can('contracts.read'),
  })

  if (!can('contracts.read')) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Expiring within 30 days</CardTitle>
      </CardHeader>
      <CardContent>
        {expiring.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : (expiring.data?.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing expiring soon.</p>
        ) : (
          <ul className="divide-y divide-border/60">
            {(expiring.data?.data ?? []).map((row) => (
              <li key={row.public_id} className="py-2 text-sm">
                <Link
                  href={`/contracts/${row.public_id}`}
                  className="font-medium hover:text-primary-strong"
                >
                  {row.title}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
