'use client'

import * as React from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { z } from 'zod'
import { FileSignature, Plus } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate } from '@/lib/utils'
import { statusLabel } from '@/lib/status'
import {
  BILLING_BASES,
  BILLING_FREQUENCIES,
  SOW_STATUSES,
  type Page as PageEnvelope,
  type Project,
  type Sow,
} from '@/lib/domain-types'
import { Button, Dialog, EmptyState, Input, Select } from '@/components/ui'
import { CurrencySelect, DateInput, Field, FieldGrid } from '@/components/forms'
import { notifyError, notifySuccess } from '@/components/toast'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import {
  FilterBar,
  FilterInput,
  FilterSelect,
  useDebouncedValue,
} from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable } from '@/components/query'

/**
 * Every statement of work in the company.
 *
 * `q` is debounced before it reaches the API: a request per keystroke over a
 * growing result set is the difference between a fast list and a stalled one.
 */
export default function SowsPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())

  const list = useCursorList<PageEnvelope<Sow>>({
    companyPublicId: activeCompanyPublicId,
    path: '/sows',
    queryKey: ['sows', 'list'],
    initialFilters: { q: debouncedSearch || null, status: null },
  })

  React.useEffect(() => {
    list.setFilter('q', debouncedSearch || null)
    // Only the debounced search drives the filter; re-running on identity changes
    // would loop, because setFilter replaces the filter object every time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const rows = list.query.data?.data ?? []

  const columns: Column<Sow>[] = [
    {
      key: 'title',
      header: 'Statement of work',
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
      key: 'project',
      header: 'Project',
      hideBelow: 'lg',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.project_name ?? '—'}</p>
          <PublicId value={row.project_id} kind="project" />
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
        row.max_total_amount
          ? formatCurrency(row.max_total_amount, row.currency, { compact: true })
          : '—',
    },
    {
      key: 'period',
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
      hideBelow: 'lg',
      cell: (row) => row.contract_count,
    },
  ]

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Statements of Work' }]}
          title="Statements of work"
          description="What was promised, to whom, and at which roles and rates. Contracts are generated from an approved SOW."
          actions={<CreateSowDialog />}
        />

        <FilterBar
          activeCount={list.activeFilterCount}
          onClear={() => {
            setSearch('')
            list.clearFilters()
          }}
        >
          <FilterInput
            id="sow-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Title or description"
            className="min-w-56 flex-1"
          />
          <FilterSelect
            id="sow-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={[...SOW_STATUSES]}
            className="w-48"
          />
        </FilterBar>

        {list.query.isPending ? (
          <LoadingTable rows={8} columns={6} />
        ) : list.query.isError ? (
          <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Statements of work in this company"
              exportName="sows"
              csv={[
                { header: 'SOW ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Project ID', value: (row) => row.project_id },
                { header: 'Project', value: (row) => row.project_name },
                { header: 'Status', value: (row) => row.status },
                { header: 'Counterparty', value: (row) => row.counterparty_company_name },
                { header: 'Currency', value: (row) => row.currency },
                { header: 'Max total', value: (row) => row.max_total_amount },
                { header: 'Start', value: (row) => row.start_date },
                { header: 'End', value: (row) => row.end_date },
                { header: 'Contracts', value: (row) => row.contract_count },
              ]}
              emptyState={
                <EmptyState
                  icon={<FileSignature aria-hidden />}
                  title={
                    list.activeFilterCount > 0
                      ? 'No SOWs match those filters'
                      : 'No statements of work yet'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Try a different search term, or clear the status filter.'
                      : can('sows.create')
                        ? 'A SOW scopes the work and prices the roles. Create one from the project it belongs to.'
                        : 'A SOW scopes the work and prices the roles. One has not been written for this company yet.'
                  }
                  action={
                    list.activeFilterCount > 0 ? (
                      <Button
                        variant="outline"
onClick={() => {
                        setSearch('')
                        list.clearFilters()
                      }}
                      >
                        Clear filters
                      </Button>
                    ) : (
                      <CreateSowDialog triggerLabel="Create the first SOW" />
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
              noun="statements of work"
            />
          </>
        )}
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Create SOW                                                                 */
/* -------------------------------------------------------------------------- */

const sowSchema = z
  .object({
    project_id: z.string().min(1, 'Choose the project this SOW belongs to.'),
    title: z.string().trim().min(2, 'Give the SOW a title.').max(200),
    description: z.string().trim().max(20000).optional(),
    sow_type: z.enum(['COMPANY', 'INDIVIDUAL']),
    counterparty_company_id: z.string().trim().optional(),
    currency: z.string().length(3),
    billing_basis: z.enum(['TIMESHEET', 'FIXED', 'RECURRING', 'USAGE', 'MILESTONE']),
    billing_frequency: z.enum(['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM']),
    start_date: z.string().optional(),
    end_date: z.string().optional(),
  })
  .refine((values) => !values.start_date || !values.end_date || values.end_date >= values.start_date, {
    message: 'The end date cannot be before the start date.',
    path: ['end_date'],
  })

type SowFormValues = z.infer<typeof sowSchema>

const EMPTY_SOW: SowFormValues = {
  project_id: '',
  title: '',
  description: '',
  sow_type: 'COMPANY',
  counterparty_company_id: '',
  currency: 'USD',
  billing_basis: 'TIMESHEET',
  billing_frequency: 'MONTHLY',
  start_date: '',
  end_date: '',
}

function CreateSowDialog({ triggerLabel = 'New SOW' }: { triggerLabel?: string }) {
  const router = useRouter()
  const { activeCompanyPublicId, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [values, setValues] = React.useState<SowFormValues>(EMPTY_SOW)
  const [errors, setErrors] = React.useState<Partial<Record<string, string>>>({})
  const [projects, setProjects] = React.useState<Project[]>([])

  React.useEffect(() => {
    if (!open) return
    setValues(EMPTY_SOW)
    setErrors({})
    void api
      .get<PageEnvelope<Project>>('/projects?limit=100', {
        companyPublicId: activeCompanyPublicId,
      })
      .then((page) => setProjects(page.data ?? []))
      .catch(() => setProjects([]))
  }, [open, activeCompanyPublicId])

  const create = useCompanyMutation<Sow, SowFormValues>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (form) =>
      api.post<Sow>(
        `/sows?project_id=${encodeURIComponent(form.project_id)}`,
        {
          title: form.title,
          description: form.description?.trim() || undefined,
          sow_type: form.sow_type,
          counterparty_company_id: form.counterparty_company_id?.trim() || undefined,
          currency: form.currency,
          billing_basis: form.billing_basis,
          billing_frequency: form.billing_frequency,
          start_date: form.start_date || undefined,
          end_date: form.end_date || undefined,
        },
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['sows']],
    onSuccess: (sow) => {
      notifySuccess('SOW created.', `${sow.title} · ${sow.public_id}`)
      setOpen(false)
      router.push(`/sows/${sow.public_id}`)
    },
  })

  const set = <K extends keyof SowFormValues>(key: K, value: SowFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const parsed = sowSchema.safeParse(values)
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
      notifyError(cause, 'The SOW could not be created.')
    }
  }

  if (!can('sows.create')) {
    return (
      <p className="text-sm text-muted-foreground">
        You do not have permission to create statements of work in this company.
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
        title="New statement of work"
        description="A SOW allocates project roles to a counterparty. Contracts are generated from an approved SOW."
        className="max-w-2xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="create-sow" loading={create.isPending}>
              Create SOW
            </Button>
          </>
        }
      >
        <form
          id="create-sow"
          onSubmit={submit}
          className="max-h-[65vh] space-y-4 overflow-y-auto pr-1"
        >
          <Field label="Project" error={errors.project_id} required>
            <Select
              id="sow-project"
              value={values.project_id}
              onChange={(event) => set('project_id', event.target.value)}
              aria-invalid={Boolean(errors.project_id)}
            >
              <option value="">Choose a project…</option>
              {projects.map((project) => (
                <option key={project.public_id} value={project.public_id}>
                  {project.name} · {project.public_id}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Title" error={errors.title} required>
            <Input
              id="sow-title"
              value={values.title}
              onChange={(event) => set('title', event.target.value)}
              aria-invalid={Boolean(errors.title)}
              placeholder="Backend team augmentation"
            />
          </Field>

          <FieldGrid>
            <Field label="Type">
              <Select
                id="sow-type"
                value={values.sow_type}
                onChange={(event) => set('sow_type', event.target.value as SowFormValues['sow_type'])}
              >
                <option value="COMPANY">Company</option>
                <option value="INDIVIDUAL">Individual</option>
              </Select>
            </Field>
            <Field label="Counterparty company ID" error={errors.counterparty_company_id}>
              <Input
                id="sow-counterparty"
                value={values.counterparty_company_id ?? ''}
                onChange={(event) => set('counterparty_company_id', event.target.value)}
                placeholder="CO… (optional)"
              />
            </Field>
          </FieldGrid>

          <FieldGrid>
            <Field label="Currency">
              <CurrencySelect
                id="sow-currency"
                value={values.currency}
                onChange={(value) => set('currency', value)}
              />
            </Field>
            <Field label="Billing basis">
              <Select
                id="sow-basis"
                value={values.billing_basis}
                onChange={(event) => set('billing_basis', event.target.value as SowFormValues['billing_basis'])}
              >
                {BILLING_BASES.map((basis) => (
                  <option key={basis} value={basis}>
                    {statusLabel(basis)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Billing frequency">
              <Select
                id="sow-frequency"
                value={values.billing_frequency}
                onChange={(event) =>
                  set('billing_frequency', event.target.value as SowFormValues['billing_frequency'])
                }
              >
                {BILLING_FREQUENCIES.map((frequency) => (
                  <option key={frequency} value={frequency}>
                    {statusLabel(frequency)}
                  </option>
                ))}
              </Select>
            </Field>
          </FieldGrid>

          <FieldGrid>
            <Field label="Start date" error={errors.start_date}>
              <DateInput
                id="sow-start"
                value={values.start_date ?? ''}
                onChange={(value) => set('start_date', value)}
              />
            </Field>
            <Field label="End date" error={errors.end_date}>
              <DateInput
                id="sow-end"
                value={values.end_date ?? ''}
                onChange={(value) => set('end_date', value)}
              />
            </Field>
          </FieldGrid>
        </form>
      </Dialog>
    </>
  )
}