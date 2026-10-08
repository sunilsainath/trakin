'use client'

import * as React from 'react'
import { CalendarDays, Clock, Plus } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatHours } from '@/lib/utils'
import type {
  LeaveBalance,
  LeavePolicy,
  LeaveRequest,
  Page as PageEnvelope,
  Timesheet,
} from '@/lib/domain-types'
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
  Select,
  Skeleton,
  Tabs,
} from '@/components/ui'
import { Field } from '@/components/forms'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * The signed-in person's own time and leave.
 *
 * This is the screen an employee actually lives in, so it is built around their
 * own records rather than a company-wide table they cannot read.
 */
export default function TimeAndLeavePage() {
  const [tab, setTab] = React.useState('timesheets')

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Time & Leave' }]}
          title="My time and leave"
          description="Your timesheets, your leave, and what you have left this year."
          actions={
            <div className="flex flex-wrap gap-2">
              <NewTimesheetDialog triggerLabel="New timesheet" />
              <RequestLeaveDialog triggerLabel="Request leave" />
            </div>
          }
        />

        <LeaveBalances />

        <Tabs
          tabs={[
            { key: 'timesheets', label: 'My timesheets' },
            { key: 'leave', label: 'My leave' },
            { key: 'policies', label: 'Leave policies' },
          ]}
          active={tab}
          onChange={setTab}
          className="overflow-x-auto scrollbar-thin"
        />

        {tab === 'timesheets' ? <MyTimesheets /> : null}
        {tab === 'leave' ? <MyLeave /> : null}
        {tab === 'policies' ? <LeavePolicies /> : null}
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Balances                                                                   */
/* -------------------------------------------------------------------------- */

function LeaveBalances() {
  const { activeCompanyPublicId } = useCompany()
  const year = new Date().getFullYear()

  const balances = useCompanyQuery<LeaveBalance[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['leave', 'balances', year],
    path: '/leave/balances',
    queryParams: `?year=${year}`,
  })

  if (balances.isPending) return <LoadingBlock rows={2} />
  if (balances.isError) {
    return <ErrorState error={balances.error} onRetry={() => void balances.refetch()} />
  }

  const rows = balances.data ?? []

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<CalendarDays aria-hidden />}
        title="No leave balances for this year"
        description="Leave policies accrue over time. Nothing is accrued for you yet, or your policies have no accrual."
      />
    )
  }

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {rows.slice(0, 4).map((balance) => (
        <Metric
          key={balance.policy_public_id}
          label={balance.policy_name}
          value={`${balance.available ?? balance.accrued} days`}
          hint={`${balance.taken} taken · ${balance.pending} pending · ${balance.entitled} entitled`}
        />
      ))}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* My timesheets                                                              */
/* -------------------------------------------------------------------------- */

function MyTimesheets() {
  const { activeCompanyPublicId, can } = useCompany()

  const list = useCursorList<PageEnvelope<Timesheet>>({
    companyPublicId: activeCompanyPublicId,
    path: '/timesheets',
    queryKey: ['timesheets', 'mine'],
    initialFilters: { mine: true },
  })

  const rows = list.query.data?.data ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock aria-hidden className="size-4 text-primary" />
          My timesheets
        </CardTitle>
        <CardDescription>
          A timesheet is created against an assignment and covers one period. It has to
          be submitted before it can be billed.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-4">
          <FilterSelect
            id="my-timesheet-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={['DRAFT', 'SUBMITTED', 'UNDER_REVIEW', 'APPROVED', 'LOCKED', 'REJECTED']}
            className="w-52"
          />
        </div>

        {list.query.isPending ? (
          <LoadingBlock rows={6} />
        ) : list.query.isError ? (
          <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={timesheetColumns(can('timesheets.create'))}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="My timesheets"
              exportName="my-timesheets"
              csv={[
                { header: 'Timesheet ID', value: (row) => row.public_id },
                { header: 'Project', value: (row) => row.project_name },
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
                  title={list.activeFilterCount > 0 ? 'No timesheets match' : 'No timesheets yet'}
                  description={
                    list.activeFilterCount > 0
                      ? 'Clear the status filter to see everything.'
                      : 'Create a timesheet against one of your assignments to start recording time.'
                  }
                  action={
                    list.activeFilterCount > 0 ? (
                      <Button variant="outline" onClick={list.clearFilters}>
                        Clear filters
                      </Button>
                    ) : (
                      <NewTimesheetDialog triggerLabel="Create a timesheet" />
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
                noun="timesheets"
              />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function timesheetColumns(canEdit: boolean): Column<Timesheet>[] {
  return [
    {
      key: 'id',
      header: 'Timesheet',
      cell: (row) => (
        <div className="min-w-0">
          <a
            href={`/timesheets?open=${row.public_id}`}
            className="block truncate font-medium hover:text-primary-strong"
          >
            {row.project_name ?? 'Project'}
          </a>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    {
      key: 'period',
      header: 'Period',
      cell: (row) => `${formatDate(row.period_start)} – ${formatDate(row.period_end)}`,
    },
    {
      key: 'status',
      header: 'Status',
      hideBelow: 'sm',
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
      key: 'entries',
      header: 'Entries',
      numeric: true,
      hideBelow: 'lg',
      cell: (row) => row.entry_count,
    },
    ...(canEdit
      ? [
          {
            key: 'action',
            header: 'Action',
            hideBelow: 'md' as const,
            cell: (row: Timesheet) => (
              <a
                href={`/timesheets?open=${row.public_id}`}
                className="text-sm font-medium text-primary hover:underline"
              >
                {row.editable ? 'Add time' : 'Open'}
              </a>
            ),
          },
        ]
      : []),
  ]
}

/* -------------------------------------------------------------------------- */
/* My leave                                                                   */
/* -------------------------------------------------------------------------- */

const LEAVE_COLUMNS: Column<LeaveRequest>[] = [
  {
    key: 'policy',
    header: 'Policy',
    cell: (row) => (
      <div className="min-w-0">
        <p className="truncate font-medium">{row.policy_name ?? row.leave_type ?? 'Leave'}</p>
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
    hideBelow: 'sm',
    cell: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: 'reason',
    header: 'Reason',
    hideBelow: 'lg',
    cell: (row) => <span className="text-muted-foreground">{row.reason ?? '—'}</span>,
  },
  {
    key: 'decision',
    header: 'Decision',
    hideBelow: 'lg',
    cell: (row) =>
      row.decided_at ? (
        <span className="text-muted-foreground">
          {row.status} {row.decided_at ? formatDate(row.decided_at) : ''}
        </span>
      ) : (
        '—'
      ),
  },
]

function MyLeave() {
  const { activeCompanyPublicId } = useCompany()

  const list = useCursorList<PageEnvelope<LeaveRequest>>({
    companyPublicId: activeCompanyPublicId,
    path: '/leave',
    queryKey: ['leave', 'mine'],
    initialFilters: { mine: true },
  })

  const rows = list.query.data?.data ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CalendarDays aria-hidden className="size-4 text-primary" />
          My leave
        </CardTitle>
        <CardDescription>
          Leave is requested against a policy, which sets the accrual, the notice
          period and whether an approver is required.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-4">
          <FilterSelect
            id="my-leave-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={['PENDING', 'APPROVED', 'REJECTED', 'CANCELLED']}
            className="w-52"
          />
        </div>

        {list.query.isPending ? (
          <LoadingBlock rows={5} />
        ) : list.query.isError ? (
          <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={LEAVE_COLUMNS}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="My leave requests"
              exportName="my-leave"
              csv={[
                { header: 'Leave ID', value: (row) => row.public_id },
                { header: 'Policy', value: (row) => row.policy_name },
                { header: 'Type', value: (row) => row.leave_type },
                { header: 'Start', value: (row) => row.start_date },
                { header: 'End', value: (row) => row.end_date },
                { header: 'Days', value: (row) => row.total_days },
                { header: 'Status', value: (row) => row.status },
                { header: 'Reason', value: (row) => row.reason },
              ]}
              emptyState={
                <EmptyState
                  icon={<CalendarDays aria-hidden />}
                  title={
                    list.activeFilterCount > 0 ? 'No leave matches' : 'No leave requested'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Clear the status filter to see everything.'
                      : 'Request leave against one of your policies.'
                  }
                  action={
                    list.activeFilterCount > 0 ? (
                      <Button variant="outline" onClick={list.clearFilters}>
                        Clear filters
                      </Button>
                    ) : (
                      <RequestLeaveDialog triggerLabel="Request leave" />
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
                noun="requests"
              />
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function LeavePolicies() {
  const { activeCompanyPublicId } = useCompany()

  const policies = useCompanyQuery<LeavePolicy[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['leave', 'policies'],
    path: '/leave/policies',
  })

  if (policies.isPending) return <LoadingBlock rows={5} />
  if (policies.isError) {
    return <ErrorState error={policies.error} onRetry={() => void policies.refetch()} />
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Leave policies</CardTitle>
        <CardDescription>
          What each policy accrues, how quickly, and how much notice it needs.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={[
            {
              key: 'name',
              header: 'Policy',
              cell: (row: LeavePolicy) => (
                <div className="min-w-0">
                  <p className="truncate font-medium">{row.name}</p>
                  <PublicId value={row.public_id} />
                </div>
              ),
            },
            { key: 'type', header: 'Type', hideBelow: 'sm', cell: (row: LeavePolicy) => row.leave_type },
            {
              key: 'accrual',
              header: 'Accrual',
              numeric: true,
              cell: (row: LeavePolicy) => `${row.accrual_rate} ${row.accrual_method.toLowerCase()}`,
            },
            {
              key: 'cap',
              header: 'Cap',
              numeric: true,
              hideBelow: 'md',
              cell: (row: LeavePolicy) => row.max_balance ?? '—',
            },
            {
              key: 'notice',
              header: 'Min notice',
              numeric: true,
              hideBelow: 'md',
              cell: (row: LeavePolicy) => `${row.min_notice_days}d`,
            },
            {
              key: 'approval',
              header: 'Approval',
              hideBelow: 'sm',
              cell: (row: LeavePolicy) => (
                <span className="text-muted-foreground">
                  {row.requires_approval ? 'Required' : 'Not required'}
                </span>
              ),
            },
            {
              key: 'active',
              header: 'Active',
              cell: (row: LeavePolicy) => (
                <span className={row.is_active ? 'text-success' : 'text-muted-foreground'}>
                  {row.is_active ? 'Yes' : 'No'}
                </span>
              ),
            },
          ]}
          rows={policies.data ?? []}
          rowKey={(row) => row.public_id}
          caption="Leave policies in this company"
          emptyState={
            <EmptyState
              icon={<CalendarDays aria-hidden />}
              title="No leave policies"
              description="Leave cannot be requested until a policy exists."
            />
          }
        />
      </CardContent>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Create timesheet                                                           */
/* -------------------------------------------------------------------------- */

function NewTimesheetDialog({ triggerLabel = 'New timesheet' }: { triggerLabel?: string }) {
  const { activeCompanyPublicId, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [contractId, setContractId] = React.useState('')
  const [assignmentId, setAssignmentId] = React.useState('')
  const [periodStart, setPeriodStart] = React.useState('')
  const [periodEnd, setPeriodEnd] = React.useState('')
  const [frequency, setFrequency] = React.useState('MONTHLY')
  const [error, setError] = React.useState<string | null>(null)

  const assignments = useCompanyQuery<PageEnvelope<import('@/lib/domain-types').Assignment>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['assignments', 'mine'],
    path: '/assignments',
    queryParams: '?mine=true&limit=100',
    enabled: open,
  })

  React.useEffect(() => {
    if (open) {
      setContractId('')
      setAssignmentId('')
      setPeriodStart('')
      setPeriodEnd('')
      setFrequency('MONTHLY')
      setError(null)
    }
  }, [open])

  const allAssignments = React.useMemo(
    () => (assignments.data?.data ?? []).filter((assignment) => assignment.status === 'ACTIVE'),
    [assignments.data],
  )
  const contractOptions = React.useMemo(() => {
    const seen = new Map<string, string>()
    for (const assignment of allAssignments) {
      if (!seen.has(assignment.contract_id)) {
        seen.set(assignment.contract_id, assignment.contract_title ?? assignment.contract_id)
      }
    }
    return [...seen.entries()]
  }, [allAssignments])
  const visibleAssignments = contractId
    ? allAssignments.filter((assignment) => assignment.contract_id === contractId)
    : allAssignments
  const selectedAssignment = allAssignments.find((assignment) => assignment.id === assignmentId)

  const create = useCompanyMutation<Timesheet, void>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () => {
      if (!assignmentId) throw new Error('Choose an assignment.')
      if (!periodStart || !periodEnd) throw new Error('Both dates are required.')
      if (periodEnd < periodStart) throw new Error('The period end must not precede the start.')
      return api.post<Timesheet>(
        '/timesheets',
        {
          assignment_id: assignmentId,
          period_start: periodStart,
          period_end: periodEnd,
          billing_frequency: frequency,
        },
        { companyPublicId: activeCompanyPublicId },
      )
    },
    invalidate: [['timesheets', 'mine'], ['timesheets', 'list']],
    onSuccess: (sheet) => {
      notifySuccess('Timesheet created.', `${sheet.public_id} is a draft you can add time to.`)
      setOpen(false)
    },
  })

  if (!can('timesheets.create')) {
    return (
      <p className="text-sm text-muted-foreground">
        You do not have permission to create timesheets in this company.
      </p>
    )
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Plus aria-hidden />
        {triggerLabel}
      </Button>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="New timesheet"
        description="A timesheet covers one period on one assignment. Time is added to it afterwards and must be submitted before it can be billed."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="new-timesheet" loading={create.isPending}>
              Create timesheet
            </Button>
          </>
        }
      >
        <form
          id="new-timesheet"
          onSubmit={async (event) => {
            event.preventDefault()
            setError(null)
            try {
              await create.mutateAsync()
            } catch (cause) {
              notifyError(cause, 'The timesheet could not be created.')
            }
          }}
          className="space-y-4"
        >
          <Field
            label="Contract"
            required
            error={error ?? undefined}
            hint="Only contracts with an active assignment to you are listed."
          >
            {assignments.isPending ? (
              <Skeleton className="h-10 w-full" />
            ) : (
              <Select
                id="timesheet-contract"
                value={contractId}
                onChange={(event) => {
                  setContractId(event.target.value)
                  setAssignmentId('')
                }}
              >
                <option value="">Choose a contract</option>
                {contractOptions.map(([id, title]) => (
                  <option key={id} value={id}>
                    {title}
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field
            label="Assignment"
            required
            error={error ?? undefined}
            hint="A timesheet is recorded against the contract and role of an assignment."
          >
            {assignments.isPending ? (
              <Skeleton className="h-10 w-full" />
            ) : (
              <Select
                id="timesheet-assignment"
                value={assignmentId}
                onChange={(event) => setAssignmentId(event.target.value)}
              >
                <option value="">Choose an assignment</option>
                {visibleAssignments.map((assignment) => (
                  <option key={assignment.id} value={assignment.id}>
                    {assignment.project_name ?? 'Project'} ·{' '}
                    {assignment.role_title ?? 'Unassigned role'} ({assignment.user_name ?? 'You'})
                  </option>
                ))}
              </Select>
            )}
          </Field>

          {selectedAssignment ? (
            <dl className="grid gap-x-6 gap-y-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
                  Contract
                </dt>
                <dd>{selectedAssignment.contract_title ?? selectedAssignment.contract_id}</dd>
              </div>
              <div>
                <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
                  Role
                </dt>
                <dd>{selectedAssignment.role_title ?? 'Unassigned role'}</dd>
              </div>
            </dl>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Period start" required>
              <Input
                id="timesheet-start"
                type="date"
                value={periodStart}
                onChange={(event) => setPeriodStart(event.target.value)}
              />
            </Field>
            <Field label="Period end" required>
              <Input
                id="timesheet-end"
                type="date"
                value={periodEnd}
                onChange={(event) => setPeriodEnd(event.target.value)}
              />
            </Field>
          </div>

          <Field label="Billing frequency">
            <Select
              id="timesheet-frequency"
              value={frequency}
              onChange={(event) => setFrequency(event.target.value)}
            >
              {['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM'].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </Field>
        </form>
      </Dialog>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Request leave                                                              */
/* -------------------------------------------------------------------------- */

function RequestLeaveDialog({ triggerLabel = 'Request leave' }: { triggerLabel?: string }) {
  const { activeCompanyPublicId, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [policyId, setPolicyId] = React.useState('')
  const [start, setStart] = React.useState('')
  const [end, setEnd] = React.useState('')
  const [days, setDays] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)

  const policies = useCompanyQuery<LeavePolicy[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['leave', 'policies'],
    path: '/leave/policies',
    enabled: open,
  })

  React.useEffect(() => {
    if (open) {
      setPolicyId('')
      setStart('')
      setEnd('')
      setDays('')
      setReason('')
      setError(null)
    }
  }, [open])

  const request = useCompanyMutation<LeaveRequest, void>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () => {
      if (!policyId) throw new Error('Choose a leave policy.')
      if (!start || !end) throw new Error('Both dates are required.')
      if (end < start) throw new Error('The end date must not precede the start date.')
      return api.post<LeaveRequest>(
        '/leave',
        {
          leave_policy_id: policyId,
          start_date: start,
          end_date: end,
          ...(days ? { total_days: days } : {}),
          ...(reason ? { reason } : {}),
        },
        { companyPublicId: activeCompanyPublicId },
      )
    },
    invalidate: [['leave', 'mine'], ['leave', 'list'], ['leave', 'balances']],
    onSuccess: (leave) => {
      notifySuccess('Leave requested.', `${leave.public_id} is waiting for a decision.`)
      setOpen(false)
    },
  })

  if (!can('leave.request') && !can('leave.create')) {
    return (
      <p className="text-sm text-muted-foreground">
        You do not have permission to request leave in this company.
      </p>
    )
  }

  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <CalendarDays aria-hidden />
        {triggerLabel}
      </Button>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Request leave"
        description="Leave is granted against a policy. The server checks your balance and the policy's notice period before accepting it."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={request.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="request-leave" loading={request.isPending}>
              Submit request
            </Button>
          </>
        }
      >
        <form
          id="request-leave"
          onSubmit={async (event) => {
            event.preventDefault()
            setError(null)
            try {
              await request.mutateAsync()
            } catch (cause) {
              notifyError(cause, 'The leave request could not be created.')
            }
          }}
          className="space-y-4"
        >
          <Field label="Policy" required error={error ?? undefined}>
            {policies.isPending ? (
              <Skeleton className="h-10 w-full" />
            ) : (
              <Select id="leave-policy" value={policyId} onChange={(event) => setPolicyId(event.target.value)}>
                <option value="">Choose a policy</option>
                {(policies.data ?? [])
                  .filter((policy) => policy.is_active)
                  .map((policy) => (
                    <option key={policy.public_id} value={policy.public_id}>
                      {policy.name} ({policy.leave_type})
                    </option>
                  ))}
              </Select>
            )}
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="First day" required>
              <Input id="leave-start" type="date" value={start} onChange={(event) => setStart(event.target.value)} />
            </Field>
            <Field label="Last day" required>
              <Input id="leave-end" type="date" value={end} onChange={(event) => setEnd(event.target.value)} />
            </Field>
          </div>

          <Field label="Total days" hint="Optional. Calculated by the server when omitted.">
            <Input
              id="leave-days"
              type="number"
              min="0.5"
              step="0.5"
              value={days}
              onChange={(event) => setDays(event.target.value)}
            />
          </Field>

          <Field label="Reason">
            <Input id="leave-reason" value={reason} onChange={(event) => setReason(event.target.value)} />
          </Field>
        </form>
      </Dialog>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Approvals                                                                  */
/* -------------------------------------------------------------------------- */
