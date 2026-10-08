'use client'

import * as React from 'react'
import Link from 'next/link'
import { ArrowLeftRight, Sparkles } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDateTime } from '@/lib/utils'
import type { BankAccount, BankTransaction, MatchCandidate } from '@/lib/domain-types'
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
} from '@/components/ui'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { FilterBar, FilterCheckbox, FilterInput, FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifySuccess } from '@/components/toast'

/**
 * Imported bank transactions.
 *
 * A negative amount is money leaving the account and a positive amount is money
 * arriving, which is the convention the API uses; the direction column states
 * that rather than leaving it to be inferred from the sign.
 */
export default function TransactionsPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [suggesting, setSuggesting] = React.useState<BankTransaction | null>(null)
  const [candidates, setCandidates] = React.useState<MatchCandidate[] | null>(null)

  const accounts = useCompanyQuery<BankAccount[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['bank', 'accounts'],
    path: '/bank-accounts',
  })

  const list = useCursorList<import('@/lib/domain-types').Page<BankTransaction>>({
    companyPublicId: activeCompanyPublicId,
    path: '/bank-transactions',
    queryKey: ['bank', 'transactions'],
    limit: 50,
  })

  const suggest = useCompanyMutation<MatchCandidate[], string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (transactionId) =>
      api.post<MatchCandidate[]>(
        `/reconciliation/bank-transactions/${transactionId}/suggest`,
        {},
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['bank', 'transactions']],
    onSuccess: (result) => {
      setCandidates(result)
      notifySuccess(
        'Suggestions ready.',
        `${result.length} candidate ${result.length === 1 ? 'invoice' : 'invoices'} considered.`,
      )
    },
  })

  const rows = list.query.data?.data ?? []

  const columns: Column<BankTransaction>[] = [
    {
      key: 'posted',
      header: 'Posted',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{formatDateTime(row.posted_at)}</p>
          <p className="truncate text-xs text-muted-foreground">
            {row.institution_name ?? ''}
            {row.account_number_masked ? ` · ${row.account_number_masked}` : ''}
          </p>
        </div>
      ),
    },
    {
      key: 'description',
      header: 'Description',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.merchant_name ?? row.normalized_description ?? row.description_raw ?? '—'}</p>
          {row.description_raw && row.normalized_description ? (
            <p className="truncate font-mono text-2xs text-subtle-foreground">{row.description_raw}</p>
          ) : null}
        </div>
      ),
    },
    {
      key: 'amount',
      header: 'Amount',
      numeric: true,
      cell: (row) => (
        <span className={Number(row.amount) < 0 ? 'text-danger' : 'text-success'}>
          {formatCurrency(row.amount, row.currency)}
        </span>
      ),
    },
    {
      key: 'direction',
      header: 'Direction',
      hideBelow: 'md',
      cell: (row) => (
        <Badge tone={row.direction === 'CREDIT' ? 'success' : 'neutral'}>
          {row.direction === 'CREDIT' ? 'In' : 'Out'}
        </Badge>
      ),
    },
    {
      key: 'match',
      header: 'Match',
      cell: (row) => <StatusBadge status={row.match_status} />,
    },
    {
      key: 'invoice',
      header: 'Invoice',
      hideBelow: 'lg',
      cell: (row) =>
        row.invoice_public_id ? (
          <PublicId value={row.invoice_public_id} kind="invoice" />
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
    {
      key: 'actions',
      header: '',
      hideBelow: 'md',
      cell: (row) =>
        Number(row.amount) < 0 && ['UNMATCHED', 'SUGGESTED'].includes(row.match_status) && can('reconciliation.read') ? (
          <Button
            size="xs"
            variant="outline"
            onClick={() => {
              setSuggesting(row)
              setCandidates(null)
              suggest.mutate(row.id)
            }}
            loading={suggesting?.id === row.id && suggest.isPending}
          >
            <Sparkles aria-hidden />
            Match
          </Button>
        ) : null,
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[
            { label: 'Payments', href: '/payments' },
            { label: 'Bank transactions' },
          ]}
          title="Bank transactions"
          description="Everything imported from your connected accounts. Money arriving is matched to invoices; money leaving is left for your own records."
        />

        <FilterBar activeCount={list.activeFilterCount} onClear={list.clearFilters}>
          <FilterSelect
            id="txn-account"
            label="Account"
            value={(list.filters.account_id as string) ?? ''}
            onChange={(value) => list.setFilter('account_id', value)}
            options={(accounts.data ?? []).map((account) => ({
              value: account.public_id,
              label: `${account.institution_name} ${account.account_number_masked}`,
            }))}
            className="w-64"
          />
          <FilterSelect
            id="txn-match-status"
            label="Match status"
            value={(list.filters.match_status as string) ?? ''}
            onChange={(value) => list.setFilter('match_status', value)}
            options={['UNMATCHED', 'SUGGESTED', 'MATCHED', 'PARTIALLY_MATCHED', 'IGNORED']}
            className="w-52"
          />
          <FilterInput
            id="txn-search"
            label="Search"
            value={(list.filters.q as string) ?? ''}
            onChange={(value) => list.setFilter('q', value)}
            placeholder="Description or merchant"
            className="min-w-48 flex-1"
          />
          <FilterCheckbox
            id="txn-unmatched"
            label="Unmatched only"
            checked={Boolean(list.filters.unmatched_only)}
            onChange={(checked) => list.setFilter('unmatched_only', checked)}
          />
        </FilterBar>

        {list.query.isPending ? (
          <LoadingBlock rows={10} />
        ) : list.query.isError ? (
          <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              caption="Imported bank transactions"
              exportName="bank-transactions"
              csv={[
                { header: 'Posted', value: (row) => row.posted_at },
                { header: 'Institution', value: (row) => row.institution_name },
                { header: 'Account', value: (row) => row.account_number_masked },
                { header: 'Description', value: (row) => row.description_raw },
                { header: 'Merchant', value: (row) => row.merchant_name },
                { header: 'Category', value: (row) => row.category },
                { header: 'Amount', value: (row) => row.amount },
                { header: 'Currency', value: (row) => row.currency },
                { header: 'Direction', value: (row) => row.direction },
                { header: 'Match status', value: (row) => row.match_status },
                { header: 'Invoice ID', value: (row) => row.invoice_public_id },
                { header: 'Invoice number', value: (row) => row.invoice_number },
              ]}
              emptyState={
                <EmptyState
                  icon={<ArrowLeftRight aria-hidden />}
                  title={
                    list.activeFilterCount > 0 ? 'No transactions match' : 'No transactions imported'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Clear the filters to see everything.'
                      : 'Connect a bank account and sync it to start importing.'
                  }
                  action={
                    list.activeFilterCount > 0 ? (
                      <Button variant="outline" onClick={list.clearFilters}>
                        Clear filters
                      </Button>
                    ) : (
                      <Link href="/payments/accounts">
                        <Button>Connect a bank</Button>
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
              noun="transactions"
            />
          </>
        )}
      </div>

      <Dialog
        open={Boolean(suggesting)}
        onOpenChange={(open) => {
          if (!open) {
            setSuggesting(null)
            setCandidates(null)
          }
        }}
        title="Match this transaction"
        description={
          suggesting
            ? `${formatCurrency(suggesting.amount, suggesting.currency)} · ${suggesting.merchant_name ?? suggesting.description_raw ?? 'no description'}`
            : undefined
        }
        className="max-w-3xl"
        footer={
          <Button
            variant="ghost"
            onClick={() => {
              setSuggesting(null)
              setCandidates(null)
            }}
          >
            Close
          </Button>
        }
      >
        {suggest.isPending ? (
          <LoadingBlock rows={4} />
        ) : suggest.isError ? (
          <ErrorState error={suggest.error} onRetry={() => suggest.mutate(suggesting?.id ?? "")} />
        ) : (candidates ?? []).length === 0 ? (
          <EmptyState
            title="No candidate invoices"
            description="Nothing you are authorised to read looks like a match. You can ignore this transaction instead."
          />
        ) : (
          <ul className="space-y-2">
            {(candidates ?? []).map((candidate) => (
              <li
                key={candidate.invoice_id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border p-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <PublicId value={candidate.invoice_id} kind="invoice" />
                    <StatusBadge status={candidate.invoice_status} />
                    <StatusBadge status={candidate.suggestion} />
                    <Badge tone="outline">
                      {Math.round(candidate.confidence * 100)}% confident
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm font-medium">
                    {candidate.invoice_number ?? 'Invoice'} ·{' '}
                    {formatCurrency(candidate.invoice_balance_due, candidate.invoice_currency)}
                  </p>
                  <p className="text-xs text-muted-foreground">{candidate.reason}</p>
                  {Object.keys(candidate.score_breakdown).length > 0 ? (
                    <p className="mt-0.5 font-mono text-2xs text-subtle-foreground">
                      {Object.entries(candidate.score_breakdown)
                        .map(([key, value]) => `${key}=${value}`)
                        .join('  ')}
                    </p>
                  ) : null}
                </div>
                {can('reconciliation.manage') ? (
                  candidate.suggestion === 'IGNORE' ? (
                    <Badge tone="neutral">Do not match</Badge>
                  ) : (
                    // Accepting a suggestion needs the reconciliation match id,
                    // which only the reconciliation queue holds. Linking there
                    // rather than inventing a match from this screen keeps the
                    // accept action on the one screen that can honour it.
                    <Link href={`/payments/reconciliation?transaction_id=${suggesting?.id ?? ''}`}>
                      <Button size="sm" variant="outline">
                        Review in reconciliation
                      </Button>
                    </Link>
                  )
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </Dialog>
    </PageShell>
  )
}
