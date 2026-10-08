'use client'

import * as React from 'react'
import { Landmark, Plus } from 'lucide-react'

import { api, createIdempotencyKey } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/utils'
import { PAYMENT_STATUSES, type Page as PageEnvelope, type Payment } from '@/lib/domain-types'
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
import { FilterBar, FilterCheckbox, FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { ConfirmOnlyDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Recorded payments.
 *
 * A payment here is a ledger entry: it records money that moved and allocates it
 * to invoices. It does not move money itself, and the processor is recorded as
 * `manual` unless a processor is wired up on the server.
 */
export default function PaymentsPage() {
  const { activeCompanyPublicId, activeCompany, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [detail, setDetail] = React.useState<string | null>(null)

  const list = useCursorList<PageEnvelope<Payment>>({
    companyPublicId: activeCompanyPublicId,
    path: '/payments',
    queryKey: ['payments', 'list'],
  })

  const rows = list.query.data?.data ?? []
  const currency = activeCompany?.default_currency ?? 'USD'

  const columns: Column<Payment>[] = [
    {
      key: 'payment',
      header: 'Payment',
      cell: (row) => (
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => setDetail(row.public_id)}
            className="block truncate text-left font-medium hover:text-primary-strong"
          >
            {row.counterparty_company_name ?? row.counterparty_name ?? row.public_id}
          </button>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    {
      key: 'direction',
      header: 'Direction',
      hideBelow: 'md',
      cell: (row) => (
        <Badge tone={row.direction === 'RECEIVABLE' ? 'primary' : 'neutral'}>
          {row.direction === 'RECEIVABLE' ? 'In' : 'Out'}
        </Badge>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'method',
      header: 'Method',
      hideBelow: 'lg',
      cell: (row) => <span className="text-muted-foreground">{row.payment_method}</span>,
    },
    {
      key: 'amount',
      header: 'Amount',
      numeric: true,
      cell: (row) => formatCurrency(row.amount, row.currency),
    },
    {
      key: 'fee',
      header: 'Fee',
      numeric: true,
      hideBelow: 'lg',
      cell: (row) => (Number(row.fee_amount) > 0 ? formatCurrency(row.fee_amount, row.currency) : '—'),
    },
    {
      key: 'allocated',
      header: 'Allocated',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => `${formatCurrency(row.allocated_total, row.currency)} of ${formatCurrency(row.amount, row.currency)}`,
    },
    {
      key: 'date',
      header: 'Completed',
      hideBelow: 'md',
      cell: (row) => (row.completed_at ? formatDate(row.completed_at) : row.scheduled_for ? `due ${formatDate(row.scheduled_for)}` : '—'),
    },
  ]

  const totals = rows.reduce(
    (acc, row) => {
      const amount = Number(row.amount)
      if (row.direction === 'PAYABLE') acc.out += amount
      else acc.in += amount
      return acc
    },
    { in: 0, out: 0 },
  )

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Payments' }]}
          title="Payments"
          description="Money that moved, and how it was allocated to invoices. Recording a payment here never contacts a bank or a processor."
          actions={
            can('payments.create') ? (
              <Button onClick={() => setOpen(true)}>
                <Plus aria-hidden />
                Record payment
              </Button>
            ) : undefined
          }
        />

        <div className="grid gap-4 sm:grid-cols-3">
          <Metric label="On this page" value={rows.length} />
          <Metric
            label="Received"
            value={formatCurrency(String(totals.in), currency, { compact: true })}
            tone="success"
          />
          <Metric
            label="Paid out"
            value={formatCurrency(String(totals.out), currency, { compact: true })}
          />
        </div>

        <FilterBar activeCount={list.activeFilterCount} onClear={list.clearFilters}>
          <FilterSelect
            id="payment-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={[...PAYMENT_STATUSES]}
            className="w-48"
          />
          <FilterSelect
            id="payment-direction"
            label="Direction"
            value={(list.filters.direction as string) ?? ''}
            onChange={(value) => list.setFilter('direction', value)}
            options={[
              { value: 'RECEIVABLE', label: 'Received' },
              { value: 'PAYABLE', label: 'Paid out' },
            ]}
            className="w-40"
          />
          <FilterCheckbox
            id="payment-unmatched"
            label="Unallocated only"
            checked={Boolean(list.filters.unmatched_only)}
            onChange={(checked) => list.setFilter('unmatched_only', checked)}
          />
        </FilterBar>

        {list.query.isPending ? (
          <LoadingBlock rows={8} />
        ) : list.query.isError ? (
          <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Payments in this company"
              exportName="payments"
              csv={[
                { header: 'Payment ID', value: (row) => row.public_id },
                { header: 'Direction', value: (row) => row.direction },
                { header: 'Status', value: (row) => row.status },
                { header: 'Amount', value: (row) => row.amount },
                { header: 'Fee', value: (row) => row.fee_amount },
                { header: 'Currency', value: (row) => row.currency },
                { header: 'Method', value: (row) => row.payment_method },
                { header: 'Counterparty', value: (row) => row.counterparty_company_name },
                { header: 'Allocated', value: (row) => row.allocated_total },
                { header: 'Scheduled for', value: (row) => row.scheduled_for },
                { header: 'Completed at', value: (row) => row.completed_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<Landmark aria-hidden />}
                  title={list.activeFilterCount > 0 ? 'No payments match' : 'No payments recorded'}
                  description={
                    list.activeFilterCount > 0
                      ? 'Clear the filters to see everything.'
                      : 'Record a payment when money moves, or let bank reconciliation match incoming credits automatically.'
                  }
                  action={
                    list.activeFilterCount > 0 ? (
                      <Button variant="outline" onClick={list.clearFilters}>
                        Clear filters
                      </Button>
                    ) : can('payments.create') ? (
                      <Button onClick={() => setOpen(true)}>Record a payment</Button>
                    ) : undefined
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
              noun="payments"
            />
          </>
        )}
      </div>

      <RecordPaymentDialog
        open={open}
        onOpenChange={setOpen}
        companyPublicId={activeCompanyPublicId}
        defaultCurrency={currency}
      />

      <PaymentDetailDialog paymentId={detail} onClose={() => setDetail(null)} />
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Record payment                                                             */
/* -------------------------------------------------------------------------- */

function RecordPaymentDialog({
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
  const [direction, setDirection] = React.useState('RECEIVABLE')
  const [amount, setAmount] = React.useState('')
  const [currency, setCurrency] = React.useState(defaultCurrency)
  const [counterparty, setCounterparty] = React.useState('')
  const [counterpartyName, setCounterpartyName] = React.useState('')
  const [method, setMethod] = React.useState('ACH')
  const [fee, setFee] = React.useState('0')
  const [scheduled, setScheduled] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [key, setKey] = React.useState(() => createIdempotencyKey('payment'))

  React.useEffect(() => {
    if (open) {
      setDirection('RECEIVABLE')
      setAmount('')
      setCurrency(defaultCurrency)
      setCounterparty('')
      setCounterpartyName('')
      setMethod('ACH')
      setFee('0')
      setScheduled('')
      setError(null)
      setKey(createIdempotencyKey('payment'))
    }
  }, [open, defaultCurrency])

  const record = useCompanyMutation<Payment, void>({
    context: { companyPublicId },
    mutationFn: () =>
      api.post<Payment>(
        '/payments',
        {
          direction,
          amount,
          currency,
          payment_method: method,
          fee_amount: fee || '0',
          processor: 'manual',
          idempotency_key: key,
          ...(counterparty ? { counterparty_company_id: counterparty } : {}),
          ...(counterpartyName ? { counterparty_name: counterpartyName } : {}),
          ...(scheduled ? { scheduled_for: scheduled } : {}),
        },
        { companyPublicId, idempotencyKey: key },
      ),
    invalidate: [['payments', 'list'], ['billing', 'receivables']],
    onSuccess: (payment) => {
      notifySuccess('Payment recorded.', `${payment.public_id} · not yet allocated to an invoice.`)
      setKey(createIdempotencyKey('payment'))
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Record a payment"
      description="Records money that has moved. It is not allocated to an invoice here: allocation happens on the payment or through reconciliation."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={record.isPending}>
            Cancel
          </Button>
          <Button
            loading={record.isPending}
            onClick={async () => {
              if (!amount || Number(amount) <= 0) {
                setError('Enter an amount greater than zero.')
                return
              }
              setError(null)
              try {
                await record.mutateAsync()
                onOpenChange(false)
              } catch (cause) {
                notifyError(cause, 'The payment could not be recorded.')
              }
            }}
          >
            Record payment
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Direction" required>
          <Select id="record-direction" value={direction} onChange={(event) => setDirection(event.target.value)}>
            <option value="RECEIVABLE">Received from a client</option>
            <option value="PAYABLE">Paid to a supplier</option>
          </Select>
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Amount" required error={error ?? undefined}>
            <Input
              id="record-amount"
              type="number"
              min="0"
              step="0.01"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              aria-invalid={Boolean(error)}
            />
          </Field>
          <Field label="Currency" required>
            <CurrencySelect id="record-currency" value={currency} onChange={setCurrency} />
          </Field>
          <Field label="Payment method">
            <Select id="record-method" value={method} onChange={(event) => setMethod(event.target.value)}>
              {['ACH', 'WIRE', 'CARD', 'CHECK', 'CASH', 'CRYPTO', 'OTHER'].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Processing fee">
            <Input
              id="record-fee"
              type="number"
              min="0"
              step="0.01"
              value={fee}
              onChange={(event) => setFee(event.target.value)}
            />
          </Field>
        </div>

        <Field label="Counterparty company ID" hint="Optional. A company public id, CO…">
          <Input
            id="record-counterparty"
            value={counterparty}
            onChange={(event) => setCounterparty(event.target.value.toUpperCase())}
            className="font-mono text-xs"
            placeholder="CO7X9BC4TR"
          />
        </Field>

        <Field label="Counterparty name" hint="Used when there is no linked company">
          <Input
            id="record-counterparty-name"
            value={counterpartyName}
            onChange={(event) => setCounterpartyName(event.target.value)}
          />
        </Field>

        <Field label="Scheduled for" hint="Optional. Leave blank if the money has already moved.">
          <Input
            id="record-scheduled"
            type="date"
            value={scheduled}
            onChange={(event) => setScheduled(event.target.value)}
          />
        </Field>

        <p className="rounded-md bg-surface-sunken p-3 text-xs text-muted-foreground">
          This request carries an idempotency key, so submitting twice cannot create two
          payments for the same intent.
        </p>
      </div>
    </Dialog>
  )
}

/* -------------------------------------------------------------------------- */
/* Payment detail                                                             */
/* -------------------------------------------------------------------------- */

function PaymentDetailDialog({
  paymentId,
  onClose,
}: {
  paymentId: string | null
  onClose: () => void
}) {
  const { activeCompanyPublicId, can } = useCompany()
  const [invoiceId, setInvoiceId] = React.useState('')
  const [amount, setAmount] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [confirming, setConfirming] = React.useState(false)

  const payment = useCompanyQuery<Payment>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['payments', 'detail', paymentId],
    path: `/payments/${paymentId ?? ''}`,
    enabled: Boolean(paymentId),
  })

  React.useEffect(() => {
    setInvoiceId('')
    setAmount('')
    setError(null)
  }, [paymentId])

  const data = payment.data
  const remaining = data ? Number(data.amount) - Number(data.allocated_total) : 0

  const allocate = useCompanyMutation<Payment, void>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () =>
      api.post<Payment>(
        `/payments/${paymentId ?? ''}/allocations`,
        { allocations: [{ invoice_id: invoiceId, amount }], matched_by: 'MANUAL' },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['payments'], ['invoices', 'list'], ['billing', 'receivables']],
    onSuccess: () => {
      notifySuccess('Allocated.', 'The invoice balance has been reduced.')
      setConfirming(false)
    },
  })

  return (
    <>
      <Dialog
        open={Boolean(paymentId)}
        onOpenChange={(open) => {
          if (!open) onClose()
        }}
        title={data ? `Payment ${data.public_id}` : 'Payment'}
        description={data?.counterparty_company_name ?? data?.counterparty_name ?? undefined}
        className="max-w-2xl"
        footer={
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        }
      >
        {payment.isPending ? (
          <LoadingBlock rows={5} />
        ) : payment.isError ? (
          <ErrorState error={payment.error} onRetry={() => void payment.refetch()} />
        ) : data ? (
          <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
            <div className="grid gap-4 sm:grid-cols-4">
              <Metric label="Amount" value={formatCurrency(data.amount, data.currency)} />
              <Metric
                label="Allocated"
                value={formatCurrency(data.allocated_total, data.currency)}
                tone={remaining > 0 ? 'warning' : 'success'}
              />
              <Metric label="Unallocated" value={formatCurrency(String(remaining), data.currency)} />
              <Metric label="Status" value={<StatusBadge status={data.status} />} />
            </div>

            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              <Detail label="Direction" value={data.direction} />
              <Detail label="Method" value={data.payment_method} />
              <Detail label="Processor" value={data.processor} />
              <Detail label="Reconciliation" value={data.reconciliation_status} />
              <Detail label="Fee" value={formatCurrency(data.fee_amount, data.currency)} />
              <Detail label="Account" value={data.account_number_masked ?? '—'} />
              <Detail label="Scheduled" value={data.scheduled_for ? formatDate(data.scheduled_for) : '—'} />
              <Detail label="Completed" value={data.completed_at ? formatDateTime(data.completed_at) : '—'} />
            </dl>

            {data.failure_reason ? (
              <div className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm">
                <p className="font-medium">This payment failed</p>
                <p className="mt-0.5">{data.failure_reason}</p>
              </div>
            ) : null}

            <Card>
              <CardHeader>
                <CardTitle>Allocations</CardTitle>
                <CardDescription>
                  How this money has been matched to invoices.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {data.allocations.length === 0 ? (
                  <EmptyState
                    title="Nothing allocated"
                    description="An unallocated payment does not reduce any invoice balance."
                  />
                ) : (
                  <ul className="divide-y divide-border/60">
                    {data.allocations.map((allocation) => (
                      <li key={allocation.id} className="flex items-center justify-between gap-3 py-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {allocation.invoice_number ?? allocation.invoice_public_id ?? 'Invoice'}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {allocation.matched_by}
                            {allocation.confidence ? ` · confidence ${allocation.confidence}` : ''}
                          </p>
                        </div>
                        <span className="shrink-0 text-sm font-medium tabular">
                          {formatCurrency(allocation.amount, allocation.currency)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

                {remaining > 0 && can('payments.create') ? (
                  <div className="mt-4 space-y-3 border-t border-border pt-4">
                    <p className="text-sm font-semibold">Allocate to an invoice</p>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <Field label="Invoice ID" hint="An I… public id" required>
                        <Input
                          id="alloc-invoice"
                          value={invoiceId}
                          onChange={(event) => setInvoiceId(event.target.value.toUpperCase())}
                          className="font-mono text-xs"
                          placeholder="I01H8KM2Q"
                        />
                      </Field>
                      <Field
                        label={`Amount in ${data.currency}`}
                        required
                        error={error ?? undefined}
                        hint={`Up to ${formatCurrency(String(remaining), data.currency)}`}
                      >
                        <Input
                          id="alloc-amount"
                          type="number"
                          min="0"
                          max={remaining}
                          step="0.01"
                          value={amount}
                          onChange={(event) => setAmount(event.target.value)}
                        />
                      </Field>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setConfirming(true)}
                      disabled={!invoiceId || !amount}
                    >
                      Allocate
                    </Button>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>
        ) : null}
      </Dialog>

      <ConfirmOnlyDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Allocate this payment"
        description={
          invoiceId
            ? `This reduces the balance on invoice ${invoiceId} by ${formatCurrency(amount || '0', data?.currency ?? 'USD')} and is recorded as a manual match.`
            : 'Choose an invoice first.'
        }
        confirmLabel="Allocate"
        busy={allocate.isPending}
        error={allocate.isError ? allocate.error : null}
        onConfirm={() => {
          setError(null)
          allocate
            .mutateAsync()
            .catch((cause) => notifyError(cause, 'The allocation could not be applied.'))
        }}
      />
    </>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        {label}
      </dt>
      <dd className="truncate">{value}</dd>
    </div>
  )
}