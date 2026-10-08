'use client'

import * as React from 'react'
import Link from 'next/link'
import { CreditCard, Plus } from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import { formatCurrency, formatDate } from '@/lib/utils'
import { INVOICE_STATUSES, type Invoice, type Page as PageEnvelope } from '@/lib/domain-types'
import {
  Button,
  EmptyState,
  Tabs,
} from '@/components/ui'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import {
  FilterBar,
  FilterCheckbox,
  FilterDate,
  FilterInput,
  FilterSelect,
  useDebouncedValue,
} from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable, PermissionState, isPermissionError } from '@/components/query'

/**
 * Every invoice in the company.
 *
 * A row's public id and its invoice number are both shown: the `I...` id is what
 * the rest of the platform links to, and the human-readable number is what goes
 * on the paperwork the counterparty holds.
 */
const INVOICE_TABS = [
  { key: 'all', label: 'All' },
  { key: 'receivable', label: 'Receivable' },
  { key: 'payable', label: 'Payable' },
  { key: 'DRAFT', label: 'Draft' },
  { key: 'PAID', label: 'Paid' },
  { key: 'REJECTED', label: 'Rejected' },
] as const

function InvoiceTabs({
  list,
}: {
  list: {
    filters: Record<string, unknown>
    setFilter: (key: string, value: string) => void
  }
}) {
  const direction = (list.filters.direction as string) ?? ''
  const status = (list.filters.status as string) ?? ''
  const active =
    direction === 'RECEIVABLE'
      ? 'receivable'
      : direction === 'PAYABLE'
        ? 'payable'
        : INVOICE_TABS.some((tab) => tab.key === status)
          ? status
          : 'all'
  return (
    <Tabs
      tabs={[...INVOICE_TABS]}
      active={active}
      onChange={(key) => {
        if (key === 'receivable' || key === 'payable') {
          list.setFilter('status', '')
          list.setFilter('direction', key.toUpperCase())
        } else {
          list.setFilter('direction', '')
          list.setFilter('status', key === 'all' ? '' : key)
        }
      }}
      className="overflow-x-auto scrollbar-thin"
    />
  )
}

export default function InvoicesPage() {
  const { activeCompanyPublicId } = useCompany()
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())

  const list = useCursorList<PageEnvelope<Invoice>>({
    companyPublicId: activeCompanyPublicId,
    path: '/invoices',
    queryKey: ['invoices', 'list'],
  })

  React.useEffect(() => {
    list.setFilter('q', debouncedSearch || null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const rows = list.query.data?.data ?? []

  const columns: Column<Invoice>[] = [
    {
      key: 'invoice',
      header: 'Invoice',
      cell: (row) => (
        <div className="min-w-0">
          <Link
            href={`/invoices/${row.public_id}`}
            className="block truncate font-medium hover:text-primary-strong"
          >
            {row.invoice_number ?? 'Not numbered'}
          </Link>
          <PublicId value={row.public_id} kind="invoice" />
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
        <div className="min-w-0">
          <p className="truncate">
            {row.counterparty_company_name ?? row.counterparty_user_name ?? '—'}
          </p>
          {row.project_name ? (
            <p className="truncate text-xs text-muted-foreground">{row.project_name}</p>
          ) : null}
        </div>
      ),
    },
    {
      key: 'period',
      header: 'Service period',
      hideBelow: 'lg',
      cell: (row) => `${formatDate(row.period_start)} – ${formatDate(row.period_end)}`,
    },
    {
      key: 'due',
      header: 'Due',
      hideBelow: 'md',
      cell: (row) => formatDate(row.due_date),
    },
    {
      key: 'total',
      header: 'Total',
      numeric: true,
      cell: (row) => formatCurrency(row.total_amount, row.currency),
    },
    {
      key: 'balance',
      header: 'Balance',
      numeric: true,
      cell: (row) => (
        <span className={Number(row.balance_due) > 0 ? 'font-medium text-warning' : undefined}>
          {formatCurrency(row.balance_due, row.currency)}
        </span>
      ),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Invoices' }]}
          title="Invoices"
          description="What you have billed and what is still owed. Amounts are computed on the server from approved timesheets and the contract role rates."
          actions={
            <Link href="/billing">
              <Button variant="outline">
                <Plus aria-hidden />
                Bill a period
              </Button>
            </Link>
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
            id="invoice-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Number, counterparty or project"
            className="min-w-52 flex-1"
          />
          <FilterSelect
            id="invoice-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={[...INVOICE_STATUSES]}
            className="w-48"
          />
          <FilterDate
            id="invoice-period-start"
            label="Period from"
            value={(list.filters.period_start as string) ?? ''}
            onChange={(value) => list.setFilter('period_start', value)}
          />
          <FilterDate
            id="invoice-period-end"
            label="Period to"
            value={(list.filters.period_end as string) ?? ''}
            onChange={(value) => list.setFilter('period_end', value)}
          />
          <FilterCheckbox
            id="invoice-overdue"
            label="Overdue only"
            checked={Boolean(list.filters.overdue_only)}
            onChange={(checked) => list.setFilter('overdue_only', checked)}
          />
        </FilterBar>

        <InvoiceTabs list={list} />

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
              caption="Invoices in this company"
              exportName="invoices"
              csv={[
                { header: 'Invoice ID', value: (row) => row.public_id },
                { header: 'Invoice number', value: (row) => row.invoice_number },
                { header: 'Status', value: (row) => row.status },
                { header: 'Direction', value: (row) => row.direction },
                { header: 'Counterparty', value: (row) => row.counterparty_company_name },
                { header: 'Project ID', value: (row) => row.project_id },
                { header: 'Contract ID', value: (row) => row.contract_id },
                { header: 'Period start', value: (row) => row.period_start },
                { header: 'Period end', value: (row) => row.period_end },
                { header: 'Due', value: (row) => row.due_date },
                { header: 'Currency', value: (row) => row.currency },
                { header: 'Subtotal', value: (row) => row.subtotal },
                { header: 'Tax', value: (row) => row.tax_total },
                { header: 'Total', value: (row) => row.total_amount },
                { header: 'Paid', value: (row) => row.amount_paid },
                { header: 'Balance due', value: (row) => row.balance_due },
              ]}
              emptyState={
                <EmptyState
                  icon={<CreditCard aria-hidden />}
                  title={
                    list.activeFilterCount > 0 ? 'No invoices match those filters' : 'No invoices yet'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Try a different status, period or search term.'
                      : 'Invoices are raised by running billing for a contract period from approved timesheets.'
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
                      <Link href="/billing">
                        <Button>Run billing</Button>
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
              noun="invoices"
            />
          </>
        )}
      </div>
    </PageShell>
  )
}
