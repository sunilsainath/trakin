'use client'

import Link from 'next/link'

import { useCompany } from '@/hooks/use-company'
import { PageHeader, PageShell } from '@/components/page'
import { Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { Metric } from '@/components/badges'
import { DataTable } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import {
  ErrorState,
  LoadingBlock,
  PermissionState,
  isPermissionError,
  useCompanyQuery,
} from '@/components/query'
import { formatCurrency, formatDate } from '@/lib/utils'
import type { DashboardResponse } from '@/lib/domain-types'

/**
 * The Core module overview: projects, SOWs, contracts and receivables in one
 * place, read from the same dashboard assembly the workspace dashboard uses.
 *
 * Distributions render as labelled bars rather than a charting library: the
 * counts are small, exact numbers matter more than shapes, and there is one
 * less dependency to audit.
 */
export default function CoreOverviewPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const dashboard = useCompanyQuery<DashboardResponse>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['core', 'overview'],
    path: '/dashboard',
    enabled: can('dashboard.read'),
  })

  if (!can('dashboard.read')) {
    return (
      <PageShell width="wide">
        <PageHeader
          crumbs={[{ label: 'Core' }]}
          title="Core overview"
          description="Projects, SOWs, contracts and receivables at a glance."
        />
        <PermissionState
          error={{ code: 'PERMISSION_DENIED', message: 'Reading the Core overview requires dashboard.read.' }}
        />
      </PageShell>
    )
  }

  if (dashboard.isPending) {
    return (
      <PageShell width="wide">
        <PageHeader
          crumbs={[{ label: 'Core' }]}
          title="Core overview"
          description="Projects, SOWs, contracts and receivables at a glance."
        />
        <LoadingBlock rows={6} />
      </PageShell>
    )
  }

  if (dashboard.isError) {
    return (
      <PageShell width="wide">
        <PageHeader
          crumbs={[{ label: 'Core' }]}
          title="Core overview"
          description="Projects, SOWs, contracts and receivables at a glance."
        />
        {isPermissionError(dashboard.error) ? (
          <PermissionState error={dashboard.error} />
        ) : (
          <ErrorState error={dashboard.error} onRetry={() => void dashboard.refetch()} />
        )}
      </PageShell>
    )
  }

  const data = dashboard.data
  const currency = data.company.currency || 'USD'
  const projects = data.panels.projects
  const contracts = data.panels.contracts
  const sows = data.panels.sows ?? {}
  const receivables = data.panels.finance?.receivables

  const contractPending =
    (contracts?.by_status['SENT'] ?? 0) + (contracts?.by_status['PENDING_ACCEPTANCE'] ?? 0)
  const sowPending =
    (sows['PENDING_APPROVAL'] ?? 0) + (sows['SENT'] ?? 0) + (sows['PENDING_ACCEPTANCE'] ?? 0)
  const expiring = contracts?.expiring_within_60_days ?? []

  return (
    <PageShell width="wide">
      <PageHeader
        crumbs={[{ label: 'Core' }]}
        title="Core overview"
        description="Projects, SOWs, contracts and receivables at a glance."
      />
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric label="Active projects" value={projects?.active ?? 0} />
          <Metric
            label="Active SOWs"
            value={sows['ACTIVE'] ?? 0}
          />
          <Metric
            label="Active contracts"
            value={contracts?.by_status['ACTIVE'] ?? 0}
          />
          <Metric
            label="Pending acceptance"
            value={contractPending + sowPending}
          />
          <Metric
            label="Expiring within 60 days"
            value={expiring.length}
          />
          <Metric
            label="Receivables outstanding"
            value={
              receivables ? formatCurrency(receivables.outstanding, currency, { compact: true }) : '—'
            }
          />
          <Metric
            label="Overdue"
            value={
              receivables ? formatCurrency(receivables.overdue, currency, { compact: true }) : '—'
            }
          />
          <Metric
            label="Draft contracts"
            value={contracts?.by_status['DRAFT'] ?? 0}
          />
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Contracts by status</CardTitle>
            </CardHeader>
            <CardContent>
              <Distribution counts={contracts?.by_status ?? {}} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>SOWs by status</CardTitle>
            </CardHeader>
            <CardContent>
              <Distribution counts={sows} />
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Expiring within 60 days</CardTitle>
          </CardHeader>
          <CardContent>
            {expiring.length === 0 ? (
              <EmptyState
                title="Nothing expiring soon"
                description="Contracts approaching their end date appear here with time to renew."
              />
            ) : (
              <DataTable
                columns={[
                  {
                    key: 'title',
                    header: 'Contract',
                    cell: (row: { public_id: string; title: string }) => (
                      <div className="min-w-0">
                        <Link
                          href={`/contracts/${row.public_id}`}
                          className="block truncate font-medium hover:text-primary-strong"
                        >
                          {row.title}
                        </Link>
                        <PublicId value={row.public_id} kind="contract" />
                      </div>
                    ),
                  },
                  {
                    key: 'status',
                    header: 'Status',
                    cell: (row: { status: string }) => (
                      <span className="text-sm">{row.status}</span>
                    ),
                  },
                  {
                    key: 'end',
                    header: 'Ends',
                    cell: (row: { end_date: string }) => formatDate(row.end_date),
                  },
                ]}
                rows={expiring}
                rowKey={(row) => row.public_id}
                caption="Contracts ending within 60 days"
                exportName="core-expiring-contracts"
              />
            )}
          </CardContent>
        </Card>
      </div>
    </PageShell>
  )
}

function Distribution({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).filter(([, value]) => value > 0)
  const total = entries.reduce((sum, [, value]) => sum + value, 0)
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing to show yet.</p>
  }
  return (
    <ul className="space-y-2">
      {entries.map(([label, value]) => (
        <li key={label} className="flex items-center gap-3">
          <span className="w-36 shrink-0 truncate text-xs text-muted-foreground">{label}</span>
          <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-muted">
            <span
              className="block h-full rounded-full bg-primary"
              style={{ width: `${total > 0 ? Math.round((value / total) * 100) : 0}%` }}
            />
          </span>
          <span className="w-10 shrink-0 text-right text-xs font-medium">{value}</span>
        </li>
      ))}
    </ul>
  )
}
