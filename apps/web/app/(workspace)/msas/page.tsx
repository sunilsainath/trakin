'use client'

import * as React from 'react'
import { Handshake, Plus, RefreshCw, Send } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { uploadDocument, useCompanyMutation } from '@/hooks/use-mutations'
import { formatDate, formatDateTime } from '@/lib/utils'
import type { Msa, Page as PageEnvelope } from '@/lib/domain-types'
import type { SearchHit } from '@/lib/types'
import { Button, Dialog, EmptyState, Input, Select } from '@/components/ui'
import { DateInput, Field } from '@/components/forms'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { OffsetFooter } from '@/components/list'
import { FilterBar, FilterNumber, FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, errorMessage, useCompanyQuery } from '@/components/query'
import { ConfirmOnlyDialog, DecisionDialog, ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Master service agreements.
 *
 * An MSA is the commercial framework a company needs before it can invoice a
 * counterparty, which is why its status matters beyond this screen: an invoice
 * for a company with no active MSA cannot be approved or sent.
 */
export default function MsasPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [offset, setOffset] = React.useState(0)
  const [status, setStatus] = React.useState('')
  const [expiring, setExpiring] = React.useState('')
  const [opening, setOpening] = React.useState(false)
  const [detail, setDetail] = React.useState<Msa | null>(null)
  const [action, setAction] = React.useState<
    | {
        kind:
          | 'request'
          | 'activate'
          | 'reject'
          | 'terminate'
          | 'renew'
          | 'review_accept'
          | 'review_reject'
        id: string
        label: string
        versionNo?: number
      }
    | null
  >(null)

  const limit = 50

  React.useEffect(() => {
    setOffset(0)
  }, [status, expiring])

  const params = React.useMemo(() => {
    const query = new URLSearchParams()
    query.set('limit', String(limit))
    query.set('offset', String(offset))
    if (status) query.set('status', status)
    if (expiring) query.set('expiring_within_days', expiring)
    return `?${query.toString()}`
  }, [status, expiring, offset])

  const msas = useCompanyQuery<PageEnvelope<Msa>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['msas', 'list', status, expiring, offset],
    path: '/msas',
    queryParams: params,
  })

  const transition = useCompanyMutation<
    Msa,
    { kind: 'activate' | 'reject' | 'terminate'; id: string; reason?: string }
  >({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ kind, id, reason }) =>
      api.post<Msa>(`/msas/${id}/${kind}`, reason ? { reason } : {}, { companyPublicId: activeCompanyPublicId }),
    invalidate: [['msas', 'list'], ['msas', 'detail']],
    onSuccess: () => {
      setAction(null)
      setDetail(null)
      notifySuccess('Agreement updated.')
    },
  })

  const request = useCompanyMutation<Msa, { id: string; message: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ id, message }) =>
      api.post<Msa>(`/msas/${id}/request`, message ? { message } : {}, { companyPublicId: activeCompanyPublicId }),
    invalidate: [['msas', 'list']],
    onSuccess: () => {
      setAction(null)
      notifySuccess('Request sent.', 'The counterparty has been asked to provide terms.')
    },
  })

  const review = useCompanyMutation<
    Msa,
    { id: string; versionNo: number; decision: 'ACCEPTED' | 'REJECTED'; notes?: string }
  >({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ id, versionNo, decision, notes }) =>
      api.post<Msa>(
        `/msas/${id}/versions/${versionNo}/review`,
        notes ? { decision, notes } : { decision },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['msas', 'list'], ['msas', 'detail']],
    onSuccess: () => {
      setAction(null)
      notifySuccess('Version reviewed.')
    },
  })

  const renew = useCompanyMutation<Msa, { id: string; from: string; to: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ id, from, to }) =>
      api.post<Msa>(
        `/msas/${id}/renew`,
        { effective_date: from, expiration_date: to },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['msas', 'list']],
    onSuccess: () => {
      setAction(null)
      setDetail(null)
      notifySuccess('Agreement renewed.')
    },
  })

  const rows = msas.data?.data ?? []

  const columns: Column<Msa>[] = [
    {
      key: 'parties',
      header: 'Parties',
      cell: (row) => (
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => setDetail(row)}
            className="block truncate text-left font-medium hover:text-primary-strong"
          >
            {row.company_a_name} &amp; {row.company_b_name}
          </button>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'term',
      header: 'Term',
      hideBelow: 'sm',
      cell: (row) =>
        row.effective_date
          ? `${formatDate(row.effective_date)} – ${row.expiration_date ? formatDate(row.expiration_date) : 'open'}`
          : '—',
    },
    {
      key: 'law',
      header: 'Governing law',
      hideBelow: 'lg',
      cell: (row) => row.governing_law ?? '—',
    },
    {
      key: 'terms',
      header: 'Payment terms',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => `${row.payment_terms_days}d`,
    },
    {
      key: 'renew',
      header: 'Auto renew',
      hideBelow: 'md',
      cell: (row) => (
        <span className="text-muted-foreground">
          {row.auto_renew ? `Yes · ${row.renewal_notice_days ?? 0}d notice` : 'No'}
        </span>
      ),
    },
    {
      key: 'versions',
      header: 'Versions',
      numeric: true,
      hideBelow: 'lg',
      cell: (row) => row.versions.length,
    },
  ]

  const activeFilters = (status ? 1 : 0) + (expiring ? 1 : 0)

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Agreements' }]}
          title="Master service agreements"
          description="The commercial framework with each counterparty. An active agreement is required before invoices to that company can be approved or sent."
          actions={
            can('msas.request') ? (
              <Button onClick={() => setOpening(true)}>
                <Plus aria-hidden />
                Open an agreement
              </Button>
            ) : undefined
          }
        />

        <FilterBar
          activeCount={activeFilters}
          onClear={() => {
            setStatus('')
            setExpiring('')
          }}
        >
          <FilterSelect
            id="msa-status"
            label="Status"
            value={status}
            onChange={setStatus}
            options={['NO_MSA', 'REQUESTED', 'UNDER_REVIEW', 'ACTIVE', 'REJECTED', 'EXPIRED', 'TERMINATED', 'WITHDRAWN']}
            className="w-52"
          />
          <FilterNumber
            id="msa-expiring"
            label="Expiring within (days)"
            value={expiring}
            onChange={setExpiring}
            placeholder="e.g. 60"
            step="1"
            className="w-48"
          />
        </FilterBar>

        {msas.isPending ? (
          <LoadingBlock rows={8} />
        ) : msas.isError ? (
          <ErrorState error={msas.error} onRetry={() => void msas.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Master service agreements"
              exportName="msas"
              csv={[
                { header: 'MSA ID', value: (row) => row.public_id },
                { header: 'Company A', value: (row) => row.company_a_name },
                { header: 'Company B', value: (row) => row.company_b_name },
                { header: 'Status', value: (row) => row.status },
                { header: 'Effective', value: (row) => row.effective_date },
                { header: 'Expires', value: (row) => row.expiration_date },
                { header: 'Governing law', value: (row) => row.governing_law },
                { header: 'Payment terms', value: (row) => row.payment_terms_days },
                { header: 'Auto renew', value: (row) => (row.auto_renew ? 'yes' : 'no') },
                { header: 'Versions', value: (row) => row.versions.length },
              ]}
              emptyState={
                <EmptyState
                  icon={<Handshake aria-hidden />}
                  title={activeFilters > 0 ? 'No agreements match' : 'No agreements yet'}
                  description={
                    activeFilters > 0
                      ? 'Clear the filters to see everything.'
                      : 'Open an agreement with a counterparty. Invoices to that company stay blocked until it is active.'
                  }
                  action={
                    activeFilters > 0 ? (
                      <Button
                        variant="outline"
                        onClick={() => {
                          setStatus('')
                          setExpiring('')
                        }}
                      >
                        Clear filters
                      </Button>
                    ) : can('msas.request') ? (
                      <Button onClick={() => setOpening(true)}>Open an agreement</Button>
                    ) : undefined
                  }
                />
              }
            />

            <OffsetFooter
              limit={limit}
              offset={offset}
              meta={msas.data?.meta}
              onOffsetChange={setOffset}
              busy={msas.isFetching}
            />
          </>
        )}
      </div>

      <OpenMsaDialog
        open={opening}
        onOpenChange={setOpening}
        companyPublicId={activeCompanyPublicId}
        onCreated={() => void msas.refetch()}
      />

      <MsaDetailDialog
        msa={detail}
        onClose={() => setDetail(null)}
        canRequest={can('msas.request')}
        canReview={can('msas.review')}
        canSubmit={can('msas.submit')}
        onAction={(kind, versionNo) =>
          setAction({ kind, id: detail?.public_id ?? '', label: detail ? `${detail.company_a_name} & ${detail.company_b_name}` : '', ...(versionNo ? { versionNo } : {}) })
        }
      />

      <ReasonDialog
        open={action?.kind === 'reject'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Reject this agreement"
        description="Rejecting records that the terms are not acceptable. A new request can be made later."
        confirmLabel="Reject"
        label="Reason"
        busy={transition.isPending}
        error={transition.isError ? transition.error : null}
        onConfirm={(reason) => {
          if (!action) return
          transition
            .mutateAsync({ kind: 'reject', id: action.id, reason })
            .catch((cause) => notifyError(cause, 'The agreement could not be rejected.'))
        }}
      />

      <ReasonDialog
        open={action?.kind === 'terminate'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Terminate this agreement"
        description="Terminating ends the framework. Invoices already raised under it are unaffected, but no new ones can be approved against it."
        confirmLabel="Terminate"
        label="Reason for terminating"
        busy={transition.isPending}
        error={transition.isError ? transition.error : null}
        onConfirm={(reason) => {
          if (!action) return
          transition
            .mutateAsync({ kind: 'terminate', id: action.id, reason })
            .catch((cause) => notifyError(cause, 'The agreement could not be terminated.'))
        }}
      />

      <ConfirmOnlyDialog
        open={action?.kind === 'activate'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Activate this agreement"
        description="Activating makes the framework live. Invoices to this counterparty can then be approved and sent."
        confirmLabel="Activate"
        busy={transition.isPending}
        error={transition.isError ? transition.error : null}
        onConfirm={() => {
          if (!action) return
          transition
            .mutateAsync({ kind: 'activate', id: action.id })
            .catch((cause) => notifyError(cause, 'The agreement could not be activated.'))
        }}
      />

      <RequestDialog
        open={action?.kind === 'request'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
          busy={request.isPending}
        error={request.isError ? request.error : null}
        onConfirm={(message) => {
          if (!action) return
          request
            .mutateAsync({ id: action.id, message })
            .catch((cause) => notifyError(cause, 'The request could not be sent.'))
        }}
      />

      <DecisionDialog
        open={action?.kind === 'review_accept' || action?.kind === 'review_reject'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        decision={action?.kind === 'review_accept' ? 'APPROVED' : 'REJECTED'}
        title={`${action?.kind === 'review_accept' ? 'Accept' : 'Reject'} version ${action?.versionNo ?? ''}`}
        description="Accepting a version makes it the current terms. Rejecting records why they were refused."
        confirmLabel={action?.kind === 'review_accept' ? 'Accept version' : 'Reject version'}
        busy={review.isPending}
        error={review.isError ? review.error : null}
        onConfirm={async (notes) => {
          if (!action?.versionNo) return
          try {
            await review.mutateAsync({
              id: action.id,
              versionNo: action.versionNo,
              decision: action.kind === 'review_accept' ? 'ACCEPTED' : 'REJECTED',
              ...(notes ? { notes } : {}),
            })
          } catch (cause) {
            notifyError(cause, 'The version could not be reviewed.')
          }
        }}
      />

      <RenewDialog
        open={action?.kind === 'renew'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
          busy={renew.isPending}
        error={renew.isError ? renew.error : null}
        onConfirm={(from, to) => {
          if (!action) return
          renew
            .mutateAsync({ id: action.id, from, to })
            .catch((cause) => notifyError(cause, 'The agreement could not be renewed.'))
        }}
      />
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Open an agreement                                                          */
/* -------------------------------------------------------------------------- */

function OpenMsaDialog({
  open,
  onOpenChange,
  companyPublicId,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  companyPublicId: string | null
  onCreated: () => void
}) {
  const [query, setQuery] = React.useState('')
  const [counterparty, setCounterparty] = React.useState('')
  const [counterpartyName, setCounterpartyName] = React.useState('')
  const [results, setResults] = React.useState<SearchHit[]>([])
  const [searching, setSearching] = React.useState(false)
  const [law, setLaw] = React.useState('')
  const [terms, setTerms] = React.useState('30')
  const [autoRenew, setAutoRenew] = React.useState('no')
  const [renewalNotice, setRenewalNotice] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open) {
      setQuery('')
      setCounterparty('')
      setCounterpartyName('')
      setResults([])
      setLaw('')
      setTerms('30')
      setAutoRenew('no')
      setRenewalNotice('')
      setNotes('')
      setError(null)
    }
  }, [open])

  // Counterparty discovery: any company on the platform, not just the ones
  // the caller already belongs to — otherwise an MSA with a new partner
  // could never be started.
  React.useEffect(() => {
    const term = query.trim()
    if (!open || term.length < 2) {
      setResults([])
      setSearching(false)
      return
    }
    setSearching(true)
    const timer = window.setTimeout(() => {
      void api
        .get<PageEnvelope<SearchHit>>(
          `/search?q=${encodeURIComponent(term)}&types=COMPANY&limit=10`,
          { companyPublicId },
        )
        .then((page) =>
          setResults(
            (page.data ?? []).filter((hit) => hit.public_id !== companyPublicId),
          ),
        )
        .catch(() => setResults([]))
        .finally(() => setSearching(false))
    }, 300)
    return () => window.clearTimeout(timer)
  }, [query, open, companyPublicId])

  const create = useCompanyMutation<Msa>({
    context: { companyPublicId },
    mutationFn: () =>
      api.post<Msa>(
        '/msas',
        {
          counterparty_company_id: counterparty,
          ...(law ? { governing_law: law } : {}),
          payment_terms_days: Number(terms),
          auto_renew: autoRenew === 'yes',
          ...(autoRenew === 'yes' && renewalNotice
            ? { renewal_notice_days: Number(renewalNotice) }
            : {}),
          ...(notes ? { notes } : {}),
        },
        { companyPublicId },
      ),
    invalidate: [['msas', 'list']],
    onSuccess: (msa) => {
      notifySuccess('Agreement opened.', `${msa.company_b_name} · ${msa.public_id}`)
      onOpenChange(false)
      onCreated()
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Open a master service agreement"
      description="This starts the framework with a counterparty. You can then ask them for terms, review what they send back, and activate it."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={create.isPending}>
            Cancel
          </Button>
          <Button
            loading={create.isPending}
            onClick={async () => {
              if (!counterparty) {
                setError('Choose the counterparty company.')
                return
              }
              if (autoRenew === 'yes' && !renewalNotice) {
                setError('An auto-renewing agreement needs a renewal notice period.')
                return
              }
              setError(null)
              try {
                await create.mutateAsync()
              } catch (cause) {
                notifyError(cause, 'The agreement could not be opened.')
              }
            }}
          >
            Open agreement
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="Counterparty"
          required
          error={error ?? undefined}
          hint="Search any company on the platform — not just the ones you belong to."
        >
          {counterparty ? (
            <div className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm">
              <span className="truncate font-medium">
                {counterpartyName || counterparty}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setCounterparty('')
                  setCounterpartyName('')
                  setQuery('')
                }}
              >
                Change
              </Button>
            </div>
          ) : (
            <>
              <Input
                id="msa-counterparty-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Type at least 2 letters of the company name…"
              />
              {searching ? (
                <p className="mt-1 text-xs text-muted-foreground">Searching…</p>
              ) : query.trim().length >= 2 && results.length === 0 ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  No companies match that search.
                </p>
              ) : null}
              {results.length > 0 ? (
                <ul className="mt-2 divide-y divide-border/60 rounded-md border border-border">
                  {results.map((hit) => (
                    <li key={hit.public_id}>
                      <button
                        type="button"
                        onClick={() => {
                          setCounterparty(hit.public_id)
                          setCounterpartyName(hit.title)
                          setResults([])
                        }}
                        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                      >
                        <span className="truncate font-medium">{hit.title}</span>
                        <span className="shrink-0 font-mono text-2xs text-subtle-foreground">
                          {hit.public_id}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          )}
        </Field>

        <Field label="Governing law">
          <Input
            id="msa-law"
            value={law}
            onChange={(event) => setLaw(event.target.value)}
            placeholder="State of New York"
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Payment terms (days)">
            <Input
              id="msa-terms"
              type="number"
              min="0"
              max="365"
              value={terms}
              onChange={(event) => setTerms(event.target.value)}
            />
          </Field>
          <Field label="Auto renew">
            <Select id="msa-auto-renew" value={autoRenew} onChange={(event) => setAutoRenew(event.target.value)}>
              <option value="no">No, it expires</option>
              <option value="yes">Yes, unless cancelled</option>
            </Select>
          </Field>
        </div>

        {autoRenew === 'yes' ? (
          <Field label="Renewal notice (days)" required>
            <Input
              id="msa-renewal-notice"
              type="number"
              min="1"
              max="365"
              value={renewalNotice}
              onChange={(event) => setRenewalNotice(event.target.value)}
            />
          </Field>
        ) : null}

        <Field label="Notes">
          <Input id="msa-notes" value={notes} onChange={(event) => setNotes(event.target.value)} />
        </Field>
      </div>
    </Dialog>
  )
}

/* -------------------------------------------------------------------------- */
/* Detail                                                                     */
/* -------------------------------------------------------------------------- */

function MsaDetailDialog({
  msa,
  onClose,
  canRequest,
  canReview,
  canSubmit,
  onAction,
}: {
  msa: Msa | null
  onClose: () => void
  canRequest: boolean
  canReview: boolean
  canSubmit: boolean
  onAction: (kind: 'request' | 'activate' | 'reject' | 'terminate' | 'renew' | 'review_accept' | 'review_reject', versionNo?: number) => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [submitting, setSubmitting] = React.useState(false)

  const detail = useCompanyQuery<Msa>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['msas', 'detail', msa?.public_id],
    path: `/msas/${msa?.public_id ?? ''}`,
    enabled: Boolean(msa),
  })

  const data = msa ? (detail.data ?? msa) : null
  const allowed = new Set((data?.allowed_transitions ?? []).map((value) => value.toUpperCase()))

  return (
    <Dialog
      open={Boolean(msa)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={data ? `${data.company_a_name} & ${data.company_b_name}` : 'Agreement'}
      description={data ? `Agreement ${data.public_id}` : undefined}
      className="max-w-3xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {data && allowed.has('REQUESTED') && canRequest ? (
            <Button variant="outline" onClick={() => onAction('request')}>
              <Send aria-hidden />
              Ask for terms
            </Button>
          ) : null}
          {data && canSubmit ? (
            <Button variant="outline" onClick={() => setSubmitting(true)}>
              Submit terms
            </Button>
          ) : null}
          {data && allowed.has('ACTIVE') && canReview ? (
            <Button onClick={() => onAction('activate')}>Activate</Button>
          ) : null}
          {data && ['ACTIVE', 'EXPIRED', 'TERMINATED'].includes(data.status) && canReview ? (
            <>
              <Button variant="outline" onClick={() => onAction('renew')}>
                <RefreshCw aria-hidden />
                Renew
              </Button>
              {allowed.has('TERMINATED') ? (
                <Button
                  variant="ghost"
                  className="text-danger hover:bg-danger-soft"
                  onClick={() => onAction('terminate')}
                >
                  Terminate
                </Button>
              ) : null}
            </>
          ) : null}
          {data && allowed.has('REJECTED') && canReview ? (
            <Button variant="outline" onClick={() => onAction('reject')}>
              Reject
            </Button>
          ) : null}
        </>
      }
    >
      {detail.isPending ? (
        <LoadingBlock rows={6} />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : data ? (
        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={data.status} />
            <PublicId value={data.public_id} />
          </div>

          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <Detail label="Party A" value={data.company_a_name} />
            <Detail label="Party B" value={data.company_b_name} />
            <Detail
              label="Effective"
              value={data.effective_date ? formatDate(data.effective_date) : 'Not yet effective'}
            />
            <Detail
              label="Expires"
              value={data.expiration_date ? formatDate(data.expiration_date) : 'No expiry'}
            />
            <Detail label="Governing law" value={data.governing_law ?? '—'} />
            <Detail label="Payment terms" value={`${data.payment_terms_days} days`} />
            <Detail
              label="Auto renew"
              value={
                data.auto_renew ? `Yes · ${data.renewal_notice_days ?? 0} days notice` : 'No'
              }
            />
            <Detail
              label="Activated"
              value={data.activated_at ? formatDateTime(data.activated_at) : '—'}
            />
          </dl>

          {data.notes ? (
            <p className="rounded-md bg-surface-sunken p-3 text-sm text-muted-foreground">
              {data.notes}
            </p>
          ) : null}

          <section>
            <h3 className="text-sm font-semibold">Versions</h3>
            {data.versions.length === 0 ? (
              <p className="mt-1 text-sm text-muted-foreground">
                No versions have been submitted. Ask the counterparty for terms to start.
              </p>
            ) : (
              <ul className="mt-2 divide-y divide-border/60">
                {data.versions.map((version) => (
                  <li key={version.version_no} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs">v{version.version_no}</span>
                        <StatusBadge status={version.status} />
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {version.effective_date ? formatDate(version.effective_date) : 'No effective date'}
                        {version.expiration_date ? ` – ${formatDate(version.expiration_date)}` : ''}
                      </p>
                      {version.review_notes ? (
                        <p className="text-xs text-muted-foreground">{version.review_notes}</p>
                      ) : null}
                    </div>
                    {canReview && ['SUBMITTED', 'UNDER_REVIEW'].includes(version.status) ? (
                      <div className="flex gap-1.5">
                        <Button
                          size="xs"
                          variant="success"
                          onClick={() => onAction('review_accept', version.version_no)}
                        >
                          Accept
                        </Button>
                        <Button
                          size="xs"
                          variant="outline"
                          onClick={() => onAction('review_reject', version.version_no)}
                        >
                          Reject
                        </Button>
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {data.requests.length > 0 ? (
            <section>
              <h3 className="text-sm font-semibold">Requests</h3>
              <ul className="mt-2 divide-y divide-border/60">
                {data.requests.map((request, index) => (
                  <li key={index} className="py-2 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span>
                        {request.requester_company_name} → {request.target_company_name}
                      </span>
                      <StatusBadge status={request.status} />
                    </div>
                    {request.message ? (
                      <p className="text-xs text-muted-foreground">{request.message}</p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
      {data ? (
        <SubmitTermsDialog
          open={submitting}
          onOpenChange={setSubmitting}
          msaId={data.public_id}
          companyPublicId={activeCompanyPublicId}
          onSubmitted={() => {
            setSubmitting(false)
            void detail.refetch()
          }}
        />
      ) : null}
    </Dialog>
  )
}

/**
 * Submit terms for review: effective/expiration dates plus an optional signed
 * MSA scan. The version starts as a draft the counterparty accepts or rejects;
 * activation additionally requires an accepted version carrying both dates.
 */
function SubmitTermsDialog({
  open,
  onOpenChange,
  msaId,
  companyPublicId,
  onSubmitted,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  msaId: string
  companyPublicId: string | null
  onSubmitted: () => void
}) {
  const [effective, setEffective] = React.useState('')
  const [expiration, setExpiration] = React.useState('')
  const [file, setFile] = React.useState<File | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    if (open) {
      setEffective('')
      setExpiration('')
      setFile(null)
      setError(null)
      setBusy(false)
    }
  }, [open ])

  const submit = async () => {
    if (effective && expiration && expiration < effective) {
      setError('The expiration date cannot precede the effective date.')
      return
    }
    setError(null)
    setBusy(true)
    try {
      let documentId: string | undefined
      if (file) {
        const uploaded = await uploadDocument({
          companyPublicId,
          file,
          title: file.name,
          docType: 'MSA',
          relatedType: 'MSA',
          relatedPublicId: msaId,
        })
        documentId = uploaded.public_id
      }
      await api.post<Msa>(
        `/msas/${msaId}/versions`,
        {
          ...(effective ? { effective_date: effective } : {}),
          ...(expiration ? { expiration_date: expiration } : {}),
          ...(documentId ? { document_id: documentId } : {}),
        },
        { companyPublicId },
      )
      notifySuccess('Terms submitted.', 'The counterparty can now review this version.')
      onSubmitted()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The version could not be submitted.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Submit terms for review"
      description="Upload the signed MSA scan and set its term. The version is reviewable immediately; activation still needs an accepted version with both dates."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button loading={busy} onClick={() => void submit()}>
            Submit version
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Effective date">
            <DateInput id="msa-version-effective" value={effective} onChange={setEffective} />
          </Field>
          <Field label="Expiration date">
            <DateInput id="msa-version-expiration" value={expiration} onChange={setExpiration} />
          </Field>
        </div>
        <Field label="Signed MSA scan (optional)" hint="PDF or image. Stored as a company document linked to this agreement.">
          <input
            type="file"
            accept="application/pdf,image/*"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="text-sm"
            aria-label="Signed MSA file"
          />
          {file ? <p className="mt-1 text-xs text-muted-foreground">Attached: {file.name}</p> : null}
        </Field>
      </div>
    </Dialog>
  )
}

function RequestDialog({
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
  onConfirm: (message: string) => void
}) {
  const [message, setMessage] = React.useState('')

  React.useEffect(() => {
    if (open) setMessage('')
  }, [open])

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Ask the counterparty for terms"
      description="This sends a request asking them to submit their standard terms. Nothing is agreed until a version is accepted and activated."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button loading={busy} onClick={() => onConfirm(message.trim())}>
            Send request
          </Button>
        </>
      }
    >
      <div className="space-y-1.5">
        <label htmlFor="msa-request-message" className="block text-sm font-medium text-foreground">
          Message
        </label>
        <textarea
          id="msa-request-message"
          rows={3}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          disabled={busy}
          placeholder="Please send over your standard master service agreement so we can start billing."
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

function RenewDialog({
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
  onConfirm: (from: string, to: string) => void
}) {
  const [from, setFrom] = React.useState('')
  const [to, setTo] = React.useState('')
  const [touched, setTouched] = React.useState(false)

  React.useEffect(() => {
    if (open) {
      setFrom('')
      setTo('')
      setTouched(false)
    }
  }, [open])

  const invalid = touched && (!from || !to || to < from)

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Renew this agreement"
      description="Sets a new effective and expiry date. The previous term is retained in the version history."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            loading={busy}
            onClick={() => {
              setTouched(true)
              if (!from || !to || to < from) return
              onConfirm(from, to)
            }}
          >
            Renew
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Effective date" required>
            <Input id="msa-renew-from" type="date" value={from} onChange={(event) => setFrom(event.target.value)} onBlur={() => setTouched(true)} disabled={busy} />
          </Field>
          <Field label="Expiry date" required>
            <Input id="msa-renew-to" type="date" value={to} onChange={(event) => setTo(event.target.value)} onBlur={() => setTouched(true)} disabled={busy} />
          </Field>
        </div>
        {invalid ? (
          <p role="alert" className="text-xs font-medium text-danger">
            Both dates are required, and the expiry cannot precede the effective date.
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
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