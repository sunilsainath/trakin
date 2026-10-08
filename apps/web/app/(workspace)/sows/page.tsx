'use client'

import * as React from 'react'
import Link from 'next/link'
import { FileSignature } from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import { formatCurrency, formatDate } from '@/lib/utils'
import { SOW_STATUSES, type Page as PageEnvelope, type Sow } from '@/lib/domain-types'
import { Button, EmptyState, Tabs } from '@/components/ui'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import {
  FilterBar,
  FilterInput,
  FilterSelect,
  useDebouncedValue,
} from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable, PermissionState, isPermissionError } from '@/components/query'
import { CreateSowOpener } from './create-sow'

/**
 * Every statement of work in the company.
 *
 * `q` is debounced before it reaches the API: a request per keystroke over a
 * growing result set is the difference between a fast list and a stalled one.
 */
const SOW_TABS = [
  { key: 'all', label: 'All' },
  { key: 'ACTIVE', label: 'Active' },
  { key: 'DRAFT', label: 'Draft' },
  { key: 'PENDING_APPROVAL,PENDING_ACCEPTANCE,SENT', label: 'Pending' },
  { key: 'REJECTED', label: 'Rejected' },
  { key: 'CLOSED', label: 'Closed' },
] as const

function SowStatusTabs({
  status,
  onSelect,
}: {
  status: string
  onSelect: (value: string) => void
}) {
  const active = SOW_TABS.some((tab) => tab.key === status) ? status : 'all'
  return (
    <Tabs
      tabs={[...SOW_TABS]}
      active={active}
      onChange={(key) => onSelect(key === 'all' ? '' : key)}
      className="overflow-x-auto scrollbar-thin"
    />
  )
}

export default function SowsPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())

  const list = useCursorList<PageEnvelope<Sow>>({
    companyPublicId: activeCompanyPublicId,
    path: '/sows',
    queryKey: ['sows', 'list'],
    initialFilters: { q: debouncedSearch || null, status: null },
  })

  React.useEffect(() => {
    list.setFilter('q', debouncedSearch || null)
    // Only the debounced search drives the filter; re-running on identity changes
    // would loop, because setFilter replaces the filter object every time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const rows = list.query.data?.data ?? []

  const columns: Column<Sow>[] = [
    {
      key: 'title',
      header: 'Statement of work',
      cell: (row) => (
        <div className="min-w-0">
          <Link
            href={`/sows/${row.public_id}`}
            className="block truncate font-medium hover:text-primary-strong"
          >
            {row.title}
          </Link>
          <PublicId value={row.public_id} kind="sow" />
        </div>
      ),
    },
    {
      key: 'project',
      header: 'Project',
      hideBelow: 'lg',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.project_name ?? '—'}</p>
          <PublicId value={row.project_id} kind="project" />
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      hideBelow: 'sm',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'counterparty',
      header: 'Counterparty',
      hideBelow: 'lg',
      cell: (row) => (
        <span className="text-muted-foreground">
          {row.counterparty_company_name ?? row.counterparty_user_name ?? '—'}
        </span>
      ),
    },
    {
      key: 'max',
      header: 'Capped at',
      numeric: true,
      hideBelow: 'md',
      cell: (row) =>
        row.max_total_amount
          ? formatCurrency(row.max_total_amount, row.currency, { compact: true })
          : '—',
    },
    {
      key: 'period',
      header: 'Period',
      hideBelow: 'md',
      cell: (row) =>
        row.start_date
          ? `${formatDate(row.start_date)} – ${row.end_date ? formatDate(row.end_date) : 'open'}`
          : '—',
    },
    {
      key: 'contracts',
      header: 'Contracts',
      numeric: true,
      hideBelow: 'lg',
      cell: (row) => row.contract_count,
    },
  ]

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Statements of Work' }]}
          title="Statements of work"
          description="What was promised, to whom, and at which roles and rates. Contracts are generated from an approved SOW."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Link href="/projects">
                <Button variant="outline">Choose a project</Button>
              </Link>
              {can('sows.create') ? (
                <React.Suspense fallback={null}>
                  <CreateSowOpener />
                </React.Suspense>
              ) : null}
            </div>
          }
        />

        <FilterBar
          activeCount={list.activeFilterCount}
          onClear={() => {
            setSearch('')
            list.clearFilters()
          }}
        >
          <FilterInput
            id="sow-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Title or description"
            className="min-w-56 flex-1"
          />
          <FilterSelect
            id="sow-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={[...SOW_STATUSES]}
            className="w-48"
          />
        </FilterBar>

        <SowStatusTabs
          status={(list.filters.status as string) ?? ''}
          onSelect={(value) => list.setFilter('status', value)}
        />

        {list.query.isPending ? (
          <LoadingTable rows={8} columns={6} />
        ) : list.query.isError ? (
          isPermissionError(list.query.error) ? (
            <PermissionState error={list.query.error} />
          ) : (
            <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
          )
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Statements of work in this company"
              exportName="sows"
              csv={[
                { header: 'SOW ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Project ID', value: (row) => row.project_id },
                { header: 'Project', value: (row) => row.project_name },
                { header: 'Status', value: (row) => row.status },
                { header: 'Counterparty', value: (row) => row.counterparty_company_name },
                { header: 'Currency', value: (row) => row.currency },
                { header: 'Max total', value: (row) => row.max_total_amount },
                { header: 'Start', value: (row) => row.start_date },
                { header: 'End', value: (row) => row.end_date },
                { header: 'Contracts', value: (row) => row.contract_count },
              ]}
              emptyState={
                <EmptyState
                  icon={<FileSignature aria-hidden />}
                  title={
                    list.activeFilterCount > 0
                      ? 'No SOWs match those filters'
                      : 'No statements of work yet'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Try a different search term, or clear the status filter.'
                      : can('sows.create')
                        ? 'A SOW scopes the work and prices the roles. Create one from the project it belongs to.'
                        : 'A SOW scopes the work and prices the roles. One has not been written for this company yet.'
                  }
                  action={
                    list.activeFilterCount > 0 ? (
                      <Button
                        variant="outline"
onClick={() => {
                        setSearch('')
                        list.clearFilters()
                      }}
                      >
                        Clear filters
                      </Button>
                    ) : (
                      <Link href="/projects">
                        <Button>Open projects</Button>
                      </Link>
                    )
                  }
                />
              }
            />

            <CursorFooter
              meta={list.query.data?.meta}
              count={rows.length}
              onNext={list.next}
              onPrevious={list.previous}
              canGoBack={list.canGoBack}
              busy={list.query.isFetching}
              noun="statements of work"
            />
          </>
        )}
      </div>
    </PageShell>
  )
}