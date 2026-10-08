'use client'

import * as React from 'react'
import Link from 'next/link'
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileSignature,
  FolderKanban,
  Sparkles,
  Users,
  Wallet,
} from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import { useCompanyQuery } from '@/components/query'
import { formatCurrency, formatDate, formatHours, formatRelative } from '@/lib/utils'
import { statusLabel, statusTone } from '@/lib/status'
import type { DashboardResponse } from '@/lib/domain-types'
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
} from '@/components/ui'
import { Metric, StatusBadge } from '@/components/badges'
import { PublicId } from '@/components/public-id'
import { PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, LoadingMetrics } from '@/components/query'

/**
 * The company dashboard.
 *
 * `GET /dashboard` returns only the panels the caller is allowed to read, so the
 * layout is assembled from the keys that came back rather than from a role
 * matrix written here. A panel that is absent is genuinely unreadable to this
 * person; that is different from a panel that is present and empty, and the two
 * are rendered differently.
 */
export function Dashboard() {
  const { me, activeCompanyPublicId, roleKeys } = useCompany()

  const query = useCompanyQuery<DashboardResponse>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['dashboard'],
    path: '/dashboard',
  })

  const greeting = React.useMemo(() => {
    const hour = new Date().getHours()
    if (hour < 12) return 'Good morning'
    if (hour < 18) return 'Good afternoon'
    return 'Good evening'
  }, [])

  if (query.isPending) {
    return (
      <PageShell>
        <div className="space-y-6">
          <SkeletonHeader />
          <LoadingMetrics count={4} />
          <LoadingBlock rows={5} />
        </div>
      </PageShell>
    )
  }

  if (query.isError) {
    return (
      <PageShell>
        <div className="space-y-6">
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        </div>
      </PageShell>
    )
  }

  const data = query.data
  const panels = data.panels
  const readable = data.domains.filter((domain) => domain !== 'self')

  return (
    <PageShell>
      <div className="space-y-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight">
              {greeting}, {me?.first_name ?? 'there'}
            </h1>
            <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
              {data.company.name ?? 'Your company'}
              <span className="text-subtle-foreground" aria-hidden>
                ·
              </span>
              <PublicId value={data.company.public_id} kind="company" />
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            {roleKeys.map((key) => (
              <Badge key={key} tone="outline">
                {key}
              </Badge>
            ))}
          </div>
        </header>

        {/* An employee sees "my work" first; everyone sees their domain panels. */}
        <MyWorkPanel panel={panels.me} />

        {readable.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Sparkles aria-hidden />}
              title="Nothing to summarise yet"
              description="Your roles in this company do not include any reporting modules. Once you are given delivery, finance or people access, your dashboard fills in here."
            />
          </Card>
        ) : (
          <>
            {panels.finance ? <FinancePanel panel={panels.finance} currency={data.company.currency} /> : null}
            {panels.projects ? <ProjectsPanel panel={panels.projects} /> : null}
            {panels.contracts ? <ContractsPanel panel={panels.contracts} /> : null}
            {panels.sows ? <SowsPanel counts={panels.sows} /> : null}
            {panels.timesheets ? <TimesheetsPanel panel={panels.timesheets} /> : null}
            {panels.leave ? <LeavePanel counts={panels.leave} /> : null}
            {panels.people ? <PeoplePanel panel={panels.people} /> : null}
            {panels.ai_alerts.length > 0 ? <AlertsPanel alerts={panels.ai_alerts} /> : null}
          </>
        )}
      </div>
    </PageShell>
  )
}

function SkeletonHeader() {
  return (
    <div className="space-y-2">
      <div className="h-6 w-56 rounded-md bg-muted/70" aria-hidden />
      <div className="h-4 w-72 rounded-md bg-muted/70" aria-hidden />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* My work                                                                    */
/* -------------------------------------------------------------------------- */

interface MyAssignments {
  id?: string
  role_title?: string | null
  status?: string | null
  start_date?: string | null
  end_date?: string | null
  allocation_pct?: string | null
  contract_public_id?: string | null
  contract_title?: string | null
  project_public_id?: string | null
  project_name?: string | null
  role_public_id?: string | null
}

interface MyTimesheets {
  public_id?: string
  status?: string
  period_start?: string
  period_end?: string
  total_hours?: string
  billable_hours?: string
  total_amount?: string
  currency?: string
  rejection_reason?: string | null
}

interface MyLeave {
  public_id?: string
  status?: string
  start_date?: string
  end_date?: string
  total_days?: string
}

function MyWorkPanel({ panel }: { panel: DashboardResponse['panels']['me'] }) {
  const assignments: MyAssignments[] = (panel?.assignments ?? []) as MyAssignments[]
  const timesheets: MyTimesheets[] = (panel?.timesheets ?? []) as MyTimesheets[]
  const leave: MyLeave[] = (panel?.leave ?? []) as MyLeave[]

  const openTimesheets = timesheets.filter((sheet) => sheet.status === 'DRAFT' || sheet.status === 'REJECTED')
  const awaiting = timesheets.filter(
    (sheet) => sheet.status === 'SUBMITTED' || sheet.status === 'UNDER_REVIEW',
  )
  const pendingApprovals = panel?.pending_approvals ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your work</CardTitle>
        <CardDescription>
          Only you and your approvers see this panel.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Active assignments"
            value={assignments.length}
            hint={assignments.length === 1 ? '1 engagement' : `${assignments.length} engagements`}
          />
          <Metric
            label="Timesheets to finish"
            value={openTimesheets.length}
            tone={openTimesheets.some((sheet) => sheet.status === 'REJECTED') ? 'danger' : undefined}
            hint={
              openTimesheets.some((sheet) => sheet.status === 'REJECTED')
                ? 'One was sent back to you'
                : 'Draft or returned'
            }
          />
          <Metric
            label="Awaiting approval"
            value={awaiting.length}
            hint="Submitted, not yet decided"
          />
          <Metric
            label="Approvals for you"
            value={pendingApprovals}
            tone={pendingApprovals > 0 ? 'warning' : undefined}
            hint="Timesheets waiting on your decision"
          />
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <section className="space-y-2">
            <SectionHeading icon={<FolderKanban aria-hidden />} title="Your assignments" />
            {assignments.length === 0 ? (
              <EmptyState
                icon={<FolderKanban aria-hidden />}
                title="You have no active assignments"
                description="A project manager assigns you to a contract, and it appears here with its allocation."
              />
            ) : (
              <ul className="divide-y divide-border/60">
                {assignments.slice(0, 6).map((assignment, index) => (
                  <li key={assignment.id ?? `${assignment.contract_public_id}-${index}`} className="py-2.5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {assignment.project_name ?? 'Project'}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {assignment.role_title ?? assignment.role_public_id ?? 'Unassigned role'}
                          {assignment.contract_title ? ` · ${assignment.contract_title}` : ''}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        {assignment.status ? <StatusBadge status={assignment.status} /> : null}
                        {assignment.allocation_pct ? (
                          <p className="mt-1 text-2xs text-muted-foreground tabular">
                            {assignment.allocation_pct}% allocation
                          </p>
                        ) : null}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="space-y-2">
            <SectionHeading icon={<Clock aria-hidden />} title="Your timesheets" />
            {timesheets.length === 0 ? (
              <EmptyState
                icon={<Clock aria-hidden />}
                title="No timesheets yet"
                description="Create one against an assignment to start recording time."
                action={
                  <Link href="/timesheets" className="text-sm font-medium text-primary hover:underline">
                    Open timesheets
                  </Link>
                }
              />
            ) : (
              <ul className="divide-y divide-border/60">
                {timesheets.slice(0, 6).map((sheet) => (
                  <li key={sheet.public_id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {sheet.period_start && sheet.period_end
                          ? `${formatDate(sheet.period_start)} – ${formatDate(sheet.period_end)}`
                          : 'Period not set'}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {formatHours(sheet.total_hours ?? 0)} total
                        {sheet.total_amount
                          ? ` · ${formatCurrency(sheet.total_amount, sheet.currency ?? 'USD')}`
                          : ''}
                      </p>
                      {sheet.status === 'REJECTED' && sheet.rejection_reason ? (
                        <p className="mt-1 text-xs text-danger">
                          Sent back: {sheet.rejection_reason}
                        </p>
                      ) : null}
                    </div>
                    <Link href={`/timesheets?open=${sheet.public_id}`} className="shrink-0">
                      <StatusBadge status={sheet.status ?? 'DRAFT'} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        {leave.length > 0 ? (
          <section className="space-y-2">
            <SectionHeading icon={<CalendarIcon />} title="Your leave" />
            <ul className="flex flex-wrap gap-2">
              {leave.map((request) => (
                <li
                  key={request.public_id}
                  className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs"
                >
                  <StatusBadge status={request.status ?? 'PENDING'} />
                  <span>
                    {request.start_date ? formatDate(request.start_date) : '—'}
                    {request.end_date ? ` – ${formatDate(request.end_date)}` : ''}
                  </span>
                  {request.total_days ? (
                    <span className="text-muted-foreground tabular">{request.total_days}d</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </CardContent>
    </Card>
  )
}

function CalendarIcon() {
  return <Clock aria-hidden className="size-4 text-muted-foreground" />
}

function SectionHeading({ icon, title, action }: { icon: React.ReactNode; title: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <span className="[&_svg]:size-4 text-muted-foreground">{icon}</span>
        {title}
      </h3>
      {action}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Finance                                                                    */
/* -------------------------------------------------------------------------- */

function FinancePanel({
  panel,
  currency,
}: {
  panel: NonNullable<DashboardResponse['panels']['finance']>
  currency: string
}) {
  const receivables = panel.receivables

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Wallet aria-hidden className="size-4 text-primary" />
          Receivables
        </CardTitle>
        <CardDescription>
          Money you are owed, by when it falls due.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric
            label="Outstanding"
            value={formatCurrency(receivables.outstanding, currency, { compact: true })}
            hint={`${receivables.open_count} open ${receivables.open_count === 1 ? 'invoice' : 'invoices'}`}
          />
          <Metric
            label="Overdue"
            value={formatCurrency(receivables.overdue, currency, { compact: true })}
            tone={Number(receivables.overdue) > 0 ? 'danger' : undefined}
            hint={Number(receivables.overdue) > 0 ? 'Past its due date' : 'Nothing past due'}
          />
          <Metric
            label="Not yet due"
            value={formatCurrency(receivables.not_yet_due, currency, { compact: true })}
            hint="Within payment terms"
          />
          <Metric
            label="Collected this month"
            value={formatCurrency(receivables.collected_this_month, currency, { compact: true })}
            tone="success"
            hint={`Invoiced ${formatCurrency(receivables.invoiced_this_month, currency, { compact: true })}`}
          />
        </div>

        {receivables.aging.length > 0 ? (
          <div>
            <SectionHeading icon={<Clock aria-hidden />} title="Aging" />
            <ul className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {receivables.aging.map((bucket) => (
                <li key={bucket.bucket} className="rounded-md border border-border p-2.5">
                  <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
                    {agingLabel(bucket.bucket)}
                  </p>
                  <p className="mt-0.5 text-sm font-semibold tabular">
                    {formatCurrency(bucket.amount, currency, { compact: true })}
                  </p>
                  <p className="text-2xs text-muted-foreground">
                    {bucket.invoice_count} {bucket.invoice_count === 1 ? 'invoice' : 'invoices'}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {receivables.late_payers.length > 0 ? (
          <div>
            <SectionHeading icon={<AlertTriangle aria-hidden />} title="Late payers" />
            <ul className="mt-2 divide-y divide-border/60">
              {receivables.late_payers.slice(0, 5).map((payer) => (
                <li key={payer.counterparty} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{payer.counterparty}</p>
                    <p className="text-xs text-muted-foreground">
                      {payer.invoice_count} {payer.invoice_count === 1 ? 'invoice' : 'invoices'} ·
                      up to {payer.days_late} days late
                    </p>
                  </div>
                  <span className="shrink-0 text-sm font-semibold tabular text-danger">
                    {formatCurrency(payer.amount, currency, { compact: true })}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Link
            href="/invoices"
            className="inline-flex h-8 items-center rounded-md border border-border px-3 text-sm font-medium transition-colors hover:bg-muted"
          >
            Open invoices
          </Link>
          <Link
            href="/billing"
            className="inline-flex h-8 items-center rounded-md border border-border px-3 text-sm font-medium transition-colors hover:bg-muted"
          >
            Billing runs
          </Link>
        </div>
      </CardContent>
    </Card>
  )
}

function agingLabel(bucket: string): string {
  switch (bucket) {
    case 'OVERDUE':
      return 'Overdue'
    case 'DUE_1_30':
      return '1–30 days'
    case 'DUE_31_60':
      return '31–60 days'
    case 'DUE_61_90':
      return '61–90 days'
    case 'DUE_90_PLUS':
      return '90+ days'
    default:
      return bucket
  }
}

/* -------------------------------------------------------------------------- */
/* Delivery                                                                   */
/* -------------------------------------------------------------------------- */

function ProjectsPanel({ panel }: { panel: NonNullable<DashboardResponse['panels']['projects']> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderKanban aria-hidden className="size-4 text-primary" />
          Projects
        </CardTitle>
        <CardDescription>
          {panel.total} {panel.total === 1 ? 'project' : 'projects'} · average health{' '}
          {panel.avg_health}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="Active" value={panel.active} />
          <Metric label="Pipeline" value={panel.pipeline} />
          <Metric label="On hold" value={panel.on_hold} tone={panel.on_hold > 0 ? 'warning' : undefined} />
          <Metric label="Completed" value={panel.completed} />
          <Metric label="Avg health" value={panel.avg_health} />
        </div>

        {panel.at_risk.length > 0 ? (
          <div>
            <SectionHeading icon={<AlertTriangle aria-hidden />} title="Needs attention" />
            <ul className="mt-2 divide-y divide-border/60">
              {panel.at_risk.map((project) => (
                <li key={project.public_id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <Link
                      href={`/projects/${project.public_id}`}
                      className="truncate text-sm font-medium hover:text-primary-strong"
                    >
                      {project.name}
                    </Link>
                    <p className="font-mono text-2xs text-subtle-foreground">{project.public_id}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <StatusBadge status={project.status} />
                    <Badge tone={project.health_score < 50 ? 'danger' : 'warning'}>
                      Health {project.health_score}
                    </Badge>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

function ContractsPanel({ panel }: { panel: NonNullable<DashboardResponse['panels']['contracts']> }) {
  const entries = Object.entries(panel.by_status).sort(([, a], [, b]) => b - a)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileSignature aria-hidden className="size-4 text-primary" />
          Contracts
        </CardTitle>
        <CardDescription>
          {panel.total} {panel.total === 1 ? 'contract' : 'contracts'} across every status
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap gap-2">
          {entries.map(([status, count]) => (
            <Link
              key={status}
              href={`/contracts?status=${status}`}
              className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            >
              <StatusBadge status={status} />
              <span className="font-semibold tabular">{count}</span>
            </Link>
          ))}
          {entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No contracts yet.</p>
          ) : null}
        </div>

        {panel.expiring_within_60_days.length > 0 ? (
          <div>
            <SectionHeading icon={<Clock aria-hidden />} title="Expiring within 60 days" />
            <ul className="mt-2 divide-y divide-border/60">
              {panel.expiring_within_60_days.map((contract) => (
                <li key={contract.public_id} className="flex items-center justify-between gap-3 py-2">
                  <div className="min-w-0">
                    <Link
                      href={`/contracts/${contract.public_id}`}
                      className="truncate text-sm font-medium hover:text-primary-strong"
                    >
                      {contract.title}
                    </Link>
                    <p className="font-mono text-2xs text-subtle-foreground">{contract.public_id}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-xs text-muted-foreground">
                      ends {formatDate(contract.end_date)}
                    </span>
                    <StatusBadge status={contract.status} />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

function SowsPanel({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort(([, a], [, b]) => b - a)
  const total = entries.reduce((sum, [, count]) => sum + count, 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileSignature aria-hidden className="size-4 text-primary" />
          Statements of work
        </CardTitle>
        <CardDescription>
          {total} {total === 1 ? 'SOW' : 'SOWs'} on file
        </CardDescription>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <EmptyState
            icon={<FileSignature aria-hidden />}
            title="No SOWs yet"
            description="A SOW scopes the work and the roles before a contract is signed."
          />
        ) : (
          <div className="flex flex-wrap gap-2">
            {entries.map(([status, count]) => (
              <Link
                key={status}
                href={`/sows?status=${status}`}
                className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
              >
                <StatusBadge status={status} />
                <span className="font-semibold tabular">{count}</span>
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function TimesheetsPanel({ panel }: { panel: NonNullable<DashboardResponse['panels']['timesheets']> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock aria-hidden className="size-4 text-primary" />
          Timesheets
        </CardTitle>
        <CardDescription>
          {formatHours(panel.billable_hours_this_month)} billable so far this month
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <Metric label="Drafts" value={panel.drafts} />
          <Metric
            label="Sent back"
            value={panel.rejected}
            tone={panel.rejected > 0 ? 'danger' : undefined}
          />
          <Metric label="Awaiting review" value={panel.awaiting} />
          <Metric label="Approved (30d)" value={panel.approved_recent} tone="success" />
          <Metric label="Yours in review" value={panel.mine_awaiting} />
        </div>
      </CardContent>
    </Card>
  )
}

function LeavePanel({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort(([, a], [, b]) => b - a)
  const pending = counts.PENDING ?? 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CheckCircle2 aria-hidden className="size-4 text-primary" />
          Leave
        </CardTitle>
        <CardDescription>
          {pending > 0 ? `${pending} request${pending === 1 ? '' : 's'} awaiting a decision` : 'Nothing awaiting a decision'}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-2">
          {entries.map(([status, count]) => (
            <Link
              key={status}
              href={`/leave?status=${status}`}
              className="flex items-center gap-2 rounded-md border border-border px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            >
              <StatusBadge status={status} />
              <span className="font-semibold tabular">{count}</span>
            </Link>
          ))}
          {entries.length === 0 ? <p className="text-sm text-muted-foreground">No leave requests.</p> : null}
        </div>
      </CardContent>
    </Card>
  )
}

function PeoplePanel({ panel }: { panel: NonNullable<DashboardResponse['panels']['people']> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Users aria-hidden className="size-4 text-primary" />
          People
        </CardTitle>
        <CardDescription>
          {panel.on_active_work} of {panel.members} active members are on live work
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2">
          <Metric label="Active members" value={panel.members} />
          <Metric label="On active work" value={panel.on_active_work} />
        </div>
      </CardContent>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* AI alerts                                                                  */
/* -------------------------------------------------------------------------- */

function AlertsPanel({
  alerts,
}: {
  alerts: NonNullable<DashboardResponse['panels']['ai_alerts']>
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles aria-hidden className="size-4 text-primary" />
          Insights
        </CardTitle>
        <CardDescription>
          Generated from your own records. Each one cites what it is based on.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {alerts.map((alert) => (
            <li
              key={alert.public_id}
              className="flex items-start gap-3 rounded-md border border-border p-3"
            >
              <Badge tone={statusTone(alert.severity)} className="mt-0.5 shrink-0">
                {statusLabel(alert.severity)}
              </Badge>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{alert.title}</p>
                <p className="mt-0.5 text-sm text-muted-foreground">{alert.summary}</p>
                <p className="mt-1 text-2xs text-subtle-foreground">
                  {alert.type} · {formatRelative(alert.created_at)}
                </p>
              </div>
              <Link
                href="/ai/insights"
                className="shrink-0 text-xs font-medium text-primary hover:underline"
              >
                All insights
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}


