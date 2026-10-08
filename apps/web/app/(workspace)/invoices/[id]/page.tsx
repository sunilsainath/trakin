'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  Ban,
  Check,
  CreditCard,
  FileWarning,
  History,
  ListChecks,
  Send,
  ShieldAlert,
  Undo2,
} from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/utils'
import type { Invoice } from '@/lib/domain-types'
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  EmptyState,
  Input,
  Skeleton,
  Tabs,
} from '@/components/ui'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { DecisionDialog, DialogField, ReasonDialog } from '@/components/destructive'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, errorMessage, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'
import { RecordPaymentDialog } from '../record-payment'

type InvoiceAction =
  | 'submit'
  | 'send'
  | 'dispute'
  | 'cancel'
  | 'credit_note'
  | 'approve_step'
  | 'reject_step'

/**
 * One invoice.
 *
 * The action buttons come from `allowed_transitions`, the list of statuses the
 * server will accept from here. Anything not in that list would be refused, so
 * it is not offered — including the ones the status enum alone would suggest.
 */
export default function InvoiceDetailPage() {
  const params = useParams<{ id: string }>()
  const invoiceId = params.id
  const { activeCompanyPublicId, can } = useCompany()
  const [tab, setTab] = React.useState('items')
  const [action, setAction] = React.useState<InvoiceAction | null>(null)
  const [stepNo, setStepNo] = React.useState<number | null>(null)

  const invoice = useCompanyQuery<Invoice>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['invoices', 'detail', invoiceId],
    path: `/invoices/${invoiceId}`,
  })

  const lifecycle = useCompanyMutation<Invoice, { path: string; body: Record<string, unknown> }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ path, body }) =>
      api.post<Invoice>(path, body, { companyPublicId: activeCompanyPublicId }),
    invalidate: [
      ['invoices', 'detail', invoiceId],
      ['invoices', 'list'],
      ['billing', 'receivables'],
    ],
    onSuccess: () => {
      setAction(null)
      notifySuccess('Invoice updated.')
    },
  })

  const approveStep = useCompanyMutation<Invoice, { step: number; decision: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ step, decision }) =>
      api.post<Invoice>(
        `/invoices/${invoiceId}/approvals/${step}?decision=${decision}`,
        {},
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['invoices', 'detail', invoiceId]],
    onSuccess: () => {
      setStepNo(null)
      notifySuccess('Approval recorded.')
    },
  })

  if (invoice.isPending) {
    return (
      <PageShell width="wide">
        <div className="space-y-4">
          <Skeleton className="h-4 w-64" />
          <Skeleton className="h-7 w-96" />
          <LoadingBlock rows={8} />
        </div>
      </PageShell>
    )
  }

  if (invoice.isError) {
    return (
      <PageShell>
        <div className="space-y-4">
          <ErrorState error={invoice.error} onRetry={() => void invoice.refetch()} />
          <Link href="/invoices" className="text-sm font-medium text-primary hover:underline">
            Back to invoices
          </Link>
        </div>
      </PageShell>
    )
  }

  const data = invoice.data
  const allowed = new Set(data.allowed_transitions.map((value) => value.toUpperCase()))
  const pendingSteps = data.approvals.filter(
    (step) => String(step.status ?? '').toUpperCase() === 'PENDING',
  )

  const call = async (path: string, body: Record<string, unknown> = {}) => {
    try {
      await lifecycle.mutateAsync({ path, body })
    } catch (cause) {
      notifyError(cause, 'That change could not be applied.')
    }
  }

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Invoices', href: '/invoices' }, { label: data.public_id, mono: true }]}
          title={data.invoice_number ?? 'Unnumbered invoice'}
          description={
            data.counterparty_company_name ??
            data.counterparty_user_name ??
            'No counterparty recorded on this invoice'
          }
          meta={<StatusBadge status={data.status} />}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <PublicId value={data.public_id} kind="invoice" size="lg" />

              {Number(data.balance_due) > 0 && can('payments.create') ? (
                <RecordPaymentDialog invoice={data} />
              ) : null}

              {allowed.has('SUBMITTED') && can('invoices.submit') ? (
                <Button size="sm" variant="outline" onClick={() => setAction('submit')}>
                  <ListChecks aria-hidden />
                  Submit
                </Button>
              ) : null}

              {allowed.has('APPROVED') && can('invoices.send') ? (
                <Button size="sm" variant="outline" onClick={() => setAction('send')}>
                  <Send aria-hidden />
                  Send
                </Button>
              ) : null}

              {allowed.has('DISPUTED') && can('invoices.cancel') ? (
                <Button size="sm" variant="outline" onClick={() => setAction('dispute')}>
                  <FileWarning aria-hidden />
                  Dispute
                </Button>
              ) : null}

              {allowed.has('CANCELLED') && can('invoices.cancel') ? (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-danger hover:bg-danger-soft"
                  onClick={() => setAction('cancel')}
                >
                  <Ban aria-hidden />
                  Cancel
                </Button>
              ) : null}

              {can('invoices.cancel') && Number(data.amount_paid) === 0 && data.status !== 'DRAFT' ? (
                <Button size="sm" variant="outline" onClick={() => setAction('credit_note')}>
                  <Undo2 aria-hidden />
                  Credit note
                </Button>
              ) : null}
            </div>
          }
        />

        {data.msa_required ? (
          <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning-soft p-3 text-sm">
            <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
            <div>
              <p className="font-medium">
                An active master service agreement is required before this invoice can
                be approved or sent.
              </p>
              {data.msa_block_reason ? (
                <p className="mt-0.5 text-muted-foreground">{data.msa_block_reason}</p>
              ) : null}
              <Link href="/msas" className="mt-1 inline-block font-medium text-primary hover:underline">
                Manage agreements
              </Link>
            </div>
          </div>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Subtotal"
            value={formatCurrency(data.subtotal, data.currency)}
            hint={`${data.item_count} ${data.item_count === 1 ? 'line' : 'lines'}`}
          />
          <Metric label="Tax" value={formatCurrency(data.tax_total, data.currency)} />
          <Metric
            label="Total"
            value={formatCurrency(data.total_amount, data.currency)}
          />
          <Metric
            label="Balance due"
            value={formatCurrency(data.balance_due, data.currency)}
            tone={Number(data.balance_due) > 0 ? 'warning' : 'success'}
            hint={`Paid ${formatCurrency(data.amount_paid, data.currency)}`}
          />
        </div>

        <Tabs
          tabs={[
            { key: 'items', label: 'Line items', badge: data.items.length },
            { key: 'allocations', label: 'Payments', badge: data.allocations.length },
            { key: 'approvals', label: 'Approvals', badge: data.approvals.length },
            { key: 'history', label: 'History', badge: data.history.length },
          ]}
          active={tab}
          onChange={setTab}
          className="overflow-x-auto scrollbar-thin"
        />

        {tab === 'items' ? <ItemsTab invoice={data} /> : null}
        {tab === 'allocations' ? <AllocationsTab invoice={data} /> : null}
        {tab === 'approvals' ? <ApprovalsTab invoice={data} pendingSteps={pendingSteps} onDecide={(step, decision) => {
          setStepNo(step)
          setAction(decision === 'APPROVED' ? 'approve_step' : 'reject_step')
        }} canApprove={can('invoices.approve')} /> : null}
        {tab === 'history' ? <HistoryTab invoice={data} /> : null}

        <InvoiceActionDialogs
          action={action}
          onClose={() => setAction(null)}
          busy={lifecycle.isPending}
          error={lifecycle.isError ? lifecycle.error : null}
          invoice={data}
          stepNo={stepNo}
          stepBusy={approveStep.isPending}
          onConfirm={call}
          onStep={async (decision) => {
            if (stepNo === null) return
            try {
              await approveStep.mutateAsync({ step: stepNo, decision })
            } catch (cause) {
              notifyError(cause, 'The approval could not be recorded.')
            }
          }}
        />
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Tabs                                                                       */
/* -------------------------------------------------------------------------- */

function ItemsTab({ invoice }: { invoice: Invoice }) {
  const columns: Column<Invoice['items'][number]>[] = [
    {
      key: 'description',
      header: 'Description',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.description}</p>
          {row.project_role_title ? (
            <p className="truncate text-xs text-muted-foreground">{row.project_role_title}</p>
          ) : null}
        </div>
      ),
    },
    { key: 'type', header: 'Type', hideBelow: 'md', cell: (row) => row.line_type },
    { key: 'quantity', header: 'Qty', numeric: true, cell: (row) => `${row.quantity} ${row.unit}` },
    {
      key: 'rate',
      header: 'Rate',
      numeric: true,
      cell: (row) => formatCurrency(row.unit_rate, row.currency),
    },
    {
      key: 'subtotal',
      header: 'Subtotal',
      numeric: true,
      cell: (row) => formatCurrency(row.subtotal, row.currency),
    },
    {
      key: 'tax',
      header: 'Tax',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => `${formatCurrency(row.tax_total, row.currency)} (${row.tax_rate})`,
    },
    {
      key: 'total',
      header: 'Total',
      numeric: true,
      cell: (row) => formatCurrency(row.total, row.currency),
    },
    {
      key: 'period',
      header: 'Service period',
      hideBelow: 'lg',
      cell: (row) =>
        row.service_period_start && row.service_period_end
          ? `${formatDate(row.service_period_start)} – ${formatDate(row.service_period_end)}`
          : '—',
    },
  ]

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Line items</CardTitle>
          <CardDescription>
            Every line was priced by the server from approved timesheets or contract
            rates. Nothing here can be typed in by hand.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={columns}
            rows={invoice.items}
            rowKey={(row) => row.id}
            caption="Line items on this invoice"
            exportName={`invoice-${invoice.public_id}-items`}
            csv={[
              { header: 'Description', value: (row) => row.description },
              { header: 'Line type', value: (row) => row.line_type },
              { header: 'Quantity', value: (row) => row.quantity },
              { header: 'Unit', value: (row) => row.unit },
              { header: 'Unit rate', value: (row) => row.unit_rate },
              { header: 'Subtotal', value: (row) => row.subtotal },
              { header: 'Tax rate', value: (row) => row.tax_rate },
              { header: 'Tax total', value: (row) => row.tax_total },
              { header: 'Total', value: (row) => row.total },
              { header: 'Currency', value: (row) => row.currency },
              { header: 'Role', value: (row) => row.project_role_title },
              { header: 'Role ID', value: (row) => row.project_role_id },
            ]}
            emptyState={
              <EmptyState
                title="No line items"
                description="An invoice with no lines has nothing to bill."
              />
            }
          />
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Commercial context</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3">
              <Row label="Direction" value={invoice.direction} />
              <Row label="Service period" value={`${formatDate(invoice.period_start)} – ${formatDate(invoice.period_end)}`} />
              <Row label="Issue date" value={invoice.issue_date ? formatDate(invoice.issue_date) : 'Not issued'} />
              <Row label="Due date" value={formatDate(invoice.due_date)} />
              <Row label="Payment terms" value={`${invoice.payment_terms_days} days`} />
              <Row label="Version" value={String(invoice.version)} />
              {invoice.locked ? <Row label="Locked" value="Yes" /> : null}
            </dl>
            {invoice.notes ? (
              <p className="mt-4 rounded-md bg-surface-sunken p-3 text-sm text-muted-foreground">
                {invoice.notes}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Linked records</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <LinkRow label="Contract" id={invoice.contract_id} name={invoice.contract_title} href={`/contracts/${invoice.contract_id}`} kind="contract" />
            {invoice.sow_id ? (
              <LinkRow label="Statement of work" id={invoice.sow_id} name={invoice.sow_title} href={`/sows/${invoice.sow_id}`} kind="sow" />
            ) : null}
            {invoice.project_id ? (
              <LinkRow label="Project" id={invoice.project_id} name={invoice.project_name} href={`/projects/${invoice.project_id}`} kind="project" />
            ) : null}
            {invoice.role_ids.length > 0 ? (
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">Roles billed</span>
                <span className="flex flex-wrap justify-end gap-1">
                  {invoice.role_ids.map((roleId) => (
                    <PublicId key={roleId} value={roleId} kind="role" />
                  ))}
                </span>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function AllocationsTab({ invoice }: { invoice: Invoice }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Payments against this invoice</CardTitle>
        <CardDescription>
          Every allocation of money to this invoice, and how the match was made.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {invoice.allocations.length === 0 ? (
          <EmptyState
            icon={<CreditCard aria-hidden />}
            title="Nothing allocated yet"
            description="A recorded payment is allocated to an invoice, which is what reduces the balance due."
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {invoice.allocations.map((allocation, index) => (
              <li key={String(allocation.id ?? index)} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {String(allocation.payment_public_id ?? allocation.public_id ?? 'Payment')}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {String(allocation.matched_by ?? 'MANUAL')}
                    {allocation.confidence ? ` · confidence ${String(allocation.confidence)}` : ''}
                    {allocation.confirmed_at
                      ? ` · ${formatDateTime(String(allocation.confirmed_at))}`
                      : ''}
                  </p>
                </div>
                <span className="shrink-0 text-sm font-medium tabular text-success">
                  {formatCurrency(String(allocation.amount ?? '0'), String(allocation.currency ?? invoice.currency))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function ApprovalsTab({
  invoice,
  pendingSteps,
  onDecide,
  canApprove,
}: {
  invoice: Invoice
  pendingSteps: Record<string, unknown>[]
  onDecide: (step: number, decision: 'APPROVED' | 'REJECTED') => void
  canApprove: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Approvals</CardTitle>
        <CardDescription>
          Each approval step is recorded against a named approver. The server refuses a
          step you requested yourself.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {pendingSteps.length > 0 && canApprove ? (
          <div className="rounded-md border border-primary/30 bg-primary-soft/50 p-3">
            <p className="text-sm font-medium">Waiting on your decision</p>
            <ul className="mt-2 space-y-2">
              {pendingSteps.map((step, index) => (
                <li key={index} className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm">
                    Step {String(step.step_no ?? index + 1)}
                    {step.name ? ` · ${String(step.name)}` : ''}
                  </span>
                  <span className="flex gap-2">
                    <Button
                      size="sm"
                      variant="success"
                      onClick={() => onDecide(Number(step.step_no ?? index + 1), 'APPROVED')}
                    >
                      <Check aria-hidden />
                      Approve
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onDecide(Number(step.step_no ?? index + 1), 'REJECTED')}
                    >
                      Reject
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {invoice.approvals.length === 0 ? (
          <EmptyState
            title="No approval steps"
            description="This invoice did not go through a multi-step approval flow."
          />
        ) : (
          <ol className="space-y-2">
            {invoice.approvals.map((step, index) => (
              <li key={index} className="rounded-md border border-border p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">
                    Step {String(step.step_no ?? index + 1)}
                    {step.name ? ` · ${String(step.name)}` : ''}
                  </p>
                  <StatusBadge status={String(step.status ?? 'PENDING')} />
                </div>
                {step.decided_at || step.notes ? (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {step.decided_at ? formatDateTime(String(step.decided_at)) : ''}
                    {step.notes ? ` · ${String(step.notes)}` : ''}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

function HistoryTab({ invoice }: { invoice: Invoice }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History aria-hidden className="size-4 text-primary" />
          History
        </CardTitle>
        <CardDescription>
          Every state change on this invoice, in order.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {invoice.history.length === 0 ? (
          <EmptyState title="No history recorded" description="Nothing has happened to this invoice yet." />
        ) : (
          <ol className="relative space-y-4 border-l border-border pl-4">
            {invoice.history.map((entry, index) => (
              <li key={index} className="relative">
                <span aria-hidden className="absolute -left-[21px] top-1.5 size-2 rounded-full bg-primary" />
                <p className="text-sm font-medium">
                  {String(entry.action ?? entry.status ?? 'Change')}
                </p>
                <p className="text-xs text-muted-foreground">
                  {entry.at || entry.created_at
                    ? formatDateTime(String(entry.at ?? entry.created_at))
                    : ''}
                  {entry.actor_name ? ` · ${String(entry.actor_name)}` : ''}
                </p>
                {entry.reason ? (
                  <p className="mt-0.5 text-sm">{String(entry.reason)}</p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Action dialogs                                                             */
/* -------------------------------------------------------------------------- */

function InvoiceActionDialogs({
  action,
  onClose,
  busy,
  error,
  invoice,
  stepNo,
  stepBusy,
  onConfirm,
  onStep,
}: {
  action: InvoiceAction | null
  onClose: () => void
  busy: boolean
  error: unknown
  invoice: Invoice
  stepNo: number | null
  stepBusy: boolean
  onConfirm: (path: string, body: Record<string, unknown>) => Promise<void>
  onStep: (decision: 'APPROVED' | 'REJECTED') => Promise<void>
}) {
  const close = (open: boolean) => {
    if (!open) onClose()
  }

  if (action === 'approve_step' || action === 'reject_step') {
    return (
      <DecisionDialog
        open
        onOpenChange={close}
        decision={action === 'approve_step' ? 'APPROVED' : 'REJECTED'}
        title={`${action === 'approve_step' ? 'Approve' : 'Reject'} approval step ${stepNo ?? ''}`}
        description={
          action === 'approve_step'
            ? 'Approving releases the invoice to be sent. It is recorded against your identity.'
            : 'Rejecting returns the invoice and stops it being sent.'
        }
        confirmLabel={action === 'approve_step' ? 'Approve invoice' : 'Reject invoice'}
        busy={stepBusy}
        error={error}
        onConfirm={async () => {
          await onStep(action === 'approve_step' ? 'APPROVED' : 'REJECTED')
        }}
      />
    )
  }

  if (action === 'cancel') {
    return (
      <ReasonDialog
        open
        onOpenChange={close}
        title="Cancel this invoice"
        description="Cancelling voids the invoice. It stays in the ledger for audit but can no longer be sent, paid or credited, and any allocation against it must be reversed first."
        confirmLabel="Cancel invoice"
        label="Reason for cancelling"
        busy={busy}
        error={error}
        onConfirm={(reason) =>
          onConfirm(`/invoices/${invoice.public_id}/cancel`, { reason })
        }
      />
    )
  }

  if (action === 'dispute') {
    return (
      <ReasonDialog
        open
        onOpenChange={close}
        title="Record a dispute"
        description="A dispute flags the invoice as contested. It is withdrawn from the receivables totals until it is resolved, so record it only when there is a genuine disagreement."
        confirmLabel="Record dispute"
        label="What is being disputed"
        busy={busy}
        error={error}
        onConfirm={(reason) =>
          onConfirm(`/invoices/${invoice.public_id}/dispute`, { reason })
        }
      />
    )
  }

  if (action === 'credit_note') {
    return (
      <CreditNoteDialog
        open
        onOpenChange={close}
        invoice={invoice}
        busy={busy}
        error={error}
        onConfirm={(amount, reason) =>
          onConfirm(`/invoices/${invoice.public_id}/credit-notes`, { amount, reason })
        }
      />
    )
  }

  const simple: Partial<Record<InvoiceAction, { title: string; description: string; path: string; confirm: string }>> =
    {
      submit: {
        title: 'Submit this invoice',
        description:
          'Submitting starts the approval flow. An active master service agreement is required before it can be approved.',
        path: `/invoices/${invoice.public_id}/submit`,
        confirm: 'Submit invoice',
      },
      send: {
        title: 'Send this invoice',
        description:
          'Sending issues the invoice to the counterparty and starts the payment clock from the issue date.',
        path: `/invoices/${invoice.public_id}/send`,
        confirm: 'Send invoice',
      },
    }

  const config = action ? simple[action] : undefined
  if (!config) return null

  return (
    <NoteDialogForInvoice
      open
      onOpenChange={close}
      title={config.title}
      description={config.description}
      confirmLabel={config.confirm}
      busy={busy}
      error={error}
      onConfirm={(notes) => onConfirm(config.path, notes ? { notes } : {})}
    />
  )
}

function NoteDialogForInvoice({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  busy,
  error,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  busy: boolean
  error: unknown
  onConfirm: (notes: string | null) => Promise<void>
}) {
  const [notes, setNotes] = React.useState('')

  React.useEffect(() => {
    setNotes('')
  }, [open, title])

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button loading={busy} onClick={() => onConfirm(notes.trim() || null)}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-1.5">
        <label htmlFor="invoice-note" className="block text-sm font-medium text-foreground">
          Note
        </label>
        <textarea
          id="invoice-note"
          rows={3}
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          disabled={busy}
          placeholder="Optional"
          className="flex w-full resize-y rounded-md border border-input bg-surface px-3 py-2 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground/80 transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        />
        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}

function CreditNoteDialog({
  open,
  onOpenChange,
  invoice,
  busy,
  error,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  invoice: Invoice
  busy: boolean
  error: unknown
  onConfirm: (amount: string, reason: string) => Promise<void>
}) {
  const [amount, setAmount] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [touched, setTouched] = React.useState(false)

  React.useEffect(() => {
    setAmount('')
    setReason('')
    setTouched(false)
  }, [open])

  const amountInvalid = touched && (!amount || Number(amount) <= 0)
  const reasonInvalid = touched && reason.trim().length < 3

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title="Issue a credit note"
      description="A credit note reduces what the counterparty owes on this invoice. The reason is stored with it and appears on the statement."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            loading={busy}
            onClick={() => {
              setTouched(true)
              if (!amount || Number(amount) <= 0 || reason.trim().length < 3) return
              void onConfirm(amount, reason.trim())
            }}
          >
            Issue credit note
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <DialogField
          id="credit-amount"
          label={`Amount in ${invoice.currency}`}
          required
          hint="Cannot exceed the invoice total."
          error={amountInvalid ? 'Enter an amount greater than zero.' : undefined}
        >
          <Input
            id="credit-amount"
            type="number"
            min="0"
            step="0.01"
            max={invoice.total_amount}
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            onBlur={() => setTouched(true)}
            disabled={busy}
          />
        </DialogField>

        <DialogField
          id="credit-reason"
          label="Reason"
          required
          hint="At least 3 characters. Shown to the counterparty."
          error={reasonInvalid ? 'Enter at least 3 characters.' : undefined}
        >
          <Input
            id="credit-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            onBlur={() => setTouched(true)}
            disabled={busy}
          />
        </DialogField>

        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 truncate text-right text-sm font-medium">{value}</dd>
    </div>
  )
}

function LinkRow({
  label,
  id,
  name,
  href,
  kind,
}: {
  label: string
  id: string | null
  name: string | null
  href?: string
  kind?: 'project' | 'sow' | 'contract'
}) {
  if (!id) return null
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      {name ? (
        <Link href={href ?? '#'} className="truncate text-sm font-medium hover:text-primary-strong">
          {name}
        </Link>
      ) : (
        <PublicId value={id} kind={kind} href={href} />
      )}
    </div>
  )
}