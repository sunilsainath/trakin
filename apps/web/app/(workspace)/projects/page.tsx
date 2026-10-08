'use client'

import * as React from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { z } from 'zod'
import { Plus, FolderKanban } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate } from '@/lib/utils'
import { statusLabel } from '@/lib/status'
import {
  BILLING_BASES,
  BILLING_FREQUENCIES,
  PROJECT_STATUSES,
  type Page as PageEnvelope,
  type Project,
} from '@/lib/domain-types'
import {
  Button,
  Dialog,
  EmptyState,
  Input,
  Select,
  Tabs,
  Textarea,
} from '@/components/ui'
import { CurrencySelect, DateInput, Field, FieldGrid } from '@/components/forms'
import { notifyError, notifySuccess } from '@/components/toast'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { FilterBar, FilterInput, FilterSelect, useDebouncedValue } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable, PermissionState, isPermissionError, useCompanyQuery } from '@/components/query'

/* -------------------------------------------------------------------------- */
/* Create project                                                             */
/* -------------------------------------------------------------------------- */

const projectSchema = z
  .object({
    name: z.string().trim().min(2, 'Give the project a name of at least 2 characters.').max(200),
    description: z.string().trim().max(8000).optional(),
    project_type: z.enum(['SERVICE', 'LENDING', 'BLUE_COLLAR']),
    category: z.enum(['COMPANY', 'INDIVIDUAL']),
    status: z.enum(['DRAFT', 'PLANNING', 'ACTIVE', 'ON_HOLD', 'COMPLETED', 'CANCELLED']),
    start_date: z.string().optional(),
    estimated_end_date: z.string().optional(),
    estimated_hours: z.string().optional(),
    estimated_budget: z.string().optional(),
    currency: z.string().length(3),
    billing_basis: z.enum(['TIMESHEET', 'FIXED', 'RECURRING', 'USAGE', 'MILESTONE']),
    billing_frequency: z.enum(['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM']),
    payment_terms_days: z.string(),
  })
  .refine(
    (values) =>
      !values.start_date ||
      !values.estimated_end_date ||
      values.estimated_end_date >= values.start_date,
    { message: 'The end date cannot be before the start date.', path: ['estimated_end_date'] },
  )

type ProjectFormValues = z.infer<typeof projectSchema>

/** Empty string becomes undefined, because the API treats null and missing alike. */
function optional(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

function CreateProjectDialog({ triggerLabel = 'New project' }: { triggerLabel?: string }) {
  const router = useRouter()
  const { activeCompanyPublicId, activeCompany, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [values, setValues] = React.useState<ProjectFormValues>(emptyProject(activeCompany?.default_currency))
  const [errors, setErrors] = React.useState<Partial<Record<string, string>>>({})

  React.useEffect(() => {
    if (open) {
      setValues(emptyProject(activeCompany?.default_currency))
      setErrors({})
    }
  }, [open, activeCompany?.default_currency])

  const create = useCompanyMutation<Project, ProjectFormValues>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (form) =>
      api.post<Project>(
        '/projects',
        {
          name: form.name,
          description: optional(form.description),
          project_type: form.project_type,
          category: form.category,
          status: form.status,
          start_date: optional(form.start_date),
          estimated_end_date: optional(form.estimated_end_date),
          estimated_hours: optional(form.estimated_hours),
          estimated_budget: optional(form.estimated_budget),
          currency: form.currency,
          billing_basis: form.billing_basis,
          billing_frequency: form.billing_frequency,
          payment_terms_days: Number(form.payment_terms_days),
        },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['projects']],
    onSuccess: (project) => {
      notifySuccess('Project created.', `${project.name} · ${project.public_id}`)
      setOpen(false)
      router.push(`/projects/${project.public_id}`)
    },
  })

  const set = <K extends keyof ProjectFormValues>(key: K, value: ProjectFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()

    const parsed = projectSchema.safeParse(values)
    if (!parsed.success) {
      const next: Partial<Record<string, string>> = {}
      for (const issue of parsed.error.issues) {
        const key = String(issue.path[0] ?? '')
        if (key && !next[key]) next[key] = issue.message
      }
      setErrors(next)
      return
    }

    setErrors({})

    try {
      await create.mutateAsync(parsed.data)
    } catch (cause) {
      notifyError(cause, 'The project could not be created.')
    }
  }

  if (!can('projects.create')) {
    return (
      <p className="text-sm text-muted-foreground">
        You do not have permission to create projects in this company.
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
        title="New project"
        description="A project holds the roles, SOWs, contracts and invoices for one engagement."
        className="max-w-2xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="create-project" loading={create.isPending}>
              Create project
            </Button>
          </>
        }
      >
        <form id="create-project" onSubmit={submit} className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
          <Field label="Name" error={errors.name} required>
            <Input
              id="project-name"
              value={values.name}
              onChange={(event) => set('name', event.target.value)}
              aria-invalid={Boolean(errors.name)}
              placeholder="Warehouse automation rollout"
            />
          </Field>

          <Field label="Description" error={errors.description}>
            <Textarea
              id="project-description"
              value={values.description ?? ''}
              onChange={(event) => set('description', event.target.value)}
              rows={3}
            />
          </Field>

          <FieldGrid>
            <EnumField
              id="project-type"
              label="Project type"
              value={values.project_type}
              onChange={(v) => set('project_type', v as ProjectFormValues['project_type'])}
              options={['SERVICE', 'LENDING', 'BLUE_COLLAR']}
            />
            <EnumField
              id="project-category"
              label="Category"
              value={values.category}
              onChange={(v) => set('category', v as ProjectFormValues['category'])}
              options={['COMPANY', 'INDIVIDUAL']}
            />
            <EnumField
              id="project-new-status"
              label="Status"
              value={values.status}
              onChange={(v) => set('status', v as ProjectFormValues['status'])}
              options={[...PROJECT_STATUSES]}
            />
            <Field label="Currency">
              <CurrencySelect
                id="project-currency"
                value={values.currency}
                onChange={(v) => set('currency', v)}
              />
            </Field>
            <Field label="Start date" error={errors.start_date}>
              <DateInput
                id="project-start"
                value={values.start_date ?? ''}
                onChange={(v) => set('start_date', v)}
              />
            </Field>
            <Field label="Estimated end" error={errors.estimated_end_date}>
              <DateInput
                id="project-end"
                value={values.estimated_end_date ?? ''}
                onChange={(v) => set('estimated_end_date', v)}
              />
            </Field>
            <Field label="Estimated hours" error={errors.estimated_hours} hint="Optional">
              <Input
                id="project-hours"
                type="number"
                min="0"
                step="0.5"
                value={values.estimated_hours ?? ''}
                onChange={(event) => set('estimated_hours', event.target.value)}
              />
            </Field>
            <Field
              label="Estimated budget"
              error={errors.estimated_budget}
              hint={`In ${values.currency}`}
            >
              <Input
                id="project-budget"
                type="number"
                min="0"
                step="0.01"
                value={values.estimated_budget ?? ''}
                onChange={(event) => set('estimated_budget', event.target.value)}
              />
            </Field>
            <EnumField
              id="project-basis"
              label="Billing basis"
              value={values.billing_basis}
              onChange={(v) => set('billing_basis', v as ProjectFormValues['billing_basis'])}
              options={[...BILLING_BASES]}
            />
            <EnumField
              id="project-frequency"
              label="Billing frequency"
              value={values.billing_frequency}
              onChange={(v) => set('billing_frequency', v as ProjectFormValues['billing_frequency'])}
              options={[...BILLING_FREQUENCIES]}
            />
            <Field label="Payment terms (days)" error={errors.payment_terms_days}>
              <Input
                id="project-terms"
                type="number"
                min="0"
                max="365"
                value={values.payment_terms_days}
                onChange={(event) => set('payment_terms_days', event.target.value)}
              />
            </Field>
          </FieldGrid>
        </form>
      </Dialog>
    </>
  )
}

/**
 * A native select for an enum field.
 *
 * Options are the enum values verbatim rather than display labels: the API
 * rejects anything else, and the values are already readable (`TIMESHEET`).
 */
function EnumField({
  id,
  label,
  value,
  onChange,
  options,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  options: readonly string[]
}) {
  return (
    <Field label={label}>
      <Select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {statusLabel(option)}
          </option>
        ))}
      </Select>
    </Field>
  )
}

function emptyProject(currency?: string): ProjectFormValues {
  return {
    name: '',
    description: '',
    project_type: 'SERVICE',
    category: 'COMPANY',
    status: 'DRAFT',
    start_date: '',
    estimated_end_date: '',
    estimated_hours: '',
    estimated_budget: '',
    currency: currency ?? 'USD',
    billing_basis: 'TIMESHEET',
    billing_frequency: 'MONTHLY',
    payment_terms_days: '30',
  }
}

/* -------------------------------------------------------------------------- */

export default function ProjectsPage() {
  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Projects' }]}
          title="Projects"
          description="Every engagement you deliver, with its commercial position at a glance."
          actions={<CreateProjectDialog />}
        />
        <ProjectsList />
      </div>
    </PageShell>
  )
}

function ProjectsList() {
  const { activeCompanyPublicId, me } = useCompany()

  const [status, setStatus] = React.useState('')
  const [owner, setOwner] = React.useState('')
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())
  const [sort, setSort] = React.useState<{ key: string; direction: 'asc' | 'desc' } | null>(null)

  const resetKey = `${status}|${owner}|${debouncedSearch}`
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [history, setHistory] = React.useState<Array<string | null>>([null])

  React.useEffect(() => {
    setCursor(null)
    setHistory([null])
  }, [resetKey])

  const queryParams = React.useMemo(() => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (owner) params.set('owner_user_id', owner)
    if (debouncedSearch) params.set('q', debouncedSearch)
    params.set('limit', '25')
    if (cursor) params.set('cursor', cursor)
    return `?${params.toString()}`
  }, [status, owner, debouncedSearch, cursor])

  const query = useCompanyQuery<PageEnvelope<Project>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['projects', 'list', status, owner, debouncedSearch, cursor],
    path: '/projects',
    queryParams,
  })

  const rows = query.data?.data ?? []
  const meta = query.data?.meta

  const columns: Column<Project>[] = [
    {
      key: 'name',
      header: 'Project',
      sortValue: (row) => row.name,
      cell: (row) => (
        <div className="min-w-0">
          <Link
            href={`/projects/${row.public_id}`}
            className="block truncate font-medium text-foreground hover:text-primary-strong"
          >
            {row.name}
          </Link>
          <span className="font-mono text-2xs text-subtle-foreground">{row.public_id}</span>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      hideBelow: 'sm',
      sortValue: (row) => row.status,
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'client',
      header: 'Client',
      hideBelow: 'lg',
      sortValue: (row) => row.client_name,
      cell: (row) => <span className="text-muted-foreground">{row.client_name ?? '—'}</span>,
    },
    {
      key: 'team',
      header: 'Team',
      numeric: true,
      hideBelow: 'md',
      sortValue: (row) => row.team_size,
      cell: (row) => row.team_size,
    },
    {
      key: 'roles',
      header: 'Open roles',
      numeric: true,
      hideBelow: 'lg',
      sortValue: (row) => row.open_role_count,
      cell: (row) => row.open_role_count,
    },
    {
      key: 'budget',
      header: 'Budget',
      numeric: true,
      sortValue: (row) => (row.estimated_budget ? Number(row.estimated_budget) : null),
      cell: (row) =>
        row.estimated_budget ? formatCurrency(row.estimated_budget, row.currency, { compact: true }) : '—',
    },
    {
      key: 'invoiced',
      header: 'Invoiced',
      numeric: true,
      hideBelow: 'md',
      sortValue: (row) => Number(row.invoiced_total),
      cell: (row) => formatCurrency(row.invoiced_total, row.currency, { compact: true }),
    },
    {
      key: 'outstanding',
      header: 'Outstanding',
      numeric: true,
      sortValue: (row) => Number(row.outstanding_total),
      cell: (row) => formatCurrency(row.outstanding_total, row.currency, { compact: true }),
    },
    {
      key: 'end',
      header: 'Ends',
      hideBelow: 'lg',
      sortValue: (row) => row.estimated_end_date,
      cell: (row) => (row.estimated_end_date ? formatDate(row.estimated_end_date) : '—'),
    },
  ]

  if (query.isPending) return <LoadingTable rows={8} columns={6} />

  if (query.isError) {
    return isPermissionError(query.error) ? (
      <PermissionState error={query.error} />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} />
    )
  }

  const activeFilters = (status ? 1 : 0) + (owner ? 1 : 0) + (debouncedSearch ? 1 : 0)

  const activeTab = owner ? 'mine' : status || 'all'
  const selectTab = (key: string) => {
    if (key === 'mine') {
      setOwner(me?.public_id ?? '')
      setStatus('')
    } else {
      setOwner('')
      setStatus(key === 'all' ? '' : key)
    }
  }

  return (
    <div className="space-y-4">
      <FilterBar
        activeCount={activeFilters}
        onClear={() => {
          setStatus('')
          setOwner('')
          setSearch('')
        }}
      >
        <FilterInput
          id="project-search"
          label="Search"
          value={search}
          onChange={setSearch}
          placeholder="Name or description"
          className="min-w-56 flex-1"
        />
        <FilterSelect
          id="project-status"
          label="Status"
          value={status}
          onChange={setStatus}
          options={[...PROJECT_STATUSES]}
          className="w-44"
        />
      </FilterBar>

      <Tabs
        tabs={[
          { key: 'all', label: 'All' },
          { key: 'mine', label: 'Mine' },
          { key: 'ACTIVE', label: 'Active' },
          { key: 'DRAFT', label: 'Draft' },
          { key: 'ON_HOLD', label: 'On hold' },
          { key: 'COMPLETED', label: 'Completed' },
        ]}
        active={['all', 'mine', 'ACTIVE', 'DRAFT', 'ON_HOLD', 'COMPLETED'].includes(activeTab) ? activeTab : 'all'}
        onChange={selectTab}
        className="overflow-x-auto scrollbar-thin"
      />

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.public_id}
        caption="Projects in this company"
        exportName="projects"
        sort={sort}
        onSortChange={(key, direction) =>
          setSort(direction ? { key, direction } : null)
        }
        csv={[
          { header: 'Project ID', value: (row) => row.public_id },
          { header: 'Name', value: (row) => row.name },
          { header: 'Status', value: (row) => row.status },
          { header: 'Client', value: (row) => row.client_name },
          { header: 'Currency', value: (row) => row.currency },
          { header: 'Estimated budget', value: (row) => row.estimated_budget },
          { header: 'Invoiced', value: (row) => row.invoiced_total },
          { header: 'Outstanding', value: (row) => row.outstanding_total },
          { header: 'Team size', value: (row) => row.team_size },
          { header: 'Open roles', value: (row) => row.open_role_count },
          { header: 'Estimated end', value: (row) => row.estimated_end_date },
        ]}
        emptyState={
          <EmptyState
            icon={<FolderIcon />}
            title={activeFilters > 0 ? 'No projects match those filters' : 'No projects yet'}
            description={
              activeFilters > 0
                ? 'Try a different search term, or clear the status filter.'
                : 'Create the first project to start staffing it, writing a SOW and contracting.'
            }
            action={
              activeFilters > 0 ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setStatus('')
                    setOwner('')
                    setSearch('')
                  }}
                >
                  Clear filters
                </Button>
              ) : (
                <CreateProjectDialog triggerLabel="Create your first project" />
              )
            }
          />
        }
      />

      <div className="flex items-center justify-between border-t border-border pt-3">
        <p className="text-xs text-muted-foreground">
          {rows.length} {rows.length === 1 ? 'project' : 'projects'} on this page
          {meta?.has_more ? ' · more available' : ''}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={history.length <= 1}
            onClick={() => {
              const next = history.slice(0, -1)
              setHistory(next)
              setCursor(next[next.length - 1] ?? null)
            }}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!meta?.has_more || !meta.next_cursor}
            onClick={() => {
              const nextCursor = meta?.next_cursor ?? null
              if (!nextCursor) return
              setCursor(nextCursor)
              setHistory((previous) => [...previous, nextCursor])
            }}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  )
}

function FolderIcon() {
  return <FolderKanban aria-hidden />
}

