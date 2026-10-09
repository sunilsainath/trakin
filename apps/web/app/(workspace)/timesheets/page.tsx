'use client'

import * as React from 'react'
import { Clock, Lock, Plus, RotateCcw, Send, Sparkles } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime, formatHours } from '@/lib/utils'
import type { Page as PageEnvelope, Timesheet, TimesheetEntry } from '@/lib/domain-types'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Dialog,
  EmptyState,
  Input,
} from '@/components/ui'
import { Field } from '@/components/forms'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { FilterBar, FilterCheckbox, FilterSelect, FilterInput } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { ConfirmOnlyDialog, ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'
import { ImportEntries } from './import-entries'

/**
 * Every timesheet in the company, for an approver.
 *
 * `mine=false` is sent deliberately: the API defaults to the caller's own sheets,
 * so omitting it would quietly show an approver their own times and hide the ones
 * they are meant to approve.
 */
export default function TimesheetsPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [open, setOpen] = React.useState<string | null>(null)
  const [action, setAction] = React.useState<
    | { kind: 'submit' | 'lock' | 'revise' | 'approve' | 'reject' | 'delete_entry'; id: string; label: string; entryId?: string }
    | null
  >(null)

  const list = useCursorList<PageEnvelope<Timesheet>>({
    companyPublicId: activeCompanyPublicId,
    path: '/timesheets',
    queryKey: ['timesheets', 'list'],
    initialFilters: { mine: false },
  })

  const rows = list.query.data?.data ?? []

  const act = useCompanyMutation<unknown, { path: string; body?: Record<string, unknown>; reason?: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ path, body, reason }) => {
      const suffix = reason === undefined ? '' : `?reason=${encodeURIComponent(reason)}`
      return api.post(path + suffix, body ?? {}, { companyPublicId: activeCompanyPublicId })
    },
    invalidate: [['timesheets'], ['leave']],
    onSuccess: () => {
      setAction(null)
      notifySuccess('Timesheet updated.')
    },
  })

  const columns: Column<Timesheet>[] = [
    {
      key: 'id',
      header: 'Timesheet',
      cell: (row) => (
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => setOpen(row.public_id)}
            className="block truncate text-left font-medium hover:text-primary-strong"
          >
            {row.user_name ?? 'Unknown'}
          </button>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    {
      key: 'project',
      header: 'Project & role',
      hideBelow: 'lg',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.project_name ?? '—'}</p>
          <p className="truncate text-xs text-muted-foreground">{row.role_title ?? 'No role'}</p>
        </div>
      ),
    },
    {
      key: 'period',
      header: 'Period',
      hideBelow: 'sm',
      cell: (row) => `${formatDate(row.period_start)} – ${formatDate(row.period_end)}`,
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'hours',
      header: 'Hours',
      numeric: true,
      cell: (row) => (
        <span>
          {formatHours(row.total_hours)}
          <span className="ml-1 text-xs text-muted-foreground">
            ({formatHours(row.billable_hours)} billable)
          </span>
        </span>
      ),
    },
    {
      key: 'amount',
      header: 'Value',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => formatCurrency(row.total_amount, row.currency),
    },
    {
      key: 'step',
      header: 'Step',
      numeric: true,
      hideBelow: 'lg',
      cell: (row) => (row.current_step > 0 ? `of ${row.approvals.length}` : '—'),
    },
    {
      key: 'actions',
      header: 'Actions',
      hideBelow: 'md',
      cell: (row) => (
        <div className="flex flex-wrap justify-end gap-1.5">
          {row.editable && can('timesheets.create') ? (
            <Button
              size="xs"
              variant="outline"
              onClick={() => setAction({ kind: 'submit', id: row.public_id, label: `${row.user_name ?? 'this person'} · ${formatDate(row.period_start)}` })}
            >
              <Send aria-hidden />
              {row.status === 'REJECTED' ? 'Resubmit' : 'Submit'}
            </Button>
          ) : null}
          {['SUBMITTED', 'UNDER_REVIEW'].includes(row.status) && can('timesheets.approve') ? (
            <>
              <Button
                size="xs"
                variant="success"
                onClick={() => setAction({ kind: 'approve', id: row.public_id, label: `${row.user_name ?? 'this person'} · ${formatDate(row.period_start)}` })}
              >
                Approve
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => setAction({ kind: 'reject', id: row.public_id, label: `${row.user_name ?? 'this person'} · ${formatDate(row.period_start)}` })}
              >
                Return
              </Button>
            </>
          ) : null}
          {row.status === 'APPROVED' && can('timesheets.approve') ? (
            <Button
              size="xs"
              variant="outline"
              onClick={() => setAction({ kind: 'lock', id: row.public_id, label: `${row.user_name ?? 'this person'} · ${formatDate(row.period_start)}` })}
            >
              <Lock aria-hidden />
              Lock
            </Button>
          ) : null}
          {row.status === 'LOCKED' && can('timesheets.approve') ? (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => setAction({ kind: 'revise', id: row.public_id, label: `${row.user_name ?? 'this person'} · ${formatDate(row.period_start)}` })}
            >
              <RotateCcw aria-hidden />
              Revise
            </Button>
          ) : null}
          <Button size="xs" variant="ghost" onClick={() => setOpen(row.public_id)}>
            Open
          </Button>
        </div>
      ),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'All timesheets' }]}
          title="Timesheets"
          description="Every timesheet in the company. Approving one releases its hours for billing and then locks it."
        />

        <FilterBar activeCount={list.activeFilterCount - 1} onClear={list.clearFilters}>
          <FilterSelect
            id="timesheet-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={['DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'LOCKED', 'REJECTED']}
            className="w-52"
          />
          <FilterInput
            id="timesheet-user"
            label="User ID"
            value={(list.filters.user_id as string) ?? ''}
            onChange={(value) => list.setFilter('user_id', value)}
            placeholder="U01H8KM2Q"
            type="text"
            className="w-56"
          />
          <FilterCheckbox
            id="timesheet-pending"
            label="Pending approval only"
            checked={Boolean(list.filters.pending_approval)}
            onChange={(checked) => list.setFilter('pending_approval', checked)}
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
              caption="All timesheets in this company"
              exportName="timesheets"
              csv={[
                { header: 'Timesheet ID', value: (row) => row.public_id },
                { header: 'Person', value: (row) => row.user_name },
                { header: 'User ID', value: (row) => row.user_id },
                { header: 'Project', value: (row) => row.project_name },
                { header: 'Role', value: (row) => row.role_title },
                { header: 'Contract ID', value: (row) => row.contract_id },
                { header: 'Period start', value: (row) => row.period_start },
                { header: 'Period end', value: (row) => row.period_end },
                { header: 'Status', value: (row) => row.status },
                { header: 'Total hours', value: (row) => row.total_hours },
                { header: 'Billable hours', value: (row) => row.billable_hours },
                { header: 'Amount', value: (row) => row.total_amount },
                { header: 'Currency', value: (row) => row.currency },
              ]}
              emptyState={
                <EmptyState
                  icon={<Clock aria-hidden />}
                  title={list.activeFilterCount > 1 ? 'No timesheets match' : 'No timesheets yet'}
                  description={
                    list.activeFilterCount > 1
                      ? 'Clear the filters to see everything.'
                      : 'Timesheets appear once people assigned to a contract start recording time.'
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
              noun="timesheets"
            />
          </>
        )}
      </div>

      <TimesheetDialog
        timesheetId={open}
        onClose={() => setOpen(null)}
        onAction={(kind, entryId) => {
          const row = rows.find((candidate) => candidate.public_id === open)
          setAction({
            kind,
            id: open ?? '',
            label: `${row?.user_name ?? 'this person'} · ${row ? formatDate(row.period_start) : ''}`,
            ...(entryId ? { entryId } : {}),
          })
        }}
      />

      <ConfirmOnlyDialog
        open={action?.kind === 'submit'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Submit this timesheet"
        description="Submitting sends it for approval. The hours become billable once approved, and the person who submitted it can no longer edit it."
        confirmLabel="Submit timesheet"
        busy={act.isPending}
        error={act.isError ? act.error : null}
        onConfirm={() => {
          if (!action) return
          act
            .mutateAsync({ path: `/timesheets/${action.id}/submit`, body: {} })
            .catch((cause) => notifyError(cause, 'The timesheet could not be submitted.'))
        }}
      />

      <ConfirmOnlyDialog
        open={action?.kind === 'approve'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Approve this timesheet"
        description="Approving releases the hours for billing. The decision is recorded against your identity, and the server refuses it if you submitted this timesheet yourself."
        confirmLabel="Approve timesheet"
        tone={undefined}
        busy={act.isPending}
        error={act.isError ? act.error : null}
        onConfirm={() => {
          if (!action) return
          act
            .mutateAsync({ path: `/timesheet-approvals/${action.id}?decision=APPROVED`, body: {} })
            .catch((cause) => notifyError(cause, 'The timesheet could not be approved.'))
        }}
      />

      <ReasonDialog
        open={action?.kind === 'reject'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Return this timesheet"
        description="The timesheet goes back to the person who submitted it. They will see the reason you give here, so be specific."
        confirmLabel="Return timesheet"
        label="What needs to change"
        busy={act.isPending}
        error={act.isError ? act.error : null}
        onConfirm={() => {
          if (!action) return
          act
            .mutateAsync({ path: `/timesheet-approvals/${action.id}?decision=REJECTED`, body: {} })
            .catch((cause) => notifyError(cause, 'The timesheet could not be returned.'))
        }}
      />

      <ConfirmOnlyDialog
        open={action?.kind === 'lock'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Lock this timesheet"
        description="Locking freezes the hours permanently so the figures on an invoice cannot change. It can only be reopened through a recorded revision."
        confirmLabel="Lock timesheet"
        busy={act.isPending}
        error={act.isError ? act.error : null}
        onConfirm={() => {
          if (!action) return
          act
            .mutateAsync({ path: `/timesheets/${action.id}/lock`, body: {} })
            .catch((cause) => notifyError(cause, 'The timesheet could not be locked.'))
        }}
      />

      <ReasonDialog
        open={action?.kind === 'revise'}
        onOpenChange={(open) => {
          if (!open) setAction(null)
        }}
        title="Reopen this locked timesheet"
        description="Reopening lets the hours be corrected. The reason is stored with the revision, and any invoice already generated from these hours will not change by itself."
        confirmLabel="Reopen timesheet"
        label="Why the figures need to change"
        busy={act.isPending}
        error={act.isError ? act.error : null}
        onConfirm={(reason) => {
          if (!action) return
          act
            .mutateAsync({ path: `/timesheets/${action.id}/revise`, body: { reason } })
            .catch((cause) => notifyError(cause, 'The timesheet could not be reopened.'))
        }}
      />
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Timesheet detail                                                           */
/* -------------------------------------------------------------------------- */

/**
 * One timesheet with its entries.
 *
 * Entry editing is gated on the server's own `editable` flag rather than on the
 * status, because the server also refuses edits after a lock or while an approval
 * is open, and it is the only authority on that.
 */
function TimesheetDialog({
  timesheetId,
  onClose,
  onAction,
}: {
  timesheetId: string | null
  onClose: () => void
  onAction: (
    kind: 'submit' | 'lock' | 'revise' | 'approve' | 'reject' | 'delete_entry',
    entryId?: string,
  ) => void
}) {
  const { activeCompanyPublicId, can } = useCompany()

  const sheet = useCompanyQuery<Timesheet>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['timesheets', 'detail', timesheetId],
    path: `/timesheets/${timesheetId ?? ''}`,
    enabled: Boolean(timesheetId),
  })

  const addEntry = useCompanyMutation<Timesheet, Partial<TimesheetEntry> & { entry_date: string; hours: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (entry: Partial<TimesheetEntry> & { entry_date: string; hours: string }) =>
      api.post<Timesheet>(
        `/timesheets/${timesheetId ?? ''}/entries`,
        entry,
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['timesheets']],
    onSuccess: () => notifySuccess('Time recorded.'),
  })

  const analyse = useCompanyMutation<Record<string, unknown>>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () =>
      api.post<Record<string, unknown>>(
        `/ai/timesheet-intelligence/${timesheetId ?? ''}`,
        {},
        { companyPublicId: activeCompanyPublicId },
      ),
    onSuccess: (result) =>
      notifySuccess(
        'Analysis complete.',
        String(result.summary ?? 'The assistant has reviewed this timesheet.'),
      ),
  })

  const [entry, setEntry] = React.useState({
    entry_date: '',
    start_time: '',
    end_time: '',
    hours: '',
    work_description: '',
    is_billable: true,
  })
  const [entryError, setEntryError] = React.useState<string | null>(null)

  React.useEffect(() => {
    setEntry({
      entry_date: '',
      start_time: '',
      end_time: '',
      hours: '',
      work_description: '',
      is_billable: true,
    })
    setEntryError(null)
  }, [timesheetId])

  const data = sheet.data

  const entryColumns: Column<TimesheetEntry>[] = [
    { key: 'date', header: 'Date', cell: (row) => formatDate(row.entry_date) },
    {
      key: 'time',
      header: 'Time',
      hideBelow: 'sm',
      cell: (row) =>
        row.start_time && row.end_time ? `${row.start_time} – ${row.end_time}` : '—',
    },
    { key: 'hours', header: 'Hours', numeric: true, cell: (row) => formatHours(row.hours) },
    {
      key: 'billable',
      header: 'Billable',
      hideBelow: 'md',
      cell: (row) => (
        <Badge tone={row.is_billable ? 'success' : 'neutral'}>
          {row.is_billable ? 'Yes' : 'No'}
        </Badge>
      ),
    },
    {
      key: 'description',
      header: 'Description',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.work_description || '—'}</p>
          {row.project_task ? (
            <p className="truncate text-xs text-muted-foreground">{row.project_task}</p>
          ) : null}
        </div>
      ),
    },
    {
      key: 'amount',
      header: 'Value',
      numeric: true,
      hideBelow: 'md',
      cell: (row) =>
        row.rate_applied
          ? formatCurrency(row.amount, row.currency)
          : '—',
    },
    {
      key: 'source',
      header: 'Source',
      hideBelow: 'lg',
      cell: (row) => <span className="text-muted-foreground">{row.source}</span>,
    },
  ]

  return (
    <Dialog
      open={Boolean(timesheetId)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={data ? `${data.user_name ?? 'Timesheet'} · ${formatDate(data.period_start)}` : 'Timesheet'}
      description={data ? `${data.project_name ?? ''} · ${data.role_title ?? 'No role'}` : undefined}
      className="max-w-4xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {data && can('ai.contract_intelligence') ? (
            <Button variant="outline" onClick={() => analyse.mutate()} loading={analyse.isPending}>
              <Sparkles aria-hidden />
              Explain anomalies
            </Button>
          ) : null}
          {data?.editable && can('timesheets.create') ? (
            <Button onClick={() => onAction('submit')}>
              <Send aria-hidden />
              Submit for approval
            </Button>
          ) : null}
        </>
      }
    >
      {sheet.isPending ? (
        <LoadingBlock rows={6} />
      ) : sheet.isError ? (
        <ErrorState error={sheet.error} onRetry={() => void sheet.refetch()} />
      ) : data ? (
        <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
          <div className="grid gap-4 sm:grid-cols-4">
            <Stat label="Status" value={<StatusBadge status={data.status} />} />
            <Stat label="Total hours" value={formatHours(data.total_hours)} />
            <Stat label="Billable" value={formatHours(data.billable_hours)} />
            <Stat label="Value" value={formatCurrency(data.total_amount, data.currency)} />
          </div>

          {data.status === 'REJECTED' && data.rejection_reason ? (
            <div className="rounded-lg border border-danger/30 bg-danger-soft p-3 text-sm">
              <p className="font-medium">Sent back for changes</p>
              <p className="mt-0.5">{data.rejection_reason}</p>
            </div>
          ) : null}

          {!data.editable && data.status !== 'DRAFT' ? (
            <p className="text-xs text-muted-foreground">
              This timesheet can no longer be edited: the server has marked it read-only
              in its current state.
            </p>
          ) : null}

          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
            <Detail label="Timesheet" value={data.public_id} mono />
            <Detail label="Contract" value={data.contract_id} mono />
            <Detail label="Role" value={data.role_id ?? '—'} mono />
            <Detail label="Period" value={`${formatDate(data.period_start)} – ${formatDate(data.period_end)}`} />
            <Detail label="Submitted" value={data.submitted_at ? formatDate(data.submitted_at) : 'Not yet'} />
            <Detail label="Approved" value={data.approved_at ? formatDate(data.approved_at) : 'Not yet'} />
          </dl>

          {data.editable && can('timesheets.create') ? (
            <form
              onSubmit={async (formEvent) => {
                formEvent.preventDefault()
                if (!entry.entry_date) {
                  setEntryError('Choose a date for the entry.')
                  return
                }
                if (!entry.hours && !(entry.start_time && entry.end_time)) {
                  setEntryError('Enter hours, or a start and end time.')
                  return
                }
                if (entry.start_time && entry.end_time && entry.end_time <= entry.start_time) {
                  setEntryError('The end time must be after the start time.')
                  return
                }
                setEntryError(null)
                try {
                  await addEntry.mutateAsync({
                    entry_date: entry.entry_date,
                    hours: entry.hours || '0',
                    ...(entry.start_time ? { start_time: entry.start_time } : {}),
                    ...(entry.end_time ? { end_time: entry.end_time } : {}),
                    work_description: entry.work_description,
                    is_billable: entry.is_billable,
                    break_minutes: 0,
                    source: 'MANUAL',
                  })
                  setEntry((previous) => ({ ...previous, hours: '', work_description: '' }))
                } catch (cause) {
                  notifyError(cause, 'The entry could not be recorded.')
                }
              }}
              className="space-y-3 rounded-lg border border-border bg-surface-sunken p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold">Record time</p>
                <ImportEntries
                  timesheetId={timesheetId ?? ''}
                  onImported={() => void sheet.refetch()}
                />
              </div>
              <div className="grid gap-3 sm:grid-cols-4">
                <Field label="Date" required error={entryError ?? undefined}>
                  <Input
                    id="entry-date"
                    type="date"
                    value={entry.entry_date}
                    onChange={(event) => setEntry((previous) => ({ ...previous, entry_date: event.target.value }))}
                  />
                </Field>
                <Field label="Start">
                  <Input
                    id="entry-start"
                    type="time"
                    value={entry.start_time}
                    onChange={(event) => setEntry((previous) => ({ ...previous, start_time: event.target.value }))}
                  />
                </Field>
                <Field label="End">
                  <Input
                    id="entry-end"
                    type="time"
                    value={entry.end_time}
                    onChange={(event) => setEntry((previous) => ({ ...previous, end_time: event.target.value }))}
                  />
                </Field>
                <Field label="Hours" hint="Or use start and end">
                  <Input
                    id="entry-hours"
                    type="number"
                    min="0"
                    max="24"
                    step="0.25"
                    value={entry.hours}
                    onChange={(event) => setEntry((previous) => ({ ...previous, hours: event.target.value }))}
                  />
                </Field>
              </div>
              <Field label="What was done" required>
                <Input
                  id="entry-description"
                  value={entry.work_description}
                  onChange={(event) => setEntry((previous) => ({ ...previous, work_description: event.target.value }))}
                  placeholder="Serviced the north conveyor"
                />
              </Field>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <label htmlFor="entry-billable" className="flex items-center gap-2 text-sm">
                  <input
                    id="entry-billable"
                    type="checkbox"
                    checked={entry.is_billable}
                    onChange={(event) => setEntry((previous) => ({ ...previous, is_billable: event.target.checked }))}
                    className="size-4 rounded border-input text-primary"
                  />
                  Billable
                </label>
                <Button type="submit" size="sm" loading={addEntry.isPending}>
                  <Plus aria-hidden />
                  Add entry
                </Button>
              </div>
            </form>
          ) : null}

          <DataTable
            columns={entryColumns}
            rows={data.entries}
            rowKey={(row) => row.id}
            caption="Time entries"
            emptyState={
              <EmptyState
                icon={<Clock aria-hidden />}
                title="No time recorded"
                description="Entries added here appear on this timesheet and are what billing prices."
              />
            }
          />

          {data.approvals.length > 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>Approval chain</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-3">
                  {data.approvals.map((step, index) => (
                    <li key={index} className="space-y-0.5 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium">
                          Step {String(step.step_no ?? index + 1)}
                          {step.name ? ` · ${String(step.name)}` : ''}
                        </span>
                        <StatusBadge status={String(step.status ?? 'PENDING')} />
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {step.approver_name
                          ? `Decided by ${String(step.approver_name)}`
                          : 'Awaiting a reviewer'}
                        {step.decided_at ? ` · ${formatDateTime(String(step.decided_at))}` : ''}
                      </p>
                      {step.notes ? (
                        <p className="text-xs text-muted-foreground">“{String(step.notes)}”</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          ) : null}
        </div>
      ) : null}
    </Dialog>
  )
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">{label}</p>
      <div className="mt-1 text-lg font-semibold tabular">{value}</div>
    </div>
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