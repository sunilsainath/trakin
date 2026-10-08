'use client'

import * as React from 'react'
import { CheckCircle2, Landmark, Link2, RefreshCw, Unlink } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDateTime } from '@/lib/utils'
import type { BankAccount, BankConnection } from '@/lib/domain-types'
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
} from '@/components/ui'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { ConfirmOnlyDialog, ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Bank connections and accounts.
 *
 * Connecting a bank is a two-step handshake: the server issues a link token,
 * the provider returns a public token in the browser, and that is exchanged for
 * accounts. The browser step belongs to the provider's own script, which is not
 * loaded here, so the exchange form is offered directly and labelled honestly
 * rather than pretending a redirect happened.
 */
export default function BankAccountsPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [syncing, setSyncing] = React.useState<string | null>(null)
  const [disconnecting, setDisconnecting] = React.useState<BankConnection | null>(null)
  const [verifying, setVerifying] = React.useState<BankAccount | null>(null)
  const [exchangeOpen, setExchangeOpen] = React.useState(false)

  const connections = useCompanyQuery<BankConnection[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['bank', 'connections'],
    path: '/bank-accounts/connections',
  })

  const accounts = useCompanyQuery<BankAccount[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['bank', 'accounts'],
    path: '/bank-accounts',
  })

  const linkToken = useCompanyMutation<{ link_token: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () => api.post<{ link_token: string }>('/bank-accounts/link-token', {}, { companyPublicId: activeCompanyPublicId }),
    onSuccess: () =>
      notifySuccess(
        'Link token created.',
        'Pass this to your provider SDK in the browser, then exchange the public token.',
      ),
  })

  const exchange = useCompanyMutation<BankConnection, string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (publicToken: string) =>
      api.post<BankConnection>(
        '/bank-accounts/connections',
        { public_token: publicToken },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['bank', 'connections'], ['bank', 'accounts']],
    onSuccess: () => {
      notifySuccess('Bank connected.')
      setExchangeOpen(false)
    },
  })

  const sync = useCompanyMutation<unknown, string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (connectionId) =>
      api.post(`/bank-accounts/connections/${connectionId}/sync`, {}, { companyPublicId: activeCompanyPublicId }),
    invalidate: [['bank', 'connections'], ['bank', 'accounts'], ['bank', 'transactions']],
    onSuccess: (_, connectionId) => {
      setSyncing(null)
      notifySuccess('Sync requested.', `Connection ${connectionId} is refreshing.`)
    },
  })

  const disconnect = useCompanyMutation<unknown, { id: string; reason: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ id, reason }) =>
      api.post(
        `/bank-accounts/connections/${id}/disconnect?reason=${encodeURIComponent(reason)}`,
        {},
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['bank', 'connections'], ['bank', 'accounts']],
    onSuccess: () => {
      notifySuccess('Bank disconnected.', 'Transactions already imported are retained.')
      setDisconnecting(null)
    },
  })

  const verify = useCompanyMutation<unknown, { id: string; verified: boolean }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ id, verified }) =>
      api.post(`/bank-accounts/${id}/verify`, { verified }, { companyPublicId: activeCompanyPublicId }),
    invalidate: [['bank', 'accounts']],
    onSuccess: (_, variables) => {
      notifySuccess(variables.verified ? 'Account marked verified.' : 'Verification withdrawn.')
      setVerifying(null)
    },
  })

  const accountColumns: Column<BankAccount>[] = [
    {
      key: 'account',
      header: 'Account',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.name ?? row.institution_name}</p>
          <p className="font-mono text-2xs text-subtle-foreground">{row.account_number_masked}</p>
        </div>
      ),
    },
    { key: 'institution', header: 'Institution', hideBelow: 'md', cell: (row) => row.institution_name },
    { key: 'type', header: 'Type', hideBelow: 'sm', cell: (row) => row.account_type },
    {
      key: 'balance',
      header: 'Balance',
      numeric: true,
      cell: (row) =>
        row.current_balance === null
          ? '—'
          : formatCurrency(row.current_balance, row.currency),
    },
    {
      key: 'available',
      header: 'Available',
      numeric: true,
      hideBelow: 'md',
      cell: (row) =>
        row.available_balance === null
          ? '—'
          : formatCurrency(row.available_balance, row.currency),
    },
    {
      key: 'unmatched',
      header: 'Unmatched',
      numeric: true,
      hideBelow: 'lg',
      cell: (row) => (
        <span className={row.unmatched_count > 0 ? 'font-medium text-warning' : undefined}>
          {row.unmatched_count}
        </span>
      ),
    },
    {
      key: 'verification',
      header: 'Verification',
      cell: (row) => <StatusBadge status={row.verification_state} />,
    },
    {
      key: 'actions',
      header: 'Actions',
      hideBelow: 'md',
      cell: (row) => (
        <div className="flex justify-end gap-1.5">
          {row.is_primary ? <Badge tone="primary">Primary</Badge> : null}
          {can('payments.connect_bank') ? (
            <Button
              size="xs"
              variant="outline"
              onClick={() => setVerifying(row)}
              disabled={row.verification_state === 'VERIFIED'}
            >
              <CheckCircle2 aria-hidden />
              {row.verification_state === 'VERIFIED' ? 'Verified' : 'Mark verified'}
            </Button>
          ) : null}
        </div>
      ),
    },
  ]

  const accountRows = accounts.data ?? []
  const cash = accountRows.reduce(
    (total, account) => total + Number(account.current_balance ?? '0'),
    0,
  )

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Payments', href: '/payments' }, { label: 'Bank accounts' }]}
          title="Bank accounts"
          description="Connected accounts, their balances, and which of them you have verified as yours."
          actions={
            can('payments.connect_bank') ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => linkToken.mutate()} loading={linkToken.isPending}>
                  <Link2 aria-hidden />
                  Create link token
                </Button>
                <Button onClick={() => setExchangeOpen(true)}>
                  <Landmark aria-hidden />
                  Connect a bank
                </Button>
              </div>
            ) : undefined
          }
        />

        {connections.isPending || accounts.isPending ? (
          <LoadingBlock rows={6} />
        ) : connections.isError ? (
          <ErrorState error={connections.error} onRetry={() => void connections.refetch()} />
        ) : accounts.isError ? (
          <ErrorState error={accounts.error} onRetry={() => void accounts.refetch()} />
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-3">
              <Metric label="Connected accounts" value={accountRows.length} />
              <Metric
                label="Cash across accounts"
                value={formatCurrency(String(cash), accountRows[0]?.currency ?? 'USD', {
                  compact: true,
                })}
              />
              <Metric
                label="Unmatched transactions"
                value={accountRows.reduce((total, account) => total + account.unmatched_count, 0)}
                tone={
                  accountRows.reduce((total, account) => total + account.unmatched_count, 0) > 0
                    ? 'warning'
                    : undefined
                }
              />
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Connections</CardTitle>
                <CardDescription>
                  Each connection is one institution. Disconnecting keeps transactions
                  that were already imported.
                </CardDescription>
              </CardHeader>
              <CardContent>
                {(connections.data ?? []).length === 0 ? (
                  <EmptyState
                    icon={<Landmark aria-hidden />}
                    title="No bank connected"
                    description="Connect an institution to import transactions and reconcile them against your invoices automatically."
                    action={
                      can('payments.connect_bank') ? (
                        <Button onClick={() => setExchangeOpen(true)}>Connect a bank</Button>
                      ) : undefined
                    }
                  />
                ) : (
                  <ul className="divide-y divide-border/60">
                    {(connections.data ?? []).map((connection) => (
                      <li key={connection.public_id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {connection.institution_name ?? connection.provider}
                          </p>
                          <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <PublicId value={connection.public_id} kind="company" />
                            <span>{connection.account_count} accounts</span>
                            <span>{connection.transaction_count} transactions</span>
                            {connection.last_synced_at ? (
                              <span>last sync {formatDateTime(connection.last_synced_at)}</span>
                            ) : (
                              <span className="text-warning">never synced</span>
                            )}
                          </p>
                        </div>
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge status={connection.status} />
                          <StatusBadge status={connection.verification_state} />
                          {can('payments.connect_bank') ? (
                            <>
                              <Button
                                size="xs"
                                variant="outline"
                                onClick={() => {
                                  setSyncing(connection.public_id)
                                  sync.mutate(connection.public_id)
                                }}
                                loading={syncing === connection.public_id}
                              >
                                <RefreshCw aria-hidden />
                                Sync
                              </Button>
                              <Button
                                size="xs"
                                variant="ghost"
                                className="text-danger hover:bg-danger-soft"
                                onClick={() => setDisconnecting(connection)}
                              >
                                <Unlink aria-hidden />
                                Disconnect
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Accounts</CardTitle>
                <CardDescription>
                  Verification is a claim you make about ownership. It is recorded
                  against your identity rather than inferred.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <DataTable
                  columns={accountColumns}
                  rows={accountRows}
                  rowKey={(row) => row.public_id}
                  caption="Connected bank accounts"
                  exportName="bank-accounts"
                  csv={[
                    { header: 'Account ID', value: (row) => row.public_id },
                    { header: 'Institution', value: (row) => row.institution_name },
                    { header: 'Name', value: (row) => row.name },
                    { header: 'Masked number', value: (row) => row.account_number_masked },
                    { header: 'Type', value: (row) => row.account_type },
                    { header: 'Currency', value: (row) => row.currency },
                    { header: 'Balance', value: (row) => row.current_balance },
                    { header: 'Available', value: (row) => row.available_balance },
                    { header: 'Verification', value: (row) => row.verification_state },
                    { header: 'Unmatched', value: (row) => row.unmatched_count },
                    { header: 'Last synced', value: (row) => row.last_synced_at },
                  ]}
                  emptyState={
                    <EmptyState
                      icon={<Landmark aria-hidden />}
                      title="No accounts yet"
                      description="Accounts appear here once a connection is exchanged."
                    />
                  }
                />
              </CardContent>
            </Card>
          </>
        )}
      </div>

      <ExchangeTokenDialog
        open={exchangeOpen}
        onOpenChange={setExchangeOpen}
        busy={exchange.isPending}
        error={exchange.isError ? exchange.error : null}
        onConfirm={async (token) => {
          try {
            await exchange.mutateAsync(token)
          } catch (cause) {
            notifyError(cause, 'The bank could not be connected.')
          }
        }}
      />

      <ReasonDialog
        open={Boolean(disconnecting)}
        onOpenChange={(open) => {
          if (!open) setDisconnecting(null)
        }}
        title="Disconnect this bank"
        description="Disconnecting stops future syncs. Accounts and transactions already imported are kept for reconciliation and audit."
        confirmLabel="Disconnect"
        label="Reason for disconnecting"
        busy={disconnect.isPending}
        error={disconnect.isError ? disconnect.error : null}
        onConfirm={(reason) => {
          if (!disconnecting) return
          disconnect
            .mutateAsync({ id: disconnecting.public_id, reason })
            .catch((cause) => notifyError(cause, 'The bank could not be disconnected.'))
        }}
      />

      <ConfirmOnlyDialog
        open={Boolean(verifying)}
        onOpenChange={(open) => {
          if (!open) setVerifying(null)
        }}
        title="Mark this account as verified"
        description={`You are asserting that ${verifying?.institution_name ?? 'this account'} ${verifying?.account_number_masked ?? ''} belongs to this company. The claim is recorded against your identity.`}
        confirmLabel="Mark verified"
        busy={verify.isPending}
        error={verify.isError ? verify.error : null}
        onConfirm={() => {
          if (!verifying) return
          verify
            .mutateAsync({ id: verifying.public_id, verified: true })
            .catch((cause) => notifyError(cause, 'The account could not be verified.'))
        }}
      />
    </PageShell>
  )
}

function ExchangeTokenDialog({
  open,
  onOpenChange,
  busy,
  error,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  busy: boolean
  error: unknown
  onConfirm: (publicToken: string) => Promise<void>
}) {
  const [token, setToken] = React.useState('')
  const [touched, setTouched] = React.useState(false)

  React.useEffect(() => {
    if (open) {
      setToken('')
      setTouched(false)
    }
  }, [open])

  const invalid = touched && token.trim().length < 10

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Connect a bank account"
      description="Create a link token, use your provider's browser SDK to get a public token back, then paste it here to exchange it for accounts."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            loading={busy}
            onClick={() => {
              setTouched(true)
              if (token.trim().length < 10) return
              void onConfirm(token.trim())
            }}
          >
            Exchange token
          </Button>
        </>
      }
    >
      <div className="space-y-1.5">
        <label htmlFor="public-token" className="block text-sm font-medium text-foreground">
          Public token<span className="ml-0.5 text-danger">*</span>
        </label>
        <textarea
          id="public-token"
          rows={3}
          value={token}
          onChange={(event) => setToken(event.target.value)}
          onBlur={() => setTouched(true)}
          disabled={busy}
          placeholder="public-sandbox-…"
          className="flex w-full resize-y rounded-md border border-input bg-surface px-3 py-2 font-mono text-xs leading-relaxed text-foreground placeholder:text-muted-foreground/80 transition-colors focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        />
        {invalid ? (
          <p role="alert" className="text-xs font-medium text-danger">
            The public token is at least 10 characters.
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            This token is exchanged server-side. It is not stored in the browser.
          </p>
        )}
        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {error instanceof Error ? error.message : 'That did not work.'}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}