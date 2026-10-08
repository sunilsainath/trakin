'use client'

import * as React from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { CreditCard, Plus } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate } from '@/lib/utils'
import { INVOICE_STATUSES, type Invoice, type Page as PageEnvelope } from '@/lib/domain-types'
import {
  Button,
  Dialog,
  EmptyState,
  Input,
  Select,
} from '@/components/ui'
import { CurrencySelect, DateInput, Field } from '@/components/forms'
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
import { ErrorState, LoadingTable } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Every invoice in the company.
 *
 * A row's public id and its invoice number are both shown: the `I...` id is what
 * the rest of the platform links to, and the human-readable number is what goes
 * on the paperwork the counterparty holds.
 */
export default function InvoicesPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())
  const [direction, setDirection] = React.useState<'RECEIVABLE' | 'PAYABLE'>('RECEIVABLE')

  // Deep links (e.g. /invoices?status=PAID from the CODE rail) initialize the
  // filters; unrecognized values are ignored, never sent to the API.
  const initialInvoiceFilters = React.useMemo(() => {
    if (typeof window === 'undefined') return {}
    const params = new URLSearchParams(window.location.search)
    const filters: Record<string, string | boolean> = {}
    const status = params.get('status') ?? ''
    if ((INVOICE_STATUSES as readonly string[]).includes(status)) {
      filters.status = status
    }
    if (params.get('overdue_only') === 'true') {
      filters.overdue_only = true
    }
    return filters
  }, [])

  const list = useCursorList<PageEnvelope<Invoice>>({
    companyPublicId: activeCompanyPublicId,
    path: '/invoices',
    queryKey: ['invoices', 'list', direction],
    initialFilters: { ...initialInvoiceFilters, direction },
  })

  React.useEffect(() => {
    list.setFilter('direction', direction)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [direction])

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
          description={
            direction === 'PAYABLE'
              ? 'Vendor bills this company owes. Recorded from vendor paperwork under a contract; amounts are derived server-side.'
              : 'What you have billed and what is still owed. Amounts are computed on the server from approved timesheets and the contract role rates.'
          }
          actions={
            <div className="flex gap-2">
              <div role="group" aria-label="Invoice direction" className="flex rounded-md border border-border p-0.5">
                {(['RECEIVABLE', 'PAYABLE'] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    onClick={() => setDirection(option)}
                    aria-pressed={direction === option}
                    className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                      direction === option
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {option === 'RECEIVABLE' ? 'Receivable' : 'Payable'}
                  </button>
                ))}
              </div>
              {direction === 'PAYABLE' && can('invoices.create') ? (
                <VendorBillDialog />
              ) : (
                <Link href="/billing">
                  <Button variant="outline">
                    <Plus aria-hidden />
                    Bill a period
                  </Button>
                </Link>
              )}
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
                      : direction === 'PAYABLE'
                        ? 'Record what this company owes its vendors from their paperwork.'
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
                    ) : direction === 'PAYABLE' && can('invoices.create') ? (
                      <VendorBillDialog triggerLabel="Record the first bill" />
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

/* -------------------------------------------------------------------------- */
/* Vendor bill                                                                */
/* -------------------------------------------------------------------------- */

interface BillLineRow {
  description: string
  quantity: string
  unit_rate: string
}

/**
 * Record a vendor bill (PAYABLE): money this company owes.
 *
 * Line items come from the vendor's paperwork under a contract for context;
 * the server derives every amount from them. Payable invoices never need an
 * MSA, so submission is not gated on one.
 */
function VendorBillDialog({ triggerLabel = 'Record vendor bill' }: { triggerLabel?: string }) {
  const router = useRouter()
  const { activeCompanyPublicId } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [contractId, setContractId] = React.useState('')
  const [vendorKind, setVendorKind] = React.useState<'company' | 'user'>('company')
  const [vendorId, setVendorId] = React.useState('')
  const [currency, setCurrency] = React.useState('USD')
  const [issueDate, setIssueDate] = React.useState('')
  const [dueDate, setDueDate] = React.useState('')
  const [terms, setTerms] = React.useState('30')
  const [notes, setNotes] = React.useState('')
  const [lines, setLines] = React.useState<BillLineRow[]>([
    { description: '', quantity: '1', unit_rate: '' },
  ])
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open) {
      setContractId('')
      setVendorKind('company')
      setVendorId('')
      setCurrency('USD')
      setIssueDate('')
      setDueDate('')
      setTerms('30')
      setNotes('')
      setLines([{ description: '', quantity: '1', unit_rate: '' }])
      setError(null)
    }
  }, [open])

  const create = useCompanyMutation<{ public_id: string }, Record<string, unknown>>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.post<{ public_id: string }>('/invoices', body, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [['invoices']],
    onSuccess: (invoice) => {
      notifySuccess('Vendor bill recorded.', `Payable ${invoice.public_id} is a draft.`)
      setOpen(false)
      router.push(`/invoices/${invoice.public_id}`)
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!contractId.trim()) {
      setError('Choose the contract this bill belongs to.')
      return
    }
    if (!vendorId.trim()) {
      setError('Name the vendor company (CO…) or user (U…).')
      return
    }
    const usable = lines.filter((line) => line.description.trim())
    if (usable.length === 0) {
      setError('Add at least one line item.')
      return
    }
    setError(null)
    try {
      await create.mutateAsync({
        contract_id: contractId.trim(),
        ...(vendorKind === 'company'
          ? { counterparty_company_id: vendorId.trim() }
          : { counterparty_user_id: vendorId.trim() }),
        currency,
        ...(issueDate ? { issue_date: issueDate } : {}),
        ...(dueDate ? { due_date: dueDate } : {}),
        payment_terms_days: Number(terms) || 30,
        ...(notes.trim() ? { notes: notes.trim() } : {}),
        items: usable.map((line) => ({
          description: line.description.trim(),
          quantity: Number(line.quantity) || 1,
          unit_rate: Number(line.unit_rate) || 0,
        })),
      })
    } catch (cause) {
      notifyError(cause, 'The vendor bill could not be recorded.')
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        {triggerLabel}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Record a vendor bill"
        description="What this company owes a vendor, entered from their paperwork. Amounts are derived server-side from the lines."
        className="max-w-2xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="vendor-bill" loading={create.isPending}>
              Record bill
            </Button>
          </>
        }
      >
        <form
          id="vendor-bill"
          onSubmit={submit}
          className="max-h-[65vh] space-y-4 overflow-y-auto pr-1"
        >
          {error ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {error}
            </p>
          ) : null}
          <Field label="Contract" required hint="The C… identifier this bill belongs to">
            <Input
              id="bill-contract"
              value={contractId}
              onChange={(event) => setContractId(event.target.value.toUpperCase())}
              placeholder="C…"
              className="font-mono"
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Vendor is">
              <Select
                id="bill-vendor-kind"
                value={vendorKind}
                onChange={(event) => setVendorKind(event.target.value as 'company' | 'user')}
              >
                <option value="company">A company</option>
                <option value="user">An individual</option>
              </Select>
            </Field>
            <Field
              label={vendorKind === 'company' ? 'Vendor company ID' : 'Vendor user ID'}
              required
            >
              <Input
                id="bill-vendor"
                value={vendorId}
                onChange={(event) => setVendorId(event.target.value.toUpperCase())}
                placeholder={vendorKind === 'company' ? 'CO…' : 'U…'}
                className="font-mono"
              />
            </Field>
            <Field label="Currency">
              <CurrencySelect id="bill-currency" value={currency} onChange={setCurrency} />
            </Field>
            <Field label="Payment terms (days)">
              <Input
                id="bill-terms"
                type="number"
                min={0}
                max={365}
                value={terms}
                onChange={(event) => setTerms(event.target.value)}
              />
            </Field>
            <Field label="Issue date" hint="Defaults to today">
              <DateInput id="bill-issue" value={issueDate} onChange={setIssueDate} />
            </Field>
            <Field label="Due date" hint="Defaults to issue + terms">
              <DateInput id="bill-due" value={dueDate} onChange={setDueDate} />
            </Field>
          </div>
          <Field label="Notes" hint="Optional">
            <Input id="bill-notes" value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Field>
          <Field label="Line items" required>
            <div className="space-y-2">
              {lines.map((line, index) => (
                <div key={index} className="grid grid-cols-[1fr_5rem_7rem_auto] items-center gap-2">
                  <Input
                    value={line.description}
                    onChange={(event) =>
                      setLines((previous) =>
                        previous.map((candidate, position) =>
                          position === index
                            ? { ...candidate, description: event.target.value }
                            : candidate,
                        ),
                      )
                    }
                    placeholder="What was supplied"
                    aria-label={`Line ${index + 1} description`}
                  />
                  <Input
                    value={line.quantity}
                    onChange={(event) =>
                      setLines((previous) =>
                        previous.map((candidate, position) =>
                          position === index ? { ...candidate, quantity: event.target.value } : candidate,
                        ),
                      )
                    }
                    placeholder="Qty"
                    aria-label={`Line ${index + 1} quantity`}
                  />
                  <Input
                    value={line.unit_rate}
                    onChange={(event) =>
                      setLines((previous) =>
                        previous.map((candidate, position) =>
                          position === index ? { ...candidate, unit_rate: event.target.value } : candidate,
                        ),
                      )
                    }
                    placeholder="Rate"
                    aria-label={`Line ${index + 1} rate`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => setLines((previous) => previous.filter((_, position) => position !== index))}
                  >
                    Remove
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() =>
                  setLines((previous) => [...previous, { description: '', quantity: '1', unit_rate: '' }])
                }
              >
                Add line
              </Button>
            </div>
          </Field>
        </form>
      </Dialog>
    </>
  )
}
