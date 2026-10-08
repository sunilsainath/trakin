'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  Activity,
  Brain,
  Clock,
  FileSignature,
  FileText,
  Receipt,
  Sparkles,
  Users,
  Wallet,
  FolderKanban,
  Paperclip,
} from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime, formatHours, formatPercent } from '@/lib/utils'
import { statusLabel } from '@/lib/status'
import type {
  Assignment,
  Contract,
  Invoice,
  Page as PageEnvelope,
  Project,
  ProjectBilling,
  ProjectDashboard,
  ProjectRole,
  Sow,
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
  Textarea,
} from '@/components/ui'
import { Field } from '@/components/forms'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { HealthScore, Metric, StatusBadge } from '@/components/badges'
import { ReasonDialog } from '@/components/destructive'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, PermissionState, isPermissionError, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * The project dashboard.
 *
 * `GET /projects/{id}` returns the project together with its roles, team, SOWs,
 * contracts, timesheets, invoices, documents, activity, insights, billing totals
 * and a permission map. One request backs every tab, so switching tabs is
 * instant and cannot show a tab from a different project.
 */
export function ProjectDetail({ initialTab }: { initialTab?: ProjectTab }) {
  const params = useParams<{ id: string }>()
  const projectId = params.id
  const { activeCompanyPublicId, can } = useCompany()

  const [tab, setTab] = React.useState<string>(initialTab ?? 'overview')

  const query = useCompanyQuery<ProjectDashboard>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['projects', 'detail', projectId],
    path: `/projects/${projectId}`,
  })

  const data = query.data
  const project: Project = data?.project ?? EMPTY_PROJECT
  const permissions = data?.permissions ?? {}

  const tabs = React.useMemo(() => {
    const source = data
    return [
      { key: 'overview', label: 'Overview' },
      { key: 'roles', label: 'Roles', badge: source?.roles.length },
      { key: 'team', label: 'Team', badge: source?.team.length },
      { key: 'sows', label: 'SOWs', badge: source?.sows.length },
      { key: 'contracts', label: 'Contracts', badge: source?.contracts.length },
      { key: 'timesheets', label: 'Timesheets', badge: source?.timesheets.length },
      { key: 'billing', label: 'Billing' },
      { key: 'invoices', label: 'Invoices', badge: source?.invoices.length },
      { key: 'documents', label: 'Documents', badge: source?.documents.length },
      { key: 'activity', label: 'Activity' },
      { key: 'insights', label: 'AI Insights', badge: source?.insights.length },
    ]
  }, [data])

  if (query.isPending) return <ProjectDetailSkeleton />

  if (query.isError) {
    return (
      <PageShell>
        <div className="space-y-4">
          {isPermissionError(query.error) ? (
            <PermissionState error={query.error} />
          ) : (
            <ErrorState error={query.error} onRetry={() => void query.refetch()} />
          )}
          <Link href="/projects" className="text-sm font-medium text-primary hover:underline">
            Back to projects
          </Link>
        </div>
      </PageShell>
    )
  }

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Projects', href: '/projects' }, { label: project.public_id, mono: true }]}
          title={project.name}
          description={project.description ?? undefined}
          meta={<StatusBadge status={project.status} />}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <PublicId value={project.public_id} kind="project" size="lg" />
              {permissions.can_edit || can('projects.update') ? (
                <EditProjectDialog project={project} onSaved={() => void query.refetch()} />
              ) : null}
              {can('projects.delete') ? (
                <ArchiveProjectButton
                  projectId={project.public_id}
                  projectName={project.name}
                  onArchived={() => void query.refetch()}
                />
              ) : null}
            </div>
          }
        />

        <Tabs tabs={tabs} active={tab} onChange={setTab} className="overflow-x-auto scrollbar-thin" />

        <div className="min-w-0">
          {tab === 'overview' ? <OverviewTab data={data!} /> : null}
          {tab === 'roles' ? <RolesTab roles={data!.roles} projectId={project.public_id} canManage={can('projects.manage_roles')} /> : null}
          {tab === 'team' ? <TeamTab team={data!.team} /> : null}
          {tab === 'sows' ? <SowsTab sows={data!.sows} projectId={project.public_id} canCreate={can('sows.create')} /> : null}
          {tab === 'contracts' ? <ContractsTab contracts={data!.contracts} projectId={project.public_id} canCreate={can('contracts.create')} /> : null}
          {tab === 'timesheets' ? <TimesheetsTab rows={data!.timesheets} /> : null}
          {tab === 'billing' ? (
            <BillingTab billing={data!.billing} currency={project.currency} projectId={project.public_id} />
          ) : null}
          {tab === 'invoices' ? <InvoicesTab rows={data!.invoices} currency={project.currency} /> : null}
          {tab === 'documents' ? <DocumentsTab rows={data!.documents} /> : null}
          {tab === 'activity' ? <ActivityTab entries={data!.activity} /> : null}
          {tab === 'insights' ? <InsightsTab data={data!} /> : null}
        </div>
      </div>
    </PageShell>
  )
}

export type ProjectTab =
  | 'overview'
  | 'roles'
  | 'team'
  | 'sows'
  | 'contracts'
  | 'timesheets'
  | 'billing'
  | 'invoices'
  | 'documents'
  | 'activity'
  | 'insights'

/**
 * A shape to fall back on while the payload is still loading, so the header and
 * tab bar can render without a non-null assertion on every field. Every field is
 * an empty or zero value: this object is never displayed as real data, because
 * the loading state returns a skeleton instead.
 */
const EMPTY_PROJECT: Project = {
  public_id: '',
  company_id: '',
  name: '',
  description: null,
  project_type: '',
  category: '',
  status: 'DRAFT',
  start_date: null,
  estimated_end_date: null,
  estimated_hours: null,
  estimated_budget: null,
  currency: 'USD',
  billing_basis: '',
  billing_frequency: '',
  payment_terms_days: 0,
  health_score: null,
  owner_user_id: null,
  owner_name: null,
  client_name: null,
  client_company_id: null,
  contract_value: null,
  invoiced_total: '0',
  outstanding_total: '0',
  billing_status: 'NOT_STARTED',
  team_size: 0,
  open_role_count: 0,
  role_count: 0,
  sow_count: 0,
  contract_count: 0,
  active_contract_count: 0,
  timesheet_count: 0,
  last_activity_at: null,
  metadata: {},
  created_at: '',
  updated_at: '',
}

function ProjectDetailSkeleton() {
  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <div className="space-y-2">
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-7 w-80" />
        </div>
        <Skeleton className="h-10 w-full" />
        <LoadingBlock rows={8} />
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Overview                                                                   */
/* -------------------------------------------------------------------------- */

function OverviewTab({ data }: { data: ProjectDashboard }) {
  const { project, billing, roles, sows, contracts } = data

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Contracted"
          value={project.contract_value
            ? formatCurrency(project.contract_value, project.currency, { compact: true })
            : '—'}
          hint={`${project.active_contract_count} active of ${project.contract_count}`}
        />
        <Metric
          label="Invoiced"
          value={formatCurrency(project.invoiced_total, project.currency, { compact: true })}
        />
        <Metric
          label="Outstanding"
          value={formatCurrency(project.outstanding_total, project.currency, { compact: true })}
          tone={Number(project.outstanding_total) > 0 ? 'warning' : undefined}
        />
        <Metric
          label="Health score"
          value={project.health_score ?? '—'}
          hint={project.health_score ? `out of 100` : 'Not scored yet'}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Delivery</CardTitle>
            <CardDescription>How this engagement is resourced.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              <Detail label="Status" value={statusLabel(project.status)} />
              <Detail label="Type" value={`${project.project_type} · ${project.category}`} />
              <Detail label="Owner" value={project.owner_name ?? project.owner_user_id ?? '—'} mono={!project.owner_name} />
              <Detail label="Client" value={project.client_name ?? project.client_company_id ?? '—'} mono={!project.client_name} />
              <Detail label="Start" value={project.start_date ? formatDate(project.start_date) : '—'} />
              <Detail
                label="Estimated end"
                value={project.estimated_end_date ? formatDate(project.estimated_end_date) : '—'}
              />
              <Detail label="Estimated hours" value={project.estimated_hours ?? '—'} />
              <Detail
                label="Estimated budget"
                value={project.estimated_budget
                  ? `${formatCurrency(project.estimated_budget, project.currency)} ${project.currency}`
                  : '—'}
              />
              <Detail label="Billing" value={`${statusLabel(project.billing_basis)} · ${statusLabel(project.billing_frequency)}`} />
              <Detail label="Payment terms" value={`${project.payment_terms_days} days`} />
              <Detail label="Billing status" value={statusLabel(project.billing_status)} />
              <Detail
                label="Last activity"
                value={project.last_activity_at ? formatDateTime(project.last_activity_at) : '—'}
              />
            </dl>

            {project.description ? (
              <div className="mt-5 border-t border-border pt-4">
                <p className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
                  Description
                </p>
                <p className="mt-1.5 whitespace-pre-wrap text-sm text-muted-foreground">
                  {project.description}
                </p>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Health</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <HealthScore score={project.health_score} />
              <ul className="space-y-1.5 text-sm">
                <Row label="Team size" value={project.team_size} />
                <Row label="Open roles" value={project.open_role_count} tone={project.open_role_count > 0 ? 'warning' : undefined} />
                <Row label="Roles defined" value={project.role_count} />
                <Row label="SOWs" value={project.sow_count} />
                <Row label="Contracts" value={project.contract_count} />
                <Row label="Timesheets" value={project.timesheet_count} />
              </ul>
            </CardContent>
          </Card>

          {billing && billing.invoices_issued !== undefined ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Wallet aria-hidden className="size-4 text-primary" />
                  Billing position
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1.5 text-sm">
                  <Row label="Invoices issued" value={billing.invoices_issued ?? 0} />
                  <Row
                    label="Invoiced"
                    value={`${formatCurrency(billing.invoiced ?? '0', project.currency)} ${project.currency}`}
                  />
                  <Row
                    label="Outstanding"
                    value={`${formatCurrency(billing.outstanding ?? '0', project.currency)} ${project.currency}`}
                  />
                  <Row
                    label="Overdue invoices"
                    value={billing.overdue_invoices ?? 0}
                    tone={(billing.overdue_invoices ?? 0) > 0 ? 'danger' : undefined}
                  />
                  <Row
                    label="Unbilled timesheets"
                    value={billing.unbilled_timesheets ?? 0}
                    hint={formatHours(billing.unbilled_hours ?? '0')}
                  />
                </ul>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>

      <QuickLists roles={roles} sows={sows} contracts={contracts} />
    </div>
  )
}

function QuickLists({
  roles,
  sows,
  contracts,
}: {
  roles: ProjectRole[]
  sows: Sow[]
  contracts: Contract[]
}) {
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users aria-hidden className="size-4 text-primary" />
            Roles needing people
          </CardTitle>
        </CardHeader>
        <CardContent>
          {roles.filter((role) => role.status === 'OPEN').length === 0 ? (
            <EmptyState title="Every role is filled" description="No open roles on this project." />
          ) : (
            <ul className="space-y-2">
              {roles
                .filter((role) => role.status === 'OPEN')
                .slice(0, 5)
                .map((role) => (
                  <li key={role.public_id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">{role.title}</span>
                    <span className="shrink-0 text-xs text-muted-foreground tabular">
                      {role.allocated_count}/{role.required_count}
                    </span>
                  </li>
                ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileSignature aria-hidden className="size-4 text-primary" />
            SOWs
          </CardTitle>
        </CardHeader>
        <CardContent>
          {sows.length === 0 ? (
            <EmptyState title="No SOWs" description="Scope the work before contracting." />
          ) : (
            <ul className="space-y-2">
              {sows.slice(0, 5).map((sow) => (
                <li key={sow.public_id} className="flex items-center justify-between gap-2">
                  <Link
                    href={`/sows/${sow.public_id}`}
                    className="truncate text-sm font-medium hover:text-primary-strong"
                  >
                    {sow.title}
                  </Link>
                  <StatusBadge status={sow.status} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileText aria-hidden className="size-4 text-primary" />
            Contracts
          </CardTitle>
        </CardHeader>
        <CardContent>
          {contracts.length === 0 ? (
            <EmptyState title="No contracts" description="Approve a SOW to generate contracts." />
          ) : (
            <ul className="space-y-2">
              {contracts.slice(0, 5).map((contract) => (
                <li key={contract.public_id} className="flex items-center justify-between gap-2">
                  <Link
                    href={`/contracts/${contract.public_id}`}
                    className="truncate text-sm font-medium hover:text-primary-strong"
                  >
                    {contract.title}
                  </Link>
                  <StatusBadge status={contract.status} />
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">{label}</dt>
      <dd className={mono ? 'truncate font-mono text-xs' : 'truncate'}>{value}</dd>
    </div>
  )
}

function Row({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: React.ReactNode
  hint?: string
  tone?: 'warning' | 'danger'
}) {
  return (
    <li className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex items-baseline gap-1.5">
        <span
          className={`font-medium tabular ${
            tone === 'danger' ? 'text-danger' : tone === 'warning' ? 'text-warning' : ''
          }`}
        >
          {value}
        </span>
        {hint ? <span className="text-2xs text-muted-foreground">{hint}</span> : null}
      </span>
    </li>
  )
}

/* -------------------------------------------------------------------------- */
/* Roles                                                                      */
/* -------------------------------------------------------------------------- */

function RolesTab({
  roles,
  projectId,
  canManage,
}: {
  roles: ProjectRole[]
  projectId: string
  canManage: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Users aria-hidden className="size-4 text-primary" />
              Roles
            </CardTitle>
            <CardDescription>
              The positions this project needs. A role is what a SOW and a contract
              are priced against.
            </CardDescription>
          </div>
          {canManage ? <CreateRoleDialog projectId={projectId} /> : null}
        </div>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={roleColumns}
          rows={roles}
          rowKey={(row) => row.public_id}
          caption="Roles defined on this project"
          exportName={`project-${projectId}-roles`}
          csv={[
            { header: 'Role ID', value: (row) => row.public_id },
            { header: 'Title', value: (row) => row.title },
            { header: 'Status', value: (row) => row.status },
            { header: 'Required', value: (row) => row.required_count },
            { header: 'Allocated', value: (row) => row.allocated_count },
            { header: 'Currency', value: (row) => row.currency },
            { header: 'Min rate', value: (row) => row.min_hourly_rate },
            { header: 'Max rate', value: (row) => row.max_hourly_rate },
            { header: 'Skills', value: (row) => row.required_skills.join('; ') },
          ]}
          emptyState={
            <EmptyState
              icon={<Users aria-hidden />}
              title="No roles defined"
              description="Define the positions this project needs, then price them in a SOW."
              action={
                canManage ? (
                  <CreateRoleDialog projectId={projectId} triggerLabel="Define the first role" />
                ) : undefined
              }
            />
          }
        />
      </CardContent>
    </Card>
  )
}

const roleColumns: Column<ProjectRole>[] = [
  {
    key: 'title',
    header: 'Role',
    cell: (row) => (
      <div className="min-w-0">
        <p className="truncate font-medium">{row.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {row.required_skills.length > 0 ? row.required_skills.join(', ') : 'No skills listed'}
        </p>
      </div>
    ),
  },
  {
    key: 'id',
    header: 'Role ID',
    hideBelow: 'lg',
    cell: (row) => <PublicId value={row.public_id} kind="role" />,
  },
  {
    key: 'status',
    header: 'Status',
    hideBelow: 'sm',
    cell: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: 'fill',
    header: 'Filled',
    numeric: true,
    cell: (row) => (
      <span className={row.allocated_count > row.required_count ? 'text-warning' : undefined}>
        {row.allocated_count}/{row.required_count}
      </span>
    ),
  },
  {
    key: 'rate',
    header: 'Rate range',
    numeric: true,
    hideBelow: 'md',
    cell: (row) =>
      row.min_hourly_rate || row.max_hourly_rate
        ? `${formatCurrency(row.min_hourly_rate ?? '0', row.currency, { compact: true })} – ${formatCurrency(row.max_hourly_rate ?? '0', row.currency, { compact: true })}`
        : '—',
  },
  {
    key: 'utilisation',
    header: 'Utilisation',
    numeric: true,
    hideBelow: 'lg',
    cell: (row) => formatPercent(Number(row.utilisation_pct) / 100, 0),
  },
  {
    key: 'dates',
    header: 'Dates',
    hideBelow: 'lg',
    cell: (row) =>
      row.start_date ? `${formatDate(row.start_date)} – ${row.end_date ? formatDate(row.end_date) : 'open'}` : '—',
  },
]

function CreateRoleDialog({
  projectId,
  triggerLabel = 'New role',
}: {
  projectId: string
  triggerLabel?: string
}) {
  const { activeCompanyPublicId, activeCompany } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [title, setTitle] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [requiredCount, setRequiredCount] = React.useState('1')
  const [seniority, setSeniority] = React.useState('')
  const [currency, setCurrency] = React.useState(activeCompany?.default_currency ?? 'USD')
  const [minRate, setMinRate] = React.useState('')
  const [maxRate, setMaxRate] = React.useState('')
  const [skills, setSkills] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open) {
      setTitle('')
      setDescription('')
      setRequiredCount('1')
      setSeniority('')
      setMinRate('')
      setMaxRate('')
      setSkills('')
      setError(null)
      setCurrency(activeCompany?.default_currency ?? 'USD')
    }
  }, [open, activeCompany?.default_currency])

  const create = useCompanyMutation<ProjectRole>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () => {
      if (title.trim().length < 2) {
        setError('Give the role a title of at least 2 characters.')
        return Promise.reject(new Error('validation'))
      }
      if (minRate && maxRate && Number(maxRate) < Number(minRate)) {
        setError('The maximum rate cannot be below the minimum rate.')
        return Promise.reject(new Error('validation'))
      }

      return api.post<ProjectRole>(
        `/project-roles?project_id=${encodeURIComponent(projectId)}`,
        {
          title: title.trim(),
          description: description.trim() || undefined,
          required_count: Number(requiredCount),
          required_skills: skills
            .split(',')
            .map((skill) => skill.trim())
            .filter(Boolean),
          seniority: seniority.trim() || undefined,
          currency,
          min_hourly_rate: minRate || undefined,
          max_hourly_rate: maxRate || undefined,
        },
        { companyPublicId: activeCompanyPublicId },
      )
    },
    invalidate: [['projects', 'detail', projectId]],
    onSuccess: (role) => {
      notifySuccess('Role created.', `${role.title} · ${role.public_id}`)
      setOpen(false)
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await create.mutateAsync()
    } catch (cause) {
      if (create.error) notifyError(cause, 'The role could not be created.')
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        {triggerLabel}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Define a role"
        description="A role is the position this project needs. Its rate becomes the basis for pricing in a SOW and a contract."
        className="max-w-xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="create-role" loading={create.isPending}>
              Create role
            </Button>
          </>
        }
      >
        <form id="create-role" onSubmit={submit} className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
          <Field label="Title" error={error ?? undefined} required>
            <Input
              id="role-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              aria-invalid={Boolean(error)}
              placeholder="Senior field engineer"
            />
          </Field>

          <Field label="Description">
            <Textarea
              id="role-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={2}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="How many people" required>
              <Input
                id="role-count"
                type="number"
                min="1"
                max="1000"
                value={requiredCount}
                onChange={(event) => setRequiredCount(event.target.value)}
              />
            </Field>
            <Field label="Seniority">
              <Input
                id="role-seniority"
                value={seniority}
                onChange={(event) => setSeniority(event.target.value)}
                placeholder="Senior"
              />
            </Field>
            <Field label="Currency">
              <Select id="role-currency" value={currency} onChange={(event) => setCurrency(event.target.value)}>
                {['USD', 'EUR', 'GBP', 'CAD', 'AUD'].map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Required skills" hint="Comma separated">
              <Input
                id="role-skills"
                value={skills}
                onChange={(event) => setSkills(event.target.value)}
                placeholder="Welding, OSHA 30"
              />
            </Field>
            <Field label="Minimum hourly rate">
              <Input
                id="role-min-rate"
                type="number"
                min="0"
                step="0.01"
                value={minRate}
                onChange={(event) => setMinRate(event.target.value)}
              />
            </Field>
            <Field label="Maximum hourly rate">
              <Input
                id="role-max-rate"
                type="number"
                min="0"
                step="0.01"
                value={maxRate}
                onChange={(event) => setMaxRate(event.target.value)}
              />
            </Field>
          </div>
        </form>
      </Dialog>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Team                                                                       */
/* -------------------------------------------------------------------------- */

function TeamTab({ team }: { team: Assignment[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Users aria-hidden className="size-4 text-primary" />
          Team
        </CardTitle>
        <CardDescription>
          Who is assigned to this project, on which contract and role.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={teamColumns}
          rows={team}
          rowKey={(row) => row.id}
          caption="Assignments on this project"
          exportName="project-team"
          csv={[
            { header: 'Person', value: (row) => row.user_name },
            { header: 'User ID', value: (row) => row.user_id },
            { header: 'Project role', value: (row) => row.role_title },
            { header: 'Contract', value: (row) => row.contract_id },
            { header: 'Status', value: (row) => row.status },
            { header: 'Allocation %', value: (row) => row.allocation_pct },
            { header: 'Hours to date', value: (row) => row.hours_to_date },
            { header: 'Start', value: (row) => row.start_date },
            { header: 'End', value: (row) => row.end_date },
          ]}
          emptyState={
            <EmptyState
              icon={<Users aria-hidden />}
              title="Nobody is assigned yet"
              description="Assignments are created from an active contract, and they are what timesheets are recorded against."
            />
          }
        />
      </CardContent>
    </Card>
  )
}

const teamColumns: Column<Assignment>[] = [
  {
    key: 'person',
    header: 'Person',
    cell: (row) => (
      <div className="min-w-0">
        <p className="truncate font-medium">{row.user_name ?? 'Unknown'}</p>
        <p className="font-mono text-2xs text-subtle-foreground">{row.user_id}</p>
      </div>
    ),
  },
  {
    key: 'role',
    header: 'Role',
    cell: (row) => (
      <div className="min-w-0">
        <p className="truncate">{row.role_title_override ?? row.role_title ?? '—'}</p>
        {row.role_id ? <PublicId value={row.role_id} kind="role" /> : null}
      </div>
    ),
  },
  {
    key: 'contract',
    header: 'Contract',
    hideBelow: 'lg',
    cell: (row) => (
      <div className="min-w-0">
        <p className="truncate">{row.contract_title ?? '—'}</p>
        <PublicId value={row.contract_id} kind="contract" />
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    hideBelow: 'sm',
    cell: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: 'allocation',
    header: 'Allocation',
    numeric: true,
    hideBelow: 'md',
    cell: (row) => `${row.allocation_pct}%`,
  },
  {
    key: 'hours',
    header: 'Hours to date',
    numeric: true,
    cell: (row) => formatHours(row.hours_to_date),
  },
  {
    key: 'dates',
    header: 'Dates',
    hideBelow: 'lg',
    cell: (row) => `${formatDate(row.start_date)}${row.end_date ? ` – ${formatDate(row.end_date)}` : ''}`,
  },
]

/* -------------------------------------------------------------------------- */
/* SOWs                                                                       */
/* -------------------------------------------------------------------------- */

function SowsTab({ sows, projectId, canCreate }: { sows: Sow[]; projectId: string; canCreate: boolean }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2">
              <FileSignature aria-hidden className="size-4 text-primary" />
              Statements of work
            </CardTitle>
            <CardDescription>
              Scope, deliverables and the roles this project sells.
            </CardDescription>
          </div>
          {canCreate ? (
            <Link href={`/sows?project_id=${projectId}&new=1`}>
              <Button size="sm">New SOW</Button>
            </Link>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={sowColumns}
          rows={sows}
          rowKey={(row) => row.public_id}
          caption="SOWs on this project"
          exportName="project-sows"
          csv={[
            { header: 'SOW ID', value: (row) => row.public_id },
            { header: 'Title', value: (row) => row.title },
            { header: 'Status', value: (row) => row.status },
            { header: 'Currency', value: (row) => row.currency },
            { header: 'Max total', value: (row) => row.max_total_amount },
            { header: 'Start', value: (row) => row.start_date },
            { header: 'End', value: (row) => row.end_date },
            { header: 'Contracts', value: (row) => row.contract_count },
          ]}
          emptyState={
            <EmptyState
              icon={<FileSignature aria-hidden />}
              title="No statements of work"
              description="A SOW scopes the work and prices the roles. Contracts are generated from it once approved."
              action={
                canCreate ? (
                  <Link href={`/sows?project_id=${projectId}&new=1`}>
                    <Button size="sm">Write the first SOW</Button>
                  </Link>
                ) : undefined
              }
            />
          }
        />
      </CardContent>
    </Card>
  )
}

const sowColumns: Column<Sow>[] = [
  {
    key: 'title',
    header: 'SOW',
    cell: (row) => (
      <div className="min-w-0">
        <Link
          href={`/sows/${row.public_id}`}
          className="block truncate font-medium hover:text-primary-strong"
        >
          {row.title}
        </Link>
        <PublicId value={row.public_id} kind="sow" />
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    hideBelow: 'sm',
    cell: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: 'counterparty',
    header: 'Counterparty',
    hideBelow: 'lg',
    cell: (row) => (
      <span className="text-muted-foreground">
        {row.counterparty_company_name ?? row.counterparty_user_name ?? '—'}
      </span>
    ),
  },
  {
    key: 'max',
    header: 'Capped at',
    numeric: true,
    hideBelow: 'md',
    cell: (row) =>
      row.max_total_amount ? formatCurrency(row.max_total_amount, row.currency, { compact: true }) : '—',
  },
  {
    key: 'dates',
    header: 'Period',
    hideBelow: 'md',
    cell: (row) =>
      row.start_date
        ? `${formatDate(row.start_date)} – ${row.end_date ? formatDate(row.end_date) : 'open'}`
        : '—',
  },
  {
    key: 'contracts',
    header: 'Contracts',
    numeric: true,
    cell: (row) => row.contract_count,
  },
]

/* -------------------------------------------------------------------------- */
/* Contracts                                                                  */
/* -------------------------------------------------------------------------- */

function ContractsTab({
  contracts,
  projectId,
  canCreate,
}: {
  contracts: Contract[]
  projectId: string
  canCreate: boolean
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2">
              <FileText aria-hidden className="size-4 text-primary" />
              Contracts
            </CardTitle>
            <CardDescription>What has actually been agreed, and its current state.</CardDescription>
          </div>
          {canCreate ? (
            <Link href={`/contracts?project_id=${projectId}&new=1`}>
              <Button size="sm">New contract</Button>
            </Link>
          ) : null}
        </div>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={contractColumns}
          rows={contracts}
          rowKey={(row) => row.public_id}
          caption="Contracts on this project"
          exportName="project-contracts"
          csv={[
            { header: 'Contract ID', value: (row) => row.public_id },
            { header: 'Title', value: (row) => row.title },
            { header: 'Status', value: (row) => row.status },
            { header: 'Currency', value: (row) => row.currency },
            { header: 'Contract value', value: (row) => row.contract_value },
            { header: 'Invoiced', value: (row) => row.invoiced_total },
            { header: 'Outstanding', value: (row) => row.outstanding_total },
            { header: 'Start', value: (row) => row.start_date },
            { header: 'End', value: (row) => row.end_date },
          ]}
          emptyState={
            <EmptyState
              icon={<FileText aria-hidden />}
              title="No contracts yet"
              description="Approve a SOW to generate contracts from it."
              action={
                canCreate ? (
                  <Link href={`/contracts?project_id=${projectId}&new=1`}>
                    <Button size="sm">Create a contract</Button>
                  </Link>
                ) : undefined
              }
            />
          }
        />
      </CardContent>
    </Card>
  )
}

const contractColumns: Column<Contract>[] = [
  {
    key: 'title',
    header: 'Contract',
    cell: (row) => (
      <div className="min-w-0">
        <Link
          href={`/contracts/${row.public_id}`}
          className="block truncate font-medium hover:text-primary-strong"
        >
          {row.title}
        </Link>
        <PublicId value={row.public_id} kind="contract" />
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    hideBelow: 'sm',
    cell: (row) => <StatusBadge status={row.status} />,
  },
  {
    key: 'counterparty',
    header: 'Counterparty',
    hideBelow: 'lg',
    cell: (row) => (
      <span className="text-muted-foreground">
        {row.counterparty_company_name ?? row.counterparty_user_name ?? '—'}
      </span>
    ),
  },
  {
    key: 'value',
    header: 'Value',
    numeric: true,
    cell: (row) =>
      row.contract_value ? formatCurrency(row.contract_value, row.currency, { compact: true }) : '—',
  },
  {
    key: 'outstanding',
    header: 'Outstanding',
    numeric: true,
    hideBelow: 'md',
    cell: (row) => formatCurrency(row.outstanding_total, row.currency, { compact: true }),
  },
  {
    key: 'dates',
    header: 'Period',
    hideBelow: 'lg',
    cell: (row) =>
      row.start_date
        ? `${formatDate(row.start_date)} – ${row.end_date ? formatDate(row.end_date) : 'open'}`
        : '—',
  },
]

/* -------------------------------------------------------------------------- */
/* Billing                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The project's billing position.
 *
 * Every figure comes from the aggregate, and the currency is the project's own:
 * a EUR project must never be summarised in dollars. Unbilled approved time is
 * shown alongside because it is the number that predicts the next invoice.
 */
function BillingTab({
  billing,
  currency,
  projectId,
}: {
  billing: ProjectBilling
  currency: string
  projectId: string
}) {
  const invoiced = billing.invoiced ?? '0'
  const outstanding = billing.outstanding ?? '0'
  const unbilledHours = billing.unbilled_hours ?? '0'

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Invoiced"
          value={formatCurrency(invoiced, currency, { compact: true })}
          hint={`${billing.invoices_issued ?? 0} invoices issued`}
        />
        <Metric
          label="Outstanding"
          value={formatCurrency(outstanding, currency, { compact: true })}
          tone={Number(outstanding) > 0 ? 'warning' : undefined}
        />
        <Metric
          label="Overdue invoices"
          value={billing.overdue_invoices ?? 0}
          tone={(billing.overdue_invoices ?? 0) > 0 ? 'danger' : undefined}
        />
        <Metric
          label="Unbilled time"
          value={formatHours(unbilledHours)}
          hint={`${billing.unbilled_timesheets ?? 0} approved timesheets not yet billed`}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Wallet aria-hidden className="size-4 text-primary" />
            How this project gets billed
          </CardTitle>
          <CardDescription>
            Amounts are computed on the server from approved timesheets and the
            contract role rates, never from a number typed into a form.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            To raise an invoice, open a contract from this project and run billing
            for the period. Previewing shows the exact line items first, and nothing
            is written until you generate.
          </p>
          <div className="flex flex-wrap gap-2">
            <Link href="/billing">
              <Button size="sm" variant="outline">
                Open billing
              </Button>
            </Link>
            <Link href={`/invoices?project_id=${encodeURIComponent(projectId)}`}>
              <Button size="sm" variant="ghost">
                See the invoices on this project
              </Button>
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Timesheets, invoices, documents (aggregate rows)                           */
/* -------------------------------------------------------------------------- */

/** Loose row shape: the dashboard aggregate is a projection, not a schema. */
type LooseRow = Record<string, unknown>

function str(row: LooseRow, key: string): string | null {
  const value = row[key]
  return typeof value === 'string' ? value : null
}

function num(row: LooseRow, key: string): number {
  const value = row[key]
  return typeof value === 'number' ? value : Number(value ?? 0)
}

function TimesheetsTab({ rows }: { rows: LooseRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock aria-hidden className="size-4 text-primary" />
          Timesheets
        </CardTitle>
        <CardDescription>The most recent timesheets recorded against this project.</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={[
            {
              key: 'id',
              header: 'Timesheet',
              cell: (row) => <PublicId value={str(row, 'public_id')} />,
            },
            {
              key: 'person',
              header: 'Person',
              hideBelow: 'sm',
              cell: (row) => str(row, 'user_name') ?? str(row, 'user_id') ?? '—',
            },
            {
              key: 'period',
              header: 'Period',
              cell: (row) => {
                const start = str(row, 'period_start')
                const end = str(row, 'period_end')
                return start && end ? `${formatDate(start)} – ${formatDate(end)}` : '—'
              },
            },
            {
              key: 'status',
              header: 'Status',
              hideBelow: 'md',
              cell: (row) => <StatusBadge status={str(row, 'status')} />,
            },
            {
              key: 'hours',
              header: 'Hours',
              numeric: true,
              cell: (row) => formatHours(num(row, 'total_hours')),
            },
            {
              key: 'billable',
              header: 'Billable',
              numeric: true,
              hideBelow: 'md',
              cell: (row) => formatHours(num(row, 'billable_hours')),
            },
          ]}
          rows={rows}
          rowKey={(row, index) => str(row, 'public_id') ?? `row-${index}`}
          caption="Timesheets on this project"
          exportName="project-timesheets"
          csv={[
            { header: 'Timesheet ID', value: (row) => str(row, 'public_id') },
            { header: 'Person', value: (row) => str(row, 'user_name') },
            { header: 'Period start', value: (row) => str(row, 'period_start') },
            { header: 'Period end', value: (row) => str(row, 'period_end') },
            { header: 'Status', value: (row) => str(row, 'status') },
            { header: 'Total hours', value: (row) => String(num(row, 'total_hours')) },
            { header: 'Billable hours', value: (row) => String(num(row, 'billable_hours')) },
          ]}
          emptyState={
            <EmptyState
              icon={<Clock aria-hidden />}
              title="No timesheets yet"
              description="Timesheets appear once people assigned to this project record time."
            />
          }
        />
      </CardContent>
    </Card>
  )
}

function InvoicesTab({ rows, currency }: { rows: LooseRow[]; currency: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Receipt aria-hidden className="size-4 text-primary" />
          Invoices
        </CardTitle>
        <CardDescription>Billing raised against this project.</CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={[
            {
              key: 'id',
              header: 'Invoice',
              cell: (row) => (
                <div className="min-w-0">
                  <PublicId value={str(row, 'public_id')} kind="invoice" />
                  <p className="truncate text-xs text-muted-foreground">
                    {str(row, 'invoice_number') ?? 'Not numbered'}
                  </p>
                </div>
              ),
            },
            {
              key: 'status',
              header: 'Status',
              hideBelow: 'sm',
              cell: (row) => <StatusBadge status={str(row, 'status')} />,
            },
            {
              key: 'due',
              header: 'Due',
              hideBelow: 'md',
              cell: (row) => {
                const due = str(row, 'due_date')
                return due ? formatDate(due) : '—'
              },
            },
            {
              key: 'total',
              header: 'Total',
              numeric: true,
              cell: (row) =>
                formatCurrency(
                  String(row.total_amount ?? '0'),
                  str(row, 'currency') ?? currency,
                ),
            },
            {
              key: 'balance',
              header: 'Balance',
              numeric: true,
              cell: (row) =>
                formatCurrency(
                  String(row.balance_due ?? '0'),
                  str(row, 'currency') ?? currency,
                ),
            },
          ]}
          rows={rows}
          rowKey={(row, index) => str(row, 'public_id') ?? `row-${index}`}
          caption="Invoices on this project"
          exportName="project-invoices"
          csv={[
            { header: 'Invoice ID', value: (row) => str(row, 'public_id') },
            { header: 'Invoice number', value: (row) => str(row, 'invoice_number') },
            { header: 'Status', value: (row) => str(row, 'status') },
            { header: 'Due', value: (row) => str(row, 'due_date') },
            { header: 'Total', value: (row) => String(row.total_amount ?? '') },
            { header: 'Balance due', value: (row) => String(row.balance_due ?? '') },
            { header: 'Currency', value: (row) => str(row, 'currency') },
          ]}
          emptyState={
            <EmptyState
              icon={<Receipt aria-hidden />}
              title="Nothing billed yet"
              description="Run billing for a contract period to raise the first invoice."
            />
          }
        />
      </CardContent>
    </Card>
  )
}

function DocumentsTab({ rows }: { rows: LooseRow[] }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Paperclip aria-hidden className="size-4 text-primary" />
              Documents
            </CardTitle>
            <CardDescription>Files attached to this project.</CardDescription>
          </div>
          <Link href="/documents">
            <Button size="sm" variant="outline">
              All documents
            </Button>
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState
            icon={<Paperclip aria-hidden />}
            title="No documents attached"
            description="Upload contracts, SOWs and site paperwork to keep them with the project."
            action={
              <Link href="/documents">
                <Button size="sm">Go to documents</Button>
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {rows.map((row, index) => (
              <li key={str(row, 'public_id') ?? index} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{str(row, 'title') ?? 'Untitled'}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {str(row, 'doc_type') ?? 'OTHER'}
                    {str(row, 'file_name') ? ` · ${str(row, 'file_name')}` : ''}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {str(row, 'status') ? <StatusBadge status={str(row, 'status')} /> : null}
                  <Link href={`/documents/${str(row, 'public_id')}`} className="text-xs font-medium text-primary hover:underline">
                    Open
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Activity and insights                                                      */
/* -------------------------------------------------------------------------- */

function ActivityTab({ entries }: { entries: ProjectDashboard['activity'] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity aria-hidden className="size-4 text-primary" />
          Activity
        </CardTitle>
        <CardDescription>What has happened on this project, most recent first.</CardDescription>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <EmptyState
            icon={<Activity aria-hidden />}
            title="No recorded activity"
            description="Changes to this project and its commercial records appear here."
          />
        ) : (
          <ol className="relative space-y-4 border-l border-border pl-4">
            {entries.map((entry, index) => (
              <li key={`${entry.at}-${index}`} className="relative">
                <span
                  aria-hidden
                  className="absolute -left-[21px] top-1.5 size-2 rounded-full bg-primary"
                />
                <p className="text-sm font-medium">{entry.action}</p>
                <p className="text-xs text-muted-foreground">
                  {entry.actor_name ?? 'System'}
                  {entry.resource_type ? ` · ${entry.resource_type}` : ''} ·{' '}
                  {formatDateTime(entry.at)}
                </p>
                {entry.resource_public_id ? (
                  <PublicId value={entry.resource_public_id} className="mt-1" />
                ) : null}
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  )
}

function InsightsTab({ data }: { data: ProjectDashboard }) {
  return (
    <div className="space-y-6">
      {data.insights.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Brain aria-hidden />}
            title="No insights for this project yet"
            description="Insights are generated from your own records once there is enough activity to analyse, and each one cites what it is based on."
          />
        </Card>
      ) : (
        data.insights.map((insight, index) => (
          <Card key={insight.public_id ?? `insight-${index}`}>
            <CardHeader>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <CardTitle className="flex items-center gap-2">
                    <Sparkles aria-hidden className="size-4 text-primary" />
                    {insight.title ?? insight.kind}
                  </CardTitle>
                  <CardDescription className="mt-1">
                    {insight.kind}
                    {insight.score !== undefined ? ` · health score ${insight.score}` : ''}
                  </CardDescription>
                </div>
                {insight.severity ? <StatusBadge status={insight.severity} /> : null}
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm">{insight.summary}</p>

              {insight.signals && insight.signals.length > 0 ? (
                <ul className="space-y-1">
                  {insight.signals.map((signal, signalIndex) => (
                    <li key={signalIndex} className="flex items-start gap-2 text-sm text-muted-foreground">
                      <span aria-hidden className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground" />
                      {signal}
                    </li>
                  ))}
                </ul>
              ) : null}

              <Link
                href={`/ai/insights?entity_type=PROJECT&q=${encodeURIComponent(data.project.public_id)}`}
                className="inline-block text-sm font-medium text-primary hover:underline"
              >
                See all project insights
              </Link>
            </CardContent>
          </Card>
        ))
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FolderKanban aria-hidden className="size-4 text-primary" />
            Project intelligence
          </CardTitle>
          <CardDescription>
            Ask the assistant about the margin, risk or billing position of this project.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Link href={`/assistant?q=${encodeURIComponent(`Summarise the commercial position of ${data.project.name}`)}`}>
            <Button variant="outline" size="sm">
              <Sparkles aria-hidden />
              Ask about this project
            </Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Edit and archive                                                           */
/* -------------------------------------------------------------------------- */

function EditProjectDialog({
  project,
  onSaved,
}: {
  project: import('@/lib/domain-types').Project
  onSaved: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState(project.name)
  const [description, setDescription] = React.useState(project.description ?? '')
  const [status, setStatus] = React.useState(project.status)
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (open) {
      setName(project.name)
      setDescription(project.description ?? '')
      setStatus(project.status)
      setError(null)
    }
  }, [open, project])

  const save = useCompanyMutation<import('@/lib/domain-types').Project>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () =>
      api.patch<import('@/lib/domain-types').Project>(
        `/projects/${project.public_id}`,
        {
          name: name.trim(),
          description: description.trim() || null,
          status,
        },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['projects', 'detail', project.public_id]],
    onSuccess: () => {
      notifySuccess('Project updated.')
      setOpen(false)
      onSaved()
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (name.trim().length < 2) {
      setError('Give the project a name of at least 2 characters.')
      return
    }
    setError(null)
    try {
      await save.mutateAsync()
    } catch (cause) {
      notifyError(cause, 'The project could not be updated.')
    }
  }

  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        Edit
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Edit project"
        description="Name, description and status. Commercial terms that have been contracted are changed on the contract, not here."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={save.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="edit-project" loading={save.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        <form id="edit-project" onSubmit={submit} className="space-y-4">
          <Field label="Name" error={error ?? undefined} required>
            <Input
              id="edit-project-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={Boolean(error)}
            />
          </Field>
          <Field label="Description">
            <Textarea
              id="edit-project-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={4}
            />
          </Field>
          <Field label="Status">
            <Select id="edit-project-status" value={status} onChange={(event) => setStatus(event.target.value)}>
              {[project.status, ...(project.allowed_transitions ?? [])].map((value) => (
                <option key={value} value={value}>
                  {statusLabel(value)}
                </option>
              ))}
            </Select>
            {(project.allowed_transitions ?? []).length === 0 ? (
              <p className="text-xs text-muted-foreground">
                This project is in a terminal state and its status cannot change.
              </p>
            ) : null}
          </Field>
        </form>
      </Dialog>
    </>
  )
}

function ArchiveProjectButton({
  projectId,
  projectName,
  onArchived,
}: {
  projectId: string
  projectName: string
  onArchived: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [open, setOpen] = React.useState(false)

  const archive = useCompanyMutation<unknown, string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (reason) =>
      api.delete(`/projects/${projectId}?reason=${encodeURIComponent(reason)}`, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [['projects']],
    onSuccess: () => {
      notifySuccess('Project archived.', `${projectName} is archived.`)
      setOpen(false)
      onArchived()
    },
  })

  return (
    <>
      <Button size="sm" variant="ghost" className="text-danger hover:bg-danger-soft" onClick={() => setOpen(true)}>
        Archive
      </Button>
      <ReasonDialog
        open={open}
        onOpenChange={setOpen}
        title={`Archive ${projectName}`}
        description="Archiving hides the project from lists and blocks new work on it. Its contracts, timesheets and invoices are retained for audit and the server records who archived it and why."
        confirmLabel="Archive project"
        label="Reason for archiving"
        busy={archive.isPending}
        error={archive.isError ? archive.error : null}
        onConfirm={(reason) => archive.mutate(reason)}
      />
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Re-exports so sibling tab routes can reuse the shell                        */
/* -------------------------------------------------------------------------- */

export type { Invoice, PageEnvelope }
