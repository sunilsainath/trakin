'use client'

import * as React from 'react'
import { AlertTriangle, PlayCircle, Receipt, Wallet } from 'lucide-react'

import { api, createIdempotencyKey } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/utils'
import type {
  BillingRun,
  Contract,
  InvoicePreview,
  Page as PageEnvelope,
  ReceivablesSummary,
} from '@/lib/domain-types'
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
  Input,
  Select,
} from '@/components/ui'
import { CurrencySelect, Field } from '@/components/forms'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Billing: receivables, billing runs, and a preview-then-generate flow.
 *
 * Generation is the most consequential action in the product, so it is a
 * two-step: preview computes exactly what would be raised and writes nothing,
 * and only an explicit confirm generates. The preview is also what tells the user
 * the MSA is blocking before they find out from an error.
 */
export default function BillingPage() {
  const { activeCompanyPublicId, activeCompany, can } = useCompany()

  const receivables = useCompanyQuery<ReceivablesSummary>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['billing', 'receivables'],
    path: '/billing/receivables',
    enabled: can('billing_runs.read') || can('invoices.read'),
  })

  const runs = useCursorList<PageEnvelope<BillingRun>>({
    companyPublicId: activeCompanyPublicId,
    path: '/billing/runs',
    queryKey: ['billing', 'runs'],
    enabled: can('billing_runs.read'),
  })

  const currency = activeCompany?.default_currency ?? 'USD'
  const [billing, setBilling] = React.useState(false)

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Billing' }]}
          title="Billing"
          description="What you are owed, and the runs that raised the invoices. Amounts are computed by the server from approved timesheets and contract role rates."
          actions={
            can('billing_runs.execute') || can('invoices.create') ? (
              <Button onClick={() => setBilling(true)}>
                <PlayCircle aria-hidden />
                Bill a period
              </Button>
            ) : (
              <p className="text-sm text-muted-foreground">
                You do not have permission to run billing in this company.
              </p>
            )
          }
        />

        {receivables.isPending ? (
          <LoadingBlock rows={4} />
        ) : receivables.isError ? (
          <ErrorState error={receivables.error} onRetry={() => void receivables.refetch()} />
        ) : receivables.data ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Metric
                label="Outstanding"
                value={formatCurrency(receivables.data.outstanding, currency, { compact: true })}
                hint={`${receivables.data.open_count} open invoices`}
              />
              <Metric
                label="Overdue"
                value={formatCurrency(receivables.data.overdue, currency, { compact: true })}
                tone={Number(receivables.data.overdue) > 0 ? 'danger' : undefined}
              />
              <Metric
                label="Not yet due"
                value={formatCurrency(receivables.data.not_yet_due, currency, { compact: true })}
              />
              <Metric
                label="Collected this month"
                value={formatCurrency(receivables.data.collected_this_month, currency, { compact: true })}
                tone="success"
                hint={`Invoiced ${formatCurrency(receivables.data.invoiced_this_month, currency, { compact: true })}`}
              />
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Wallet aria-hidden className="size-4 text-primary" />
                    Aging
                  </CardTitle>
                  <CardDescription>
                    How long the money has been outstanding.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {receivables.data.aging.length === 0 ? (
                    <EmptyState
                      title="Nothing outstanding"
                      description="Every issued invoice is settled."
                    />
                  ) : (
                    <ul className="space-y-2">
                      {receivables.data.aging.map((bucket) => (
                        <li
                          key={bucket.bucket}
                          className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
                        >
                          <div>
                            <p className="text-sm font-medium">{bucketLabel(bucket.bucket)}</p>
                            <p className="text-xs text-muted-foreground">
                              {bucket.invoice_count}{' '}
                              {bucket.invoice_count === 1 ? 'invoice' : 'invoices'}
                            </p>
                          </div>
                          <span className="text-sm font-semibold tabular">
                            {formatCurrency(bucket.amount, currency, { compact: true })}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <AlertTriangle aria-hidden className="size-4 text-primary" />
                    Late payers
                  </CardTitle>
                  <CardDescription>
                    Counterparties with invoices past their due date.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {receivables.data.late_payers.length === 0 ? (
                    <EmptyState title="Nobody is late" description="No invoice is past its due date." />
                  ) : (
                    <ul className="divide-y divide-border/60">
                      {receivables.data.late_payers.map((payer) => (
                        <li key={payer.counterparty} className="flex items-center justify-between gap-3 py-2.5">
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{payer.counterparty}</p>
                            <p className="text-xs text-muted-foreground">
                              {payer.invoice_count} {payer.invoice_count === 1 ? 'invoice' : 'invoices'} ·{' '}
                              {payer.days_late} days late
                            </p>
                          </div>
                          <span className="shrink-0 text-sm font-semibold tabular text-danger">
                            {formatCurrency(payer.amount, currency, { compact: true })}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </div>

            {(receivables.data.revenue_by_role.length > 0 ||
              receivables.data.revenue_by_project.length > 0) ? (
              <div className="grid gap-6 lg:grid-cols-2">
                <RevenueCard
                  title="Revenue by role"
                  rows={receivables.data.revenue_by_role}
                  currency={currency}
                  labelKeys={['role_title', 'title', 'name']}
                />
                <RevenueCard
                  title="Revenue by project"
                  rows={receivables.data.revenue_by_project}
                  currency={currency}
                  labelKeys={['project_name', 'name', 'title']}
                />
              </div>
            ) : null}
          </>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Receipt aria-hidden className="size-4 text-primary" />
              Billing runs
            </CardTitle>
            <CardDescription>
              Every period you have billed. A run bills each eligible contract once;
              repeating the same period does not create duplicates.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {runs.query.isPending ? (
              <LoadingBlock rows={4} />
            ) : runs.query.isError ? (
              <ErrorState error={runs.query.error} onRetry={() => void runs.query.refetch()} />
            ) : (
              <>
                <DataTable
                  columns={runColumns}
                  rows={runs.query.data?.data ?? []}
                  rowKey={(row) => row.public_id}
                  caption="Billing runs"
                  exportName="billing-runs"
                  csv={[
                    { header: 'Run ID', value: (row) => row.public_id },
                    { header: 'Type', value: (row) => row.run_type },
                    { header: 'Status', value: (row) => row.status },
                    { header: 'Period start', value: (row) => row.period_start },
                    { header: 'Period end', value: (row) => row.period_end },
                    { header: 'Contracts scanned', value: (row) => row.contracts_scanned },
                    { header: 'Invoices created', value: (row) => row.invoices_created },
                    { header: 'Invoices skipped', value: (row) => row.invoices_skipped },
                    { header: 'Total', value: (row) => row.total_amount },
                    { header: 'Currency', value: (row) => row.currency },
                    { header: 'Started', value: (row) => row.started_at },
                    { header: 'Finished', value: (row) => row.finished_at },
                  ]}
                  emptyState={
                    <EmptyState
                      icon={<Receipt aria-hidden />}
                      title="No billing runs yet"
                      description="Run billing for a period to raise invoices from approved timesheets."
                      action={
                        can('billing_runs.execute') || can('invoices.create') ? (
                          <Button onClick={() => setBilling(true)}>Bill a period</Button>
                        ) : undefined
                      }
                    />
                  }
                />

                <div className="mt-4">
                  <CursorFooter
                    meta={runs.query.data?.meta}
                    count={runs.query.data?.data.length ?? 0}
                    onNext={runs.next}
                    onPrevious={runs.previous}
                    canGoBack={runs.canGoBack}
                    busy={runs.query.isFetching}
                    noun="runs"
                  />
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <BillingRunDialog
          open={billing}
          onOpenChange={setBilling}
          companyPublicId={activeCompanyPublicId}
          defaultCurrency={currency}
        />
      </div>
    </PageShell>
  )
}

function bucketLabel(bucket: string): string {
  switch (bucket) {
    case 'OVERDUE':
      return 'Overdue'
    case 'DUE_1_30':
      return '1 to 30 days'
    case 'DUE_31_60':
      return '31 to 60 days'
    case 'DUE_61_90':
      return '61 to 90 days'
    case 'DUE_90_PLUS':
      return 'Over 90 days'
    default:
      return bucket
  }
}

const runColumns: Column<BillingRun>[] = [
  {
    key: 'run',
    header: 'Run',
    cell: (row) => (
      <div className="min-w-0">
        <PublicId value={row.public_id} />
        <p className="truncate text-xs text-muted-foreground">{row.run_type}</p>
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
    key: 'period',
    header: 'Period',
    cell: (row) =>
      row.period_start && row.period_end
        ? `${formatDate(row.period_start)} – ${formatDate(row.period_end)}`
        : '—',
  },
  { key: 'scanned', header: 'Scanned', numeric: true, hideBelow: 'md', cell: (row) => row.contracts_scanned },
  {
    key: 'created',
    header: 'Created',
    numeric: true,
    cell: (row) => (
      <span className={row.invoices_created > 0 ? 'font-medium text-success' : undefined}>
        {row.invoices_created}
      </span>
    ),
  },
  {
    key: 'skipped',
    header: 'Skipped',
    numeric: true,
    hideBelow: 'md',
    cell: (row) => row.invoices_skipped,
  },
  {
    key: 'total',
    header: 'Total',
    numeric: true,
    cell: (row) => formatCurrency(row.total_amount, row.currency, { compact: true }),
  },
  {
    key: 'finished',
    header: 'Finished',
    hideBelow: 'lg',
    cell: (row) => (row.finished_at ? formatDateTime(row.finished_at) : 'running'),
  },
]

/**
 * Revenue aggregates arrive as projections with varying keys, so the label is
 * looked up across the plausible names rather than assumed.
 */
function RevenueCard({
  title,
  rows,
  currency,
  labelKeys,
}: {
  title: string
  rows: Record<string, unknown>[]
  currency: string
  labelKeys: string[]
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState title="No revenue recorded yet" description="Nothing has been invoiced." />
        ) : (
          <ul className="divide-y divide-border/60">
            {rows.slice(0, 10).map((row, index) => {
              const label =
                labelKeys.map((key) => row[key]).find((value) => typeof value === 'string') ??
                'Unnamed'
              const amount = String(row.amount ?? row.revenue ?? row.total ?? '0')
              return (
                <li key={index} className="flex items-center justify-between gap-3 py-2">
                  <span className="truncate text-sm">{String(label)}</span>
                  <span className="shrink-0 text-sm font-medium tabular">
                    {formatCurrency(amount, String(row.currency ?? currency), { compact: true })}
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Bill a period                                                              */
/* -------------------------------------------------------------------------- */

/** The current calendar month, as the default billing period. */
function currentMonth(): { start: string; end: string } {
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), 1)
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0)
  const iso = (date: Date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
  return { start: iso(start), end: iso(end) }
}

function BillingRunDialog({
  open,
  onOpenChange,
  companyPublicId,
  defaultCurrency,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  companyPublicId: string | null
  defaultCurrency: string
}) {
  const month = currentMonth()
  const [periodStart, setPeriodStart] = React.useState(month.start)
  const [periodEnd, setPeriodEnd] = React.useState(month.end)
  const [currency, setCurrency] = React.useState(defaultCurrency)
  const [error, setError] = React.useState<string | null>(null)
  const [key, setKey] = React.useState(() => createIdempotencyKey('billingrun'))

  const contracts = useCompanyQuery<PageEnvelope<Contract>>({
    companyPublicId,
    queryKey: ['contracts', 'list', 'billing'],
    path: '/contracts',
    queryParams: '?status=ACTIVE&limit=100',
    enabled: open,
  })

  const [contractId, setContractId] = React.useState('')
  const [preview, setPreview] = React.useState<InvoicePreview | null>(null)
  const [discount, setDiscount] = React.useState('0')
  const [adjustment, setAdjustment] = React.useState('0')
  const [notes, setNotes] = React.useState('')

  React.useEffect(() => {
    if (open) {
      const period = currentMonth()
      setPeriodStart(period.start)
      setPeriodEnd(period.end)
      setCurrency(defaultCurrency)
      setContractId('')
      setPreview(null)
      setDiscount('0')
      setAdjustment('0')
      setNotes('')
      setError(null)
      setKey(createIdempotencyKey('billingrun'))
    }
  }, [open, defaultCurrency])

  const runPreview = useCompanyMutation<InvoicePreview, void>({
    context: { companyPublicId },
    mutationFn: () => {
      if (!contractId) throw new Error('Choose a contract.')
      if (periodEnd < periodStart) throw new Error('The period end must not precede the start.')
      return api.post<InvoicePreview>(
        '/billing/preview',
        { contract_id: contractId, period_start: periodStart, period_end: periodEnd },
        { companyPublicId },
      )
    },
    onSuccess: (result) => setPreview(result),
  })

  const generate = useCompanyMutation<{ public_id: string }, void>({
    context: { companyPublicId },
    mutationFn: () => {
      if (!contractId) throw new Error('Choose a contract.')
      return api.post<{ public_id: string }>(
        '/billing/generate',
        {
          contract_id: contractId,
          period_start: periodStart,
          period_end: periodEnd,
          discount: discount || '0',
          adjustment: adjustment || '0',
          ...(notes ? { notes } : {}),
        },
        { companyPublicId, idempotencyKey: key },
      )
    },
    invalidate: [['invoices', 'list'], ['billing', 'runs'], ['billing', 'receivables']],
    onSuccess: (invoice) => {
      notifySuccess('Invoice created.', `${invoice.public_id} is ready for review.`)
      setKey(createIdempotencyKey('billingrun'))
    },
  })

  const startRun = useCompanyMutation<BillingRun, void>({
    context: { companyPublicId },
    mutationFn: () => {
      if (periodEnd < periodStart) throw new Error('The period end must not precede the start.')
      return api.post<BillingRun>(
        '/billing/runs',
        { period_start: periodStart, period_end: periodEnd, currency },
        { companyPublicId },
      )
    },
    invalidate: [['billing', 'runs']],
    onSuccess: (run) => {
      notifySuccess(
        'Billing run started.',
        `${run.contracts_scanned} contracts scanned; the rest continue in the background.`,
      )
      setKey(createIdempotencyKey('billingrun'))
    },
  })

  const doPreview = async () => {
    setError(null)
    try {
      await runPreview.mutateAsync()
    } catch (cause) {
      notifyError(cause, 'The preview could not be produced.')
    }
  }

  const doGenerate = async () => {
    setError(null)
    try {
      await generate.mutateAsync()
    } catch (cause) {
      notifyError(cause, 'The invoice could not be generated.')
    }
  }

  const doStartRun = async () => {
    setError(null)
    try {
      await startRun.mutateAsync()
    } catch (cause) {
      notifyError(cause, 'The billing run could not be started.')
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Bill a period"
      description="Preview first, then generate. Previewing writes nothing; generating creates a real invoice against the contract."
      className="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            variant="outline"
            onClick={() => void doStartRun()}
            loading={startRun.isPending}
            disabled={generate.isPending}
          >
            Run for all contracts
          </Button>
          <Button
            onClick={() => void doGenerate()}
            loading={generate.isPending}
            disabled={!preview}
            title={preview ? undefined : 'Preview the period before generating'}
          >
            Generate invoice
          </Button>
        </>
      }
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        <Field
          label="Contract"
          required
          hint="Only active contracts can be billed."
          error={error ?? undefined}
        >
          <Select id="billing-contract" value={contractId} onChange={(event) => {
            setContractId(event.target.value)
            setPreview(null)
          }}>
            <option value="">Choose a contract</option>
            {(contracts.data?.data ?? [])
              .filter((contract) => contract.status === 'ACTIVE')
              .map((contract) => (
                <option key={contract.public_id} value={contract.public_id}>
                  {contract.title} ({contract.public_id})
                </option>
              ))}
          </Select>
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Period start" required>
            <Input
              id="billing-start"
              type="date"
              value={periodStart}
              onChange={(event) => {
                setPeriodStart(event.target.value)
                setPreview(null)
              }}
            />
          </Field>
          <Field label="Period end" required>
            <Input
              id="billing-end"
              type="date"
              value={periodEnd}
              onChange={(event) => {
                setPeriodEnd(event.target.value)
                setPreview(null)
              }}
            />
          </Field>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Discount" hint={`In ${currency}`}>
            <Input
              id="billing-discount"
              type="number"
              min="0"
              step="0.01"
              value={discount}
              onChange={(event) => setDiscount(event.target.value)}
            />
          </Field>
          <Field label="Adjustment" hint={`In ${currency}`}>
            <Input
              id="billing-adjustment"
              type="number"
              step="0.01"
              value={adjustment}
              onChange={(event) => setAdjustment(event.target.value)}
            />
          </Field>
        </div>

        <Field label="Currency">
          <CurrencySelect id="billing-currency" value={currency} onChange={setCurrency} />
        </Field>

        <Field label="Notes" hint="Appears on the invoice">
          <Input id="billing-notes" value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>

        <Button variant="outline" onClick={() => void doPreview()} loading={runPreview.isPending} disabled={!contractId}>
          Preview this period
        </Button>

        {preview ? (
          <div className="rounded-lg border border-border bg-surface-sunken p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-semibold">Preview</p>
              <Badge tone={preview.contract_status === 'ACTIVE' ? 'success' : 'warning'}>
                Contract {preview.contract_status.toLowerCase()}
              </Badge>
            </div>

            {preview.msa_required ? (
              <p className="mt-2 rounded-md bg-warning-soft p-2 text-xs text-warning">
                An active master service agreement is required before this invoice can
                be approved or sent. Generating is still allowed, but it will stop at
                approval.
              </p>
            ) : null}

            {preview.warnings.length > 0 ? (
              <ul className="mt-2 space-y-1">
                {preview.warnings.map((warning, index) => (
                  <li key={index} className="flex items-start gap-1.5 text-xs text-muted-foreground">
                    <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0 text-warning" />
                    {warning}
                  </li>
                ))}
              </ul>
            ) : null}

            {preview.items.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Nothing to bill for this period. There are no approved and unbilled
                timesheets.
              </p>
            ) : (
              <table className="mt-3 w-full text-sm">
                <caption className="sr-only">Preview line items</caption>
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th scope="col" className="py-1.5 pr-2 font-semibold">Description</th>
                    <th scope="col" className="py-1.5 pr-2 text-right font-semibold">Qty</th>
                    <th scope="col" className="py-1.5 text-right font-semibold">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.items.map((item, index) => (
                    <tr key={index} className="border-b border-border/60">
                      <td className="py-1.5 pr-2">
                        {String(item.description ?? item.label ?? '—')}
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular">
                        {String(item.quantity ?? '1')}
                      </td>
                      <td className="py-1.5 text-right tabular">
                        {formatCurrency(
                          String(item.total ?? item.amount ?? '0'),
                          preview.currency,
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td colSpan={2} className="pt-2 text-right text-muted-foreground">
                      Subtotal
                    </td>
                    <td className="pt-2 text-right tabular">
                      {formatCurrency(preview.subtotal, preview.currency)}
                    </td>
                  </tr>
                  <tr>
                    <td colSpan={2} className="text-right text-muted-foreground">
                      Tax
                    </td>
                    <td className="text-right tabular">
                      {formatCurrency(preview.tax_total, preview.currency)}
                    </td>
                  </tr>
                  <tr>
                    <td colSpan={2} className="font-semibold">
                      Total
                    </td>
                    <td className="font-semibold tabular">
                      {formatCurrency(preview.total, preview.currency)}
                    </td>
                  </tr>
                </tfoot>
              </table>
            )}
          </div>
        ) : null}
      </div>
    </Dialog>
  )
}