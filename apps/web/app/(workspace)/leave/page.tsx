'use client'

import * as React from 'react'
import { CalendarDays, Check, X } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatDate } from '@/lib/utils'
import type { LeaveRequest, Page as PageEnvelope } from '@/lib/domain-types'
import { Button, EmptyState } from '@/components/ui'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { FilterBar, FilterInput, FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock } from '@/components/query'
import { DecisionDialog, ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * Every leave request in the company, for an approver.
 *
 * `mine=false` is sent explicitly: the API defaults to the caller's own requests,
 * so omitting it would show an approver their own leave and hide the queue they
 * are responsible for.
 */
export default function LeavePage() {
  const { activeCompanyPublicId, can, me } = useCompany()
  const [action, setAction] = React.useState<
    | { kind: 'approve' | 'reject' | 'cancel'; id: string; label: string }
    | null
  >(null)

  const list = useCursorList<PageEnvelope<LeaveRequest>>({
    companyPublicId: activeCompanyPublicId,
    path: '/leave',
    queryKey: ['leave', 'list'],
    initialFilters: { mine: false },
  })

  const rows = list.query.data?.data ?? []

  const decide = useCompanyMutation<unknown, { kind: 'approve' | 'reject' | 'cancel'; id: string; reason?: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ kind, id, reason }) => {
      if (kind === 'cancel') {
        return api.post(`/leave/${id}/cancel?reason=${encodeURIComponent(reason ?? '')}`, {}, { companyPublicId: activeCompanyPublicId })
      }
      return api.post(`/leave/${id}/${kind}`, {}, { companyPublicId: activeCompanyPublicId })
    },
    invalidate: [['leave'], ['dashboard']],
    onSuccess: () => {
      setAction(null)
      notifySuccess('Leave request updated.')
    },
  })

  const columns: Column<LeaveRequest>[] = [
    {
      key: 'person',
      header: 'Person',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.user_name ?? 'Unknown'}</p>
          <PublicId value={row.user_id} kind="user" />
        </div>
      ),
    },
    {
      key: 'policy',
      header: 'Policy',
      hideBelow: 'sm',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.policy_name ?? row.leave_type ?? 'Leave'}</p>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    {
      key: 'dates',
      header: 'Dates',
      cell: (row) => `${formatDate(row.start_date)} – ${formatDate(row.end_date)}`,
    },
    {
      key: 'days',
      header: 'Days',
      numeric: true,
      cell: (row) => row.total_days,
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'reason',
      header: 'Reason',
      hideBelow: 'lg',
      cell: (row) => <span className="text-muted-foreground">{row.reason ?? '—'}</span>,
    },
    {
      key: 'actions',
      header: 'Actions',
      hideBelow: 'md',
      cell: (row) => (
        <div className="flex flex-wrap justify-end gap-1.5">
          {row.status === 'PENDING' && can('leave.approve') ? (
            <>
              <Button
                size="xs"
                variant="success"
                onClick={() =>
                  setAction({
                    kind: 'approve',
                    id: row.public_id,
                    label: `${row.user_name ?? 'this person'} · ${formatDate(row.start_date)} to ${formatDate(row.end_date)}`,
                  })
                }
              >
                <Check aria-hidden />
                Approve
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() =>
                  setAction({
                    kind: 'reject',
                    id: row.public_id,
                    label: `${row.user_name ?? 'this person'} · ${formatDate(row.start_date)} to ${formatDate(row.end_date)}`,
                  })
                }
              >
                <X aria-hidden />
                Reject
              </Button>
            </>
          ) : null}
          {['PENDING', 'APPROVED'].includes(row.status) && row.user_id === me?.public_id ? (
            <Button
              size="xs"
              variant="ghost"
              className="text-danger hover:bg-danger-soft"
              onClick={() =>
                setAction({
                  kind: 'cancel',
                  id: row.public_id,
                  label: `${formatDate(row.start_date)} to ${formatDate(row.end_date)}`,
                })
              }
            >
              Cancel
            </Button>
          ) : null}
        </div>
      ),
    },
  ]

  const pending = rows.filter((row) => row.status === 'PENDING').length

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Leave' }]}
          title="Leave"
          description="Every leave request in the company. Approving deducts the days from the person's balance and blocks their assignments for those dates."
        />

        <div className="grid gap-4 sm:grid-cols-3">
          <Metric label="On this page" value={rows.length} />
          <Metric label="Awaiting a decision" value={pending} tone={pending > 0 ? 'warning' : undefined} />
          <Metric
            label="Approved"
            value={rows.filter((row) => row.status === 'APPROVED').length}
            tone="success"
          />
        </div>

        <FilterBar activeCount={list.activeFilterCount - 1} onClear={list.clearFilters}>
          <FilterSelect
            id="leave-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={['PENDING', 'APPROVED', 'REJECTED', 'CANCELLED']}
            className="w-48"
          />
          <FilterInput
            id="leave-user"
            label="User ID"
            value={(list.filters.user_id as string) ?? ''}
            onChange={(value) => list.setFilter('user_id', value)}
            placeholder="U01H8KM2Q"
            type="text"
            className="w-56"
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
              caption="Leave requests in this company"
              exportName="leave"
              csv={[
                { header: 'Leave ID', value: (row) => row.public_id },
                { header: 'Person', value: (row) => row.user_name },
                { header: 'User ID', value: (row) => row.user_id },
                { header: 'Policy', value: (row) => row.policy_name },
                { header: 'Type', value: (row) => row.leave_type },
                { header: 'Start', value: (row) => row.start_date },
                { header: 'End', value: (row) => row.end_date },
                { header: 'Days', value: (row) => row.total_days },
                { header: 'Status', value: (row) => row.status },
                { header: 'Reason', value: (row) => row.reason },
                { header: 'Decided at', value: (row) => row.decided_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<CalendarDays aria-hidden />}
                  title={list.activeFilterCount > 1 ? 'No leave matches' : 'No leave requests yet'}
                  description={
                    list.activeFilterCount > 1
                      ? 'Clear the filters to see everything.'
                      : 'Leave requests appear here once someone books time off against a policy.'
                  }
                  action={
                    list.activeFilterCount > 1 ? (
                      <Button variant="outline" onClick={list.clearFilters}>
                        Clear filters
                      </Button>
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
              noun="requests"
            />
          </>
        )}
      </div>

      <DecisionDialog
        open={action?.kind === 'approve'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        decision="APPROVED"
        title="Approve this leave"
        description={`Approving grants the leave for ${action?.label ?? ''} and deducts the days from their balance. It is recorded against your identity.`}
        confirmLabel="Approve leave"
        busy={decide.isPending}
        error={decide.isError ? decide.error : null}
        onConfirm={async () => {
          if (!action) return
          try {
            await decide.mutateAsync({ kind: 'approve', id: action.id })
          } catch (cause) {
            notifyError(cause, 'The leave request could not be approved.')
          }
        }}
      />

      <DecisionDialog
        open={action?.kind === 'reject'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        decision="REJECTED"
        title="Reject this leave"
        description={`The request for ${action?.label ?? ''} is refused. The person will see the notes you leave here.`}
        confirmLabel="Reject leave"
        notesLabel="Why this is refused"
        busy={decide.isPending}
        error={decide.isError ? decide.error : null}
        onConfirm={async (notes) => {
          if (!action) return
          try {
            await decide.mutateAsync({ kind: 'reject', id: action.id, ...(notes ? { reason: notes } : {}) })
          } catch (cause) {
            notifyError(cause, 'The leave request could not be rejected.')
          }
        }}
      />

      <ReasonDialog
        open={action?.kind === 'cancel'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Cancel this leave"
        description="Cancelling withdraws the request. If it had already been approved the days return to the balance, and anyone assigned to work on those dates is released back."
        confirmLabel="Cancel leave"
        label="Reason for cancelling"
        busy={decide.isPending}
        error={decide.isError ? decide.error : null}
        onConfirm={(reason) => {
          if (!action) return
          decide
            .mutateAsync({ kind: 'cancel', id: action.id, reason })
            .catch((cause) => notifyError(cause, 'The leave request could not be cancelled.'))
        }}
      />
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Policies                                                                   */
/* -------------------------------------------------------------------------- */
