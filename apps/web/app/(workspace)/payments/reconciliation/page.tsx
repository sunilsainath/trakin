'use client'

import * as React from 'react'
import { ArrowLeftRight, Check, ScanLine, X } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatPercent } from '@/lib/utils'
import type {
  MatchSuggestion,
  Page as PageEnvelope,
  ReconciliationSummary,
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
import { Field } from '@/components/forms'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, errorMessage, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * The reconciliation queue.
 *
 * Each row is a suggestion the server generated for one incoming credit. Nothing
 * is ever matched automatically here: accepting writes a real payment against a
 * real invoice, so the decision is confirmed and the confidence and reasons are
 * shown before it.
 */
export default function ReconciliationPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [decision, setDecision] = React.useState<
    { match: MatchSuggestion; accept: boolean } | null
  >(null)
  const [notes, setNotes] = React.useState('')
  const [createPayment, setCreatePayment] = React.useState(true)

  const summary = useCompanyQuery<ReconciliationSummary>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['reconciliation', 'summary'],
    path: '/reconciliation/summary',
  })

  const list = useCursorList<PageEnvelope<MatchSuggestion>>({
    companyPublicId: activeCompanyPublicId,
    path: '/reconciliation',
    queryKey: ['reconciliation', 'queue'],
    initialFilters: { status: 'SUGGESTED' },
  })

  const scan = useCompanyMutation<{ scanned: number; suggested: number }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () =>
      api.post<{ scanned: number; suggested: number }>(
        '/reconciliation/scan',
        {},
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['reconciliation', 'queue'], ['reconciliation', 'summary']],
    onSuccess: (result) =>
      notifySuccess(
        'Scan complete.',
        `${result.scanned} transactions examined, ${result.suggested} suggestions produced.`,
      ),
  })

  const decide = useCompanyMutation<
    { match_id: string; status: string; payment_public_id: string | null },
    { matchId: string; accept: boolean }
  >({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ matchId, accept }) =>
      api.post<{ match_id: string; status: string; payment_public_id: string | null }>(
        `/reconciliation/matches/${matchId}`,
        {
          decision: accept ? 'ACCEPTED' : 'REJECTED',
          create_payment: accept ? createPayment : false,
          ...(notes ? { notes } : {}),
        },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [
      ['reconciliation', 'queue'],
      ['reconciliation', 'summary'],
      ['payments', 'list'],
      ['invoices', 'list'],
      ['billing', 'receivables'],
      ['bank', 'transactions'],
    ],
    onSuccess: (result) => {
      setDecision(null)
      setNotes('')
      notifySuccess(
        result.status === 'ACCEPTED' ? 'Match accepted.' : 'Match rejected.',
        result.payment_public_id
          ? `Payment ${result.payment_public_id} recorded.`
          : 'No payment was created.',
      )
    },
  })

  const rows = list.query.data?.data ?? []

  const columns: Column<MatchSuggestion>[] = [
    {
      key: 'transaction',
      header: 'Transaction',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">
            {row.merchant_name ?? row.description_raw ?? 'Unidentified credit'}
          </p>
          <p className="truncate font-mono text-2xs text-subtle-foreground">
            {row.transaction_id}
          </p>
        </div>
      ),
    },
    { key: 'posted', header: 'Posted', hideBelow: 'sm', cell: (row) => formatDate(row.posted_at) },
    {
      key: 'amount',
      header: 'Received',
      numeric: true,
      cell: (row) => (
        <span className="text-success">{formatCurrency(row.txn_amount, row.txn_currency)}</span>
      ),
    },
    {
      key: 'invoice',
      header: 'Suggested invoice',
      cell: (row) => (
        <div className="min-w-0">
          <PublicId value={row.invoice_public_id} kind="invoice" />
          <p className="truncate text-xs text-muted-foreground">
            {row.invoice_number ?? 'Unnumbered'} ·{' '}
            {formatCurrency(row.balance_due, row.invoice_currency)} due
          </p>
        </div>
      ),
    },
    {
      key: 'confidence',
      header: 'Confidence',
      numeric: true,
      cell: (row) => (
        <Badge
          tone={Number(row.confidence) >= 0.9 ? 'success' : Number(row.confidence) >= 0.7 ? 'warning' : 'danger'}
        >
          {formatPercent(Number(row.confidence), 0)}
        </Badge>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      hideBelow: 'sm',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'actions',
      header: 'Actions',
      hideBelow: 'md',
      cell: (row) =>
        can('reconciliation.manage') ? (
          <div className="flex justify-end gap-1.5">
            <Button
              size="xs"
              variant="success"
              onClick={() => {
                setDecision({ match: row, accept: true })
                setNotes('')
                setCreatePayment(true)
              }}
            >
              <Check aria-hidden />
              Accept
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() => {
                setDecision({ match: row, accept: false })
                setNotes('')
              }}
            >
              <X aria-hidden />
              Reject
            </Button>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">Read only</span>
        ),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[
            { label: 'Payments', href: '/payments' },
            { label: 'Reconciliation' },
          ]}
          title="Reconciliation"
          description="Incoming money matched against your invoices. Nothing is matched automatically: every suggestion is a decision you make."
          actions={
            can('reconciliation.read') ? (
              <Button variant="outline" onClick={() => scan.mutate()} loading={scan.isPending}>
                <ScanLine aria-hidden />
                Scan unmatched credits
              </Button>
            ) : undefined
          }
        />

        {summary.isPending ? (
          <LoadingBlock rows={2} />
        ) : summary.isError ? (
          <ErrorState error={summary.error} onRetry={() => void summary.refetch()} />
        ) : summary.data ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <Metric label="Open transactions" value={summary.data.open_txns} />
            <Metric
              label="Unmatched inbound"
              value={formatCurrency(summary.data.unmatched_inbound, 'USD', { compact: true })}
              tone={Number(summary.data.unmatched_inbound) > 0 ? 'warning' : undefined}
            />
            <Metric label="Reconciled" value={summary.data.reconciled_count} tone="success" />
            <Metric
              label="Pending suggestions"
              value={summary.data.pending_suggestions}
              tone={summary.data.pending_suggestions > 0 ? 'warning' : undefined}
            />
            <Metric
              label="Unmatched total"
              value={formatCurrency(summary.data.unmatched_total, 'USD', { compact: true })}
            />
          </div>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ArrowLeftRight aria-hidden className="size-4 text-primary" />
              Suggestions
            </CardTitle>
            <CardDescription>
              The confidence is the server&apos;s own score. The reasons behind it are
              shown on each row so you can judge it rather than trust it.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-4">
              <FilterSelect
                id="reconciliation-status"
                label="Status"
                value={(list.filters.status as string) ?? ''}
                onChange={(value) => list.setFilter('status', value)}
                options={['SUGGESTED', 'ACCEPTED', 'REJECTED']}
                className="w-48"
              />
            </div>

            {list.query.isPending ? (
              <LoadingBlock rows={6} />
            ) : list.query.isError ? (
              <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
            ) : (
              <>
                <DataTable
                  columns={columns}
                  rows={rows}
                  rowKey={(row) => row.id}
                  caption="Reconciliation suggestions"
                  exportName="reconciliation"
                  csv={[
                    { header: 'Match ID', value: (row) => row.id },
                    { header: 'Transaction', value: (row) => row.transaction_id },
                    { header: 'Posted', value: (row) => row.posted_at },
                    { header: 'Description', value: (row) => row.description_raw },
                    { header: 'Amount received', value: (row) => row.txn_amount },
                    { header: 'Currency', value: (row) => row.txn_currency },
                    { header: 'Invoice ID', value: (row) => row.invoice_public_id },
                    { header: 'Invoice number', value: (row) => row.invoice_number },
                    { header: 'Balance due', value: (row) => row.balance_due },
                    { header: 'Confidence', value: (row) => row.confidence },
                    { header: 'Status', value: (row) => row.status },
                  ]}
                  emptyState={
                    <EmptyState
                      icon={<ArrowLeftRight aria-hidden />}
                      title={
                        (list.filters.status as string) === 'SUGGESTED'
                          ? 'Nothing waiting to be matched'
                          : 'No suggestions with that status'
                      }
                      description={
                        (list.filters.status as string) === 'SUGGESTED'
                          ? 'Scan your unmatched credits to generate suggestions from the amounts and references on each one.'
                          : 'Change the status filter to see the others.'
                      }
                      action={
                        (list.filters.status as string) === 'SUGGESTED' && can('reconciliation.read') ? (
                          <Button onClick={() => scan.mutate()} loading={scan.isPending}>
                            <ScanLine aria-hidden />
                            Scan now
                          </Button>
                        ) : (
                          <Button variant="outline" onClick={list.clearFilters}>
                            Clear filters
                          </Button>
                        )
                      }
                    />
                  }
                />

                <div className="mt-4">
                  <CursorFooter
                    meta={list.query.data?.meta}
                    count={rows.length}
                    onNext={list.next}
                    onPrevious={list.previous}
                    canGoBack={list.canGoBack}
                    busy={list.query.isFetching}
                    noun="suggestions"
                  />
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Dialog
        open={Boolean(decision)}
        onOpenChange={(open) => {
          if (!open) setDecision(null)
        }}
        title={decision?.accept ? 'Accept this match' : 'Reject this match'}
        description={
          decision
            ? decision.accept
              ? 'Accepting records a payment against this invoice, which reduces its balance and marks the transaction reconciled.'
              : 'Rejecting records that this is not the right invoice. The transaction stays unmatched and can be scanned again later.'
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setDecision(null)} disabled={decide.isPending}>
              Cancel
            </Button>
            <Button
              variant={decision?.accept ? 'success' : 'danger'}
              loading={decide.isPending}
              onClick={() => {
                if (!decision) return
                decide
                  .mutateAsync({ matchId: decision.match.id, accept: decision.accept })
                  .catch((cause) => notifyError(cause, 'The decision could not be recorded.'))
              }}
            >
              {decision?.accept ? 'Accept match' : 'Reject match'}
            </Button>
          </>
        }
      >
        {decision ? (
          <div className="space-y-4">
            <div className="rounded-md bg-surface-sunken p-3 text-sm">
              <dl className="space-y-1.5">
                <SummaryRow label="Received" value={formatCurrency(decision.match.txn_amount, decision.match.txn_currency)} />
                <SummaryRow
                  label="Reference"
                  value={decision.match.merchant_name ?? decision.match.description_raw ?? '—'}
                />
                <SummaryRow label="Posted" value={formatDate(decision.match.posted_at)} />
                <SummaryRow
                  label="Invoice"
                  value={`${decision.match.invoice_number ?? decision.match.invoice_public_id} · ${formatCurrency(decision.match.balance_due, decision.match.invoice_currency)} due`}
                />
                <SummaryRow
                  label="Confidence"
                  value={formatPercent(Number(decision.match.confidence), 0)}
                />
              </dl>
            </div>

            {decision.accept ? (
              <Field
                label="Also record a payment"
                hint="Turn this off to mark the transaction matched without creating a payment, for example when the money was already recorded by hand."
              >
                <Select
                  id="create-payment"
                  value={createPayment ? 'yes' : 'no'}
                  onChange={(event) => setCreatePayment(event.target.value === 'yes')}
                >
                  <option value="yes">Yes, record the payment</option>
                  <option value="no">No, only mark as matched</option>
                </Select>
              </Field>
            ) : null}

            <Field label="Notes" hint="Stored with the decision.">
              <Input value={notes} onChange={(event) => setNotes(event.target.value)} />
            </Field>

            {decide.isError ? (
              <p role="alert" className="text-xs font-medium text-danger">
                {errorMessage(decide.error)}
              </p>
            ) : null}
          </div>
        ) : null}
      </Dialog>
    </PageShell>
  )
}

/** One label/value pair in the decision summary. */
function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{value}</dd>
    </div>
  )
}
