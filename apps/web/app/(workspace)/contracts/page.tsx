'use client'

import * as React from 'react'
import Link from 'next/link'
import { FileText } from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import { formatCurrency, formatDate } from '@/lib/utils'
import { CONTRACT_STATUSES, type Contract, type Page as PageEnvelope } from '@/lib/domain-types'
import { Button, EmptyState } from '@/components/ui'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import {
  FilterBar,
  FilterInput,
  FilterNumber,
  FilterSelect,
  useDebouncedValue,
} from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable } from '@/components/query'

/**
 * Every contract in the company.
 *
 * `expiring_within_days` is sent as a number, not a string: the API validates it
 * as an integer between 1 and 365, so an empty filter must be omitted entirely
 * rather than sent blank.
 */
export default function ContractsPage() {
  const { activeCompanyPublicId } = useCompany()
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())

  const list = useCursorList<PageEnvelope<Contract>>({
    companyPublicId: activeCompanyPublicId,
    path: '/contracts',
    queryKey: ['contracts', 'list'],
  })

  React.useEffect(() => {
    list.setFilter('q', debouncedSearch || null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const rows = list.query.data?.data ?? []

  const columns: Column<Contract>[] = [
    {
      key: 'title',
      header: 'Contract',
      cell: (row) => (
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
      key: 'value',
      header: 'Value',
      numeric: true,
      cell: (row) =>
        row.contract_value
          ? formatCurrency(row.contract_value, row.currency, { compact: true })
          : '—',
    },
    {
      key: 'outstanding',
      header: 'Outstanding',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => formatCurrency(row.outstanding_total, row.currency, { compact: true }),
    },
    {
      key: 'end',
      header: 'Ends',
      hideBelow: 'md',
      cell: (row) => (row.end_date ? formatDate(row.end_date) : '—'),
    },
  ]

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Contracts' }]}
          title="Contracts"
          description="What has been agreed, with whom, and where each one stands. The action buttons on a contract come from the transitions the server will accept."
        />

        <FilterBar
          activeCount={list.activeFilterCount}
          onClear={() => {
            setSearch('')
            list.clearFilters()
          }}
        >
          <FilterInput
            id="contract-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Title or counterparty"
            className="min-w-56 flex-1"
          />
          <FilterSelect
            id="contract-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={[...CONTRACT_STATUSES]}
            className="w-48"
          />
          <FilterNumber
            id="contract-expiring"
            label="Expiring within (days)"
            value={(list.filters.expiring_within_days as string) ?? ''}
            onChange={(value) => list.setFilter('expiring_within_days', value)}
            placeholder="e.g. 60"
            step="1"
            className="w-44"
          />
        </FilterBar>

        {list.query.isPending ? (
          <LoadingTable rows={8} columns={6} />
        ) : list.query.isError ? (
          <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Contracts in this company"
              exportName="contracts"
              csv={[
                { header: 'Contract ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Status', value: (row) => row.status },
                { header: 'Project ID', value: (row) => row.project_id },
                { header: 'Project', value: (row) => row.project_name },
                { header: 'SOW ID', value: (row) => row.sow_id },
                { header: 'Counterparty', value: (row) => row.counterparty_company_name },
                { header: 'Currency', value: (row) => row.currency },
                { header: 'Contract value', value: (row) => row.contract_value },
                { header: 'Invoiced', value: (row) => row.invoiced_total },
                { header: 'Outstanding', value: (row) => row.outstanding_total },
                { header: 'Start', value: (row) => row.start_date },
                { header: 'End', value: (row) => row.end_date },
              ]}
              emptyState={
                <EmptyState
                  icon={<FileText aria-hidden />}
                  title={
                    list.activeFilterCount > 0 ? 'No contracts match those filters' : 'No contracts yet'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Try a different search term, status or expiry window.'
                      : 'Contracts are generated from an approved statement of work, or created directly against one.'
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
                      <Link href="/sows">
                        <Button>Open statements of work</Button>
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
              noun="contracts"
            />
          </>
        )}
      </div>
    </PageShell>
  )
}