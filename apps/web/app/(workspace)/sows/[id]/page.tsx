'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { Ban, Check, CheckCircle2, History, ListChecks } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/utils'
import type { Sow, VersionRow } from '@/lib/domain-types'
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  Skeleton,
  Tabs,
} from '@/components/ui'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { ConfirmOnlyDialog, DecisionDialog, ReasonDialog } from '@/components/destructive'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * One statement of work.
 *
 * The four lifecycle actions (submit, approve, reject, terminate) are offered from
 * the server's own state rather than guessed from the status enum, because the
 * server also enforces approval-step and segregation-of-duties rules that the
 * client cannot see.
 */
export default function SowDetailPage() {
  const params = useParams<{ id: string }>()
  const sowId = params.id
  const { activeCompanyPublicId, can } = useCompany()
  const [tab, setTab] = React.useState('scope')

  const sow = useCompanyQuery<Sow>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['sows', 'detail', sowId],
    path: `/sows/${sowId}`,
  })

  const versions = useCompanyQuery<VersionRow[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['sows', 'versions', sowId],
    path: `/sows/${sowId}/versions`,
    enabled: tab === 'history',
  })

  const [dialog, setDialog] = React.useState<
    'submit' | 'approve' | 'reject' | 'reopen' | 'terminate' | null
  >(null)

  const act = useCompanyMutation<Sow, { path: string; reason: string | null }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ path, reason }) =>
      api.post<Sow>(path, reason ? { reason } : {}, { companyPublicId: activeCompanyPublicId }),
    invalidate: [
      ['sows', 'detail', sowId],
      ['sows', 'list'],
      ['projects', 'detail'],
    ],
    onSuccess: () => {
      setDialog(null)
      notifySuccess('Statement of work updated.')
    },
  })

  if (sow.isPending) {
    return (
      <PageShell>
        <div className="space-y-4">
          <Skeleton className="h-4 w-56" />
          <Skeleton className="h-7 w-80" />
          <LoadingBlock rows={8} />
        </div>
      </PageShell>
    )
  }

  if (sow.isError) {
    return (
      <PageShell>
        <div className="space-y-4">
          <ErrorState error={sow.error} onRetry={() => void sow.refetch()} />
          <Link href="/sows" className="text-sm font-medium text-primary hover:underline">
            Back to statements of work
          </Link>
        </div>
      </PageShell>
    )
  }

  const data = sow.data

  const submit = async () => {
    try {
      await act.mutateAsync({ path: `/sows/${sowId}/submit`, reason: null })
    } catch (cause) {
      notifyError(cause, 'The SOW could not be submitted.')
    }
  }

  const approve = (notes: string | null) =>
    act
      .mutateAsync({ path: `/sows/${sowId}/approve`, reason: notes })
      .catch((cause) => notifyError(cause, 'The SOW could not be approved.'))

  const reject = (notes: string | null) =>
    act
      .mutateAsync({ path: `/sows/${sowId}/reject`, reason: notes })
      .catch((cause) => notifyError(cause, 'The SOW could not be rejected.'))

  const reopen = async () => {
    try {
      await act.mutateAsync({ path: `/sows/${sowId}/reopen`, reason: null })
    } catch (cause) {
      notifyError(cause, 'The SOW could not be reopened.')
    }
  }

  const terminate = (reason: string) =>
    act
      .mutateAsync({ path: `/sows/${sowId}/terminate`, reason })
      .catch((cause) => notifyError(cause, 'The SOW could not be terminated.'))

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[
            { label: 'Statements of Work', href: '/sows' },
            { label: data.public_id, mono: true },
          ]}
          title={data.title}
          description={data.description ?? undefined}
          meta={<StatusBadge status={data.status} />}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <PublicId value={data.public_id} kind="sow" size="lg" />
              {can('sows.update') && data.status === 'DRAFT' ? (
                <Button variant="outline" size="sm" onClick={() => setDialog('submit')} loading={act.isPending}>
                  Submit for approval
                </Button>
              ) : null}
              {can('sows.approve') && data.status === 'PENDING_APPROVAL' ? (
                <>
                  <Button
                    variant="success"
                    size="sm"
                    onClick={() => setDialog('approve')}
                    loading={act.isPending}
                  >
                    <Check aria-hidden />
                    Approve
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setDialog('reject')}>
                    Reject
                  </Button>
                </>
              ) : null}
              {can('sows.update') && data.status === 'REJECTED' ? (
                <Button variant="outline" size="sm" onClick={() => setDialog('reopen')}>
                  Reopen as draft
                </Button>
              ) : null}
              {can('sows.approve') && !['TERMINATED', 'CLOSED'].includes(data.status) ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-danger hover:bg-danger-soft"
                  onClick={() => setDialog('terminate')}
                >
                  <Ban aria-hidden />
                  Terminate
                </Button>
              ) : null}
            </div>
          }
        />

        <Tabs
          tabs={[
            { key: 'scope', label: 'Scope' },
            { key: 'roles', label: 'Roles', badge: data.roles.length },
            { key: 'contracts', label: 'Contracts', badge: data.contract_count },
            { key: 'history', label: 'History' },
          ]}
          active={tab}
          onChange={setTab}
          className="overflow-x-auto scrollbar-thin"
        />

        {tab === 'scope' ? <ScopeTab sow={data} /> : null}
        {tab === 'roles' ? <RolesTab sow={data} /> : null}
        {tab === 'contracts' ? <ContractsTab sow={data} /> : null}
        {tab === 'history' ? <HistoryTab sow={data} versions={versions} /> : null}

        <ConfirmOnlyDialog
          open={dialog === 'submit'}
          onOpenChange={(open) => setDialog(open ? 'submit' : null)}
          title="Submit for approval"
          description="Submitting sends this SOW to an approver. You will not be able to edit its scope or rates while it is pending."
          confirmLabel="Submit for approval"
          busy={act.isPending}
          error={act.isError ? act.error : null}
          onConfirm={submit}
        />

        <DecisionDialog
          open={dialog === 'approve'}
          onOpenChange={(open) => setDialog(open ? 'approve' : null)}
          decision="APPROVED"
          title="Approve this statement of work"
          description="Approving makes the SOW active and allows contracts to be generated from it. The decision is recorded against your identity."
          confirmLabel="Approve SOW"
          busy={act.isPending}
          error={act.isError ? act.error : null}
          onConfirm={approve}
        />

        <DecisionDialog
          open={dialog === 'reject'}
          onOpenChange={(open) => setDialog(open ? 'reject' : null)}
          decision="REJECTED"
          title="Reject this statement of work"
          description="The SOW is kept with who rejected it, when, and why. Reopen it as a draft later to revise and resubmit."
          confirmLabel="Reject SOW"
          notesLabel="Reason for rejection"
          busy={act.isPending}
          error={act.isError ? act.error : null}
          onConfirm={reject}
        />

        <ConfirmOnlyDialog
          open={dialog === 'reopen'}
          onOpenChange={(open) => setDialog(open ? 'reopen' : null)}
          title="Reopen as draft"
          description="The rejected SOW returns to draft so it can be revised and resubmitted. Its rejection stays in history."
          confirmLabel="Reopen"
          busy={act.isPending}
          error={act.isError ? act.error : null}
          onConfirm={reopen}
        />

        <ReasonDialog
          open={dialog === 'terminate'}
          onOpenChange={(open) => setDialog(open ? 'terminate' : null)}
          title="Terminate this statement of work"
          description="Terminating ends the commercial scope. Contracts already generated from it are not cancelled automatically, so you will still need to close them."
          confirmLabel="Terminate SOW"
          label="Reason for terminating"
          busy={act.isPending}
          error={act.isError ? act.error : null}
          onConfirm={terminate}
        />
      </div>
    </PageShell>
  )
}

function ScopeTab({ sow }: { sow: Sow }) {
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ListChecks aria-hidden className="size-4 text-primary" />
            Scope and terms
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <Detail label="Project" value={sow.project_name ?? sow.project_id} mono={!sow.project_name} />
            <Detail label="Type" value={sow.sow_type} />
            <Detail
              label="Counterparty"
              value={
                sow.counterparty_company_name ??
                sow.counterparty_user_name ??
                sow.counterparty_company_id ??
                sow.counterparty_user_id ??
                '—'
              }
              mono={!sow.counterparty_company_name && !sow.counterparty_user_name}
            />
            <Detail label="Start" value={sow.start_date ? formatDate(sow.start_date) : '—'} />
            <Detail label="End" value={sow.end_date ? formatDate(sow.end_date) : '—'} />
            <Detail label="Billing basis" value={sow.billing_basis} />
            <Detail label="Billing frequency" value={sow.billing_frequency} />
            <Detail label="Invoice frequency" value={sow.invoice_frequency} />
            <Detail label="Payment terms" value={`${sow.payment_terms_days} days`} />
            <Detail label="Payment method" value={sow.payment_method ?? '—'} />
            <Detail
              label="Capped total"
              value={
                sow.max_total_amount
                  ? `${formatCurrency(sow.max_total_amount, sow.currency)} ${sow.currency}`
                  : 'Not capped'
              }
            />
            <Detail
              label="Approved"
              value={sow.approved_at ? formatDateTime(sow.approved_at) : 'Not yet'}
            />
          </dl>

          {sow.scope ? (
            <section>
              <h3 className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
                Scope
              </h3>
              <p className="mt-1.5 whitespace-pre-wrap text-sm text-muted-foreground">{sow.scope}</p>
            </section>
          ) : null}

          {sow.special_conditions ? (
            <section>
              <h3 className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
                Special conditions
              </h3>
              <p className="mt-1.5 whitespace-pre-wrap text-sm text-muted-foreground">
                {sow.special_conditions}
              </p>
            </section>
          ) : null}

          <Deliverables deliverables={sow.deliverables} />
          <Milestones milestones={sow.milestones} />
        </CardContent>
      </Card>

      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <History aria-hidden className="size-4 text-primary" />
              Change trail
            </CardTitle>
          </CardHeader>
          <CardContent>
            {sow.history.length === 0 ? (
              <EmptyState title="No recorded changes" description="Edits will appear here." />
            ) : (
              <ol className="space-y-3">
                {sow.history.map((entry, index) => (
                  <li key={index} className="border-l border-border pl-3">
                    <p className="text-sm font-medium">{String(entry.action ?? 'Change')}</p>
                    <p className="text-xs text-muted-foreground">
                      {entry.at ? formatDateTime(String(entry.at)) : ''}
                      {entry.actor_name ? ` · ${String(entry.actor_name)}` : ''}
                    </p>
                    {entry.reason ? (
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {String(entry.reason)}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Document</CardTitle>
            <CardDescription>
              The signed or draft document attached to this SOW, if there is one.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {sow.document_id ? (
              <Link
                href={`/documents/${sow.document_id}`}
                className="text-sm font-medium text-primary hover:underline"
              >
                Open the attached document
              </Link>
            ) : (
              <EmptyState
                title="No document attached"
                description="Upload the SOW as a document to keep a signed copy with it."
                action={
                  <Link href="/documents">
                    <Button size="sm" variant="outline">
                      Go to documents
                    </Button>
                  </Link>
                }
              />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

/**
 * Deliverables arrive as free-form objects, so each is rendered from the keys the
 * server actually sent rather than from an assumed schema.
 */
function Deliverables({ deliverables }: { deliverables: Record<string, unknown>[] }) {
  if (deliverables.length === 0) return null

  return (
    <section>
      <h3 className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        Deliverables
      </h3>
      <ul className="mt-2 space-y-1.5">
        {deliverables.map((item, index) => (
          <li key={index} className="flex items-start gap-2 text-sm">
            <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0 text-success" />
            <span className="text-muted-foreground">{String(item.title ?? item.name ?? JSON.stringify(item))}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function Milestones({ milestones }: { milestones: Record<string, unknown>[] }) {
  if (milestones.length === 0) return null

  return (
    <section>
      <h3 className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        Milestones
      </h3>
      <ul className="mt-2 space-y-1.5">
        {milestones.map((item, index) => (
          <li key={index} className="flex items-start justify-between gap-3 text-sm">
            <span className="text-muted-foreground">
              {String(item.title ?? item.name ?? `Milestone ${index + 1}`)}
            </span>
            {item.due_date ? (
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatDate(String(item.due_date))}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}

function RolesTab({ sow }: { sow: Sow }) {
  const columns: Column<Sow['roles'][number]>[] = [
    {
      key: 'role',
      header: 'Project role',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.role_title ?? '—'}</p>
          <PublicId value={row.project_role_id} kind="role" />
        </div>
      ),
    },
    { key: 'quantity', header: 'Quantity', numeric: true, cell: (row) => row.quantity },
    {
      key: 'rate',
      header: 'Rate',
      numeric: true,
      cell: (row) =>
        row.rate ? `${formatCurrency(row.rate, row.currency)} / ${row.rate_type}` : '—',
    },
    {
      key: 'notes',
      header: 'Notes',
      hideBelow: 'md',
      cell: (row) => <span className="text-muted-foreground">{row.notes ?? '—'}</span>,
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Roles priced by this SOW</CardTitle>
        <CardDescription>
          These rates are what contracts generated from this SOW will be built on.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          rows={sow.roles}
          rowKey={(row) => row.project_role_id}
          caption="Roles priced by this statement of work"
          exportName={`sow-${sow.public_id}-roles`}
          csv={[
            { header: 'Role ID', value: (row) => row.project_role_id },
            { header: 'Role', value: (row) => row.role_title },
            { header: 'Quantity', value: (row) => row.quantity },
            { header: 'Rate', value: (row) => row.rate },
            { header: 'Rate type', value: (row) => row.rate_type },
            { header: 'Currency', value: (row) => row.currency },
            { header: 'Notes', value: (row) => row.notes },
          ]}
          emptyState={
            <EmptyState
              title="No roles priced"
              description="A SOW without roles cannot be contracted, because a contract is priced by role."
            />
          }
        />
      </CardContent>
    </Card>
  )
}

function ContractsTab({ sow }: { sow: Sow }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Contracts from this SOW</CardTitle>
        <CardDescription>
          Contracts are generated from an approved SOW, one per role or counterparty as
          configured.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {sow.contract_ids.length === 0 ? (
          <EmptyState
            title="No contracts yet"
            description={
              sow.status === 'ACTIVE'
                ? 'Contracts can now be generated from this SOW.'
                : 'Approve the SOW to enable contract generation.'
            }
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {sow.contract_ids.map((contractId) => (
              <li key={contractId} className="flex items-center justify-between gap-3 py-2.5">
                <PublicId value={contractId} kind="contract" href={`/contracts/${contractId}`} />
                <Link
                  href={`/contracts/${contractId}`}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  Open
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function HistoryTab({
  sow,
  versions,
}: {
  sow: Sow
  versions: ReturnType<typeof useCompanyQuery<VersionRow[]>>
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Version history</CardTitle>
        <CardDescription>
          Every version of this SOW with who changed it and why.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {versions.isPending ? (
          <LoadingBlock rows={4} />
        ) : versions.isError ? (
          <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
        ) : (versions.data ?? sow.history).length === 0 ? (
          <EmptyState title="No versions recorded" description="This SOW has not changed since it was created." />
        ) : (
          <ol className="space-y-4">
            {(versions.data ?? []).map((version) => (
              <li key={version.version} className="border-l border-border pl-4">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs">v{version.version}</span>
                  <StatusBadge status={version.status} />
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(version.changed_at)}
                  </span>
                </div>
                {version.changed_by_name || version.changed_by ? (
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    by {version.changed_by_name ?? version.changed_by}
                  </p>
                ) : null}
                {version.reason ? (
                  <p className="mt-1 text-sm">{version.reason}</p>
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        {label}
      </dt>
      <dd className={mono ? 'truncate font-mono text-xs' : 'truncate'}>{value}</dd>
    </div>
  )
}