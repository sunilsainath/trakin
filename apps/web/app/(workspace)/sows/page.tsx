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
  type ProjectRole,
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

  // Deep links (e.g. /sows?status=ACTIVE from the CODE rail) initialize the
  // filter; unrecognized values are ignored, never sent to the API.
  const initialStatus =
    typeof window === 'undefined'
      ? null
      : (() => {
          const value = new URLSearchParams(window.location.search).get('status') ?? ''
          return (SOW_STATUSES as readonly string[]).includes(value) ? value : null
        })()

  const list = useCursorList<PageEnvelope<Sow>>({
    companyPublicId: activeCompanyPublicId,
    path: '/sows',
    queryKey: ['sows', 'list'],
    initialFilters: { q: debouncedSearch || null, status: initialStatus },
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
    counterparty_user_id: z.string().trim().optional(),
    currency: z.string().length(3),
    billing_basis: z.enum(['TIMESHEET', 'FIXED', 'RECURRING', 'USAGE', 'MILESTONE']),
    billing_frequency: z.enum(['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM']),
    invoice_frequency: z.enum(['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM']),
    payment_terms_days: z.coerce.number().int().min(0).max(365),
    payment_method: z.string().trim().max(64).optional(),
    default_rate: z.string().trim().optional(),
    max_total_amount: z.string().trim().optional(),
    scope: z.string().trim().max(40000).optional(),
    special_conditions: z.string().trim().max(20000).optional(),
    document_id: z.string().trim().optional(),
    auto_generate_contracts: z.boolean(),
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
  counterparty_user_id: '',
  currency: 'USD',
  billing_basis: 'TIMESHEET',
  billing_frequency: 'MONTHLY',
  invoice_frequency: 'MONTHLY',
  payment_terms_days: 30,
  payment_method: '',
  default_rate: '',
  max_total_amount: '',
  scope: '',
  special_conditions: '',
  document_id: '',
  auto_generate_contracts: true,
  start_date: '',
  end_date: '',
}

interface SowRoleRow {
  project_role_id: string
  quantity: number
  rate: string
}

const PAYMENT_METHODS = ['BANK_TRANSFER', 'CARD', 'CHEQUE', 'CASH', 'OTHER']

function CreateSowDialog({ triggerLabel = 'New SOW' }: { triggerLabel?: string }) {
  const router = useRouter()
  const { activeCompanyPublicId, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [values, setValues] = React.useState<SowFormValues>(EMPTY_SOW)
  const [errors, setErrors] = React.useState<Partial<Record<string, string>>>({})
  const [projects, setProjects] = React.useState<Project[]>([])
  const [availableRoles, setAvailableRoles] = React.useState<ProjectRole[]>([])
  const [roleRows, setRoleRows] = React.useState<SowRoleRow[]>([])

  React.useEffect(() => {
    if (!open) return
    setValues(EMPTY_SOW)
    setErrors({})
    setRoleRows([])
    setAvailableRoles([])
    void api
      .get<PageEnvelope<Project>>('/projects?limit=100', {
        companyPublicId: activeCompanyPublicId,
      })
      .then((page) => setProjects(page.data ?? []))
      .catch(() => setProjects([]))
  }, [open, activeCompanyPublicId])

  const loadRoles = React.useCallback(
    (projectId: string) => {
      if (!projectId) {
        setAvailableRoles([])
        return
      }
      void api
        .get<PageEnvelope<ProjectRole>>(
          `/project-roles?project_id=${encodeURIComponent(projectId)}&limit=100`,
          { companyPublicId: activeCompanyPublicId },
        )
        .then((page) => setAvailableRoles(page.data ?? []))
        .catch(() => setAvailableRoles([]))
    },
    [activeCompanyPublicId],
  )

  React.useEffect(() => {
    if (open) loadRoles(values.project_id)
  }, [open, values.project_id, loadRoles])

  const create = useCompanyMutation<Sow, SowFormValues & { roles: SowRoleRow[] }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (form) => {
      const amount = (raw: string | undefined) => {
        const trimmed = (raw ?? '').trim()
        if (!trimmed) return undefined
        const parsed = Number(trimmed)
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined
      }
      return api.post<Sow>(
        `/sows?project_id=${encodeURIComponent(form.project_id)}`,
        {
          title: form.title,
          description: form.description?.trim() || undefined,
          sow_type: form.sow_type,
          counterparty_company_id: form.counterparty_company_id?.trim() || undefined,
          counterparty_user_id: form.counterparty_user_id?.trim() || undefined,
          currency: form.currency,
          billing_basis: form.billing_basis,
          billing_frequency: form.billing_frequency,
          invoice_frequency: form.invoice_frequency,
          payment_terms_days: form.payment_terms_days,
          payment_method: form.payment_method?.trim() || undefined,
          default_rate: amount(form.default_rate),
          max_total_amount: amount(form.max_total_amount),
          scope: form.scope?.trim() || undefined,
          special_conditions: form.special_conditions?.trim() || undefined,
          document_id: form.document_id?.trim() || undefined,
          auto_generate_contracts: form.auto_generate_contracts,
          roles: form.roles.map((row) => ({
            project_role_id: row.project_role_id,
            quantity: row.quantity,
            ...(amount(row.rate) !== undefined ? { rate: amount(row.rate) } : {}),
          })),
          start_date: form.start_date || undefined,
          end_date: form.end_date || undefined,
        },
        { companyPublicId: activeCompanyPublicId },
      )
    },
    invalidate: [['sows']],
    onSuccess: (sow) => {
      notifySuccess('SOW created.', `${sow.title} · ${sow.public_id}`)
      setOpen(false)
      router.push(`/sows/${sow.public_id}`)
    },
  })

  const set = <K extends keyof SowFormValues>(key: K, value: SowFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const addRole = (projectRoleId: string) => {
    if (!projectRoleId || roleRows.some((row) => row.project_role_id === projectRoleId)) return
    setRoleRows((previous) => [...previous, { project_role_id: projectRoleId, quantity: 1, rate: '' }])
  }

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
    if (parsed.data.sow_type === 'COMPANY' && !parsed.data.counterparty_company_id?.trim() && !parsed.data.counterparty_user_id?.trim()) {
      setErrors({ counterparty_company_id: 'Name the counterparty company or user.' })
      return
    }
    if (parsed.data.sow_type === 'INDIVIDUAL' && !parsed.data.counterparty_user_id?.trim()) {
      setErrors({ counterparty_user_id: 'Name the counterparty user (U…).' })
      return
    }
    setErrors({})
    try {
      await create.mutateAsync({ ...parsed.data, roles: roleRows })
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
              onChange={(event) => {
                set('project_id', event.target.value)
                setRoleRows([])
              }}
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
                onChange={(event) => {
                  const next = event.target.value as SowFormValues['sow_type']
                  setValues((previous) => ({
                    ...previous,
                    sow_type: next,
                    counterparty_company_id: next === 'COMPANY' ? previous.counterparty_company_id : '',
                    counterparty_user_id: next === 'INDIVIDUAL' ? previous.counterparty_user_id : '',
                  }))
                }}
              >
                <option value="COMPANY">Company</option>
                <option value="INDIVIDUAL">Individual</option>
              </Select>
            </Field>
            {values.sow_type === 'INDIVIDUAL' ? (
              <Field label="Counterparty user ID" error={errors.counterparty_user_id} required hint="The U… identifier of the person">
                <Input
                  id="sow-counterparty-user"
                  value={values.counterparty_user_id ?? ''}
                  onChange={(event) => set('counterparty_user_id', event.target.value.toUpperCase())}
                  placeholder="U…"
                  className="font-mono"
                />
              </Field>
            ) : (
              <Field label="Counterparty company ID" error={errors.counterparty_company_id} hint="The CO… identifier, when the other side is a company">
                <Input
                  id="sow-counterparty"
                  value={values.counterparty_company_id ?? ''}
                  onChange={(event) => set('counterparty_company_id', event.target.value.toUpperCase())}
                  placeholder="CO… (optional)"
                  className="font-mono"
                />
              </Field>
            )}
          </FieldGrid>

          <Field label="Roles and quantities" hint="Each role becomes one contract when the SOW is accepted.">
            {availableRoles.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {values.project_id
                  ? 'This project has no roles yet — add them on the project first.'
                  : 'Choose a project to list its roles.'}
              </p>
            ) : (
              <div className="space-y-2">
                {roleRows.map((row, index) => {
                  const role = availableRoles.find((candidate) => candidate.public_id === row.project_role_id)
                  return (
                    <div key={row.project_role_id} className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate text-sm">
                        {role?.title ?? row.project_role_id}
                      </span>
                      <label className="flex items-center gap-1 text-xs text-muted-foreground">
                        Qty
                        <Input
                          type="number"
                          min={1}
                          value={row.quantity}
                          onChange={(event) =>
                            setRoleRows((previous) =>
                              previous.map((candidate, position) =>
                                position === index
                                  ? { ...candidate, quantity: Math.max(1, Number(event.target.value) || 1) }
                                  : candidate,
                              ),
                            )
                          }
                          className="w-16"
                          aria-label={`Quantity for ${role?.title ?? row.project_role_id}`}
                        />
                      </label>
                      <label className="flex items-center gap-1 text-xs text-muted-foreground">
                        Rate
                        <Input
                          value={row.rate}
                          onChange={(event) =>
                            setRoleRows((previous) =>
                              previous.map((candidate, position) =>
                                position === index
                                  ? { ...candidate, rate: event.target.value }
                                  : candidate,
                              ),
                            )
                          }
                          className="w-24"
                          placeholder="Optional"
                          aria-label={`Rate for ${role?.title ?? row.project_role_id}`}
                        />
                      </label>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() =>
                          setRoleRows((previous) => previous.filter((_, position) => position !== index))
                        }
                      >
                        Remove
                      </Button>
                    </div>
                  )
                })}
                <div className="flex gap-2">
                  <Select
                    id="sow-add-role"
                    value=""
                    onChange={(event) => {
                      addRole(event.target.value)
                      event.target.value = ''
                    }}
                    aria-label="Add a project role"
                  >
                    <option value="">Add a role…</option>
                    {availableRoles
                      .filter((role) => !roleRows.some((row) => row.project_role_id === role.public_id))
                      .map((role) => (
                        <option key={role.public_id} value={role.public_id}>
                          {role.title} · {role.public_id}
                        </option>
                      ))}
                  </Select>
                </div>
              </div>
            )}
          </Field>

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
            <Field label="Invoice frequency">
              <Select
                id="sow-invoice-frequency"
                value={values.invoice_frequency}
                onChange={(event) =>
                  set('invoice_frequency', event.target.value as SowFormValues['invoice_frequency'])
                }
              >
                {BILLING_FREQUENCIES.map((frequency) => (
                  <option key={frequency} value={frequency}>
                    {statusLabel(frequency)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Payment terms (days)">
              <Input
                id="sow-terms"
                type="number"
                min={0}
                max={365}
                value={values.payment_terms_days}
                onChange={(event) => set('payment_terms_days', Number(event.target.value) || 0)}
              />
            </Field>
            <Field label="Payment method" hint="Optional">
              <Select
                id="sow-payment-method"
                value={values.payment_method ?? ''}
                onChange={(event) => set('payment_method', event.target.value)}
              >
                <option value="">Unspecified</option>
                {PAYMENT_METHODS.map((method) => (
                  <option key={method} value={method}>
                    {statusLabel(method)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Default rate" hint="Optional hourly fallback">
              <Input
                id="sow-rate"
                value={values.default_rate ?? ''}
                onChange={(event) => set('default_rate', event.target.value)}
                placeholder="150.00"
              />
            </Field>
            <Field label="Maximum total" hint="Optional cap">
              <Input
                id="sow-max"
                value={values.max_total_amount ?? ''}
                onChange={(event) => set('max_total_amount', event.target.value)}
                placeholder="50000.00"
              />
            </Field>
            <Field label="Linked document ID" hint="Optional D… reference">
              <Input
                id="sow-document"
                value={values.document_id ?? ''}
                onChange={(event) => set('document_id', event.target.value.toUpperCase())}
                placeholder="D…"
                className="font-mono"
              />
            </Field>
          </FieldGrid>

          <Field label="Scope" hint="Optional — what is included">
            <Input
              id="sow-scope"
              value={values.scope ?? ''}
              onChange={(event) => set('scope', event.target.value)}
            />
          </Field>
          <Field label="Special conditions" hint="Optional">
            <Input
              id="sow-conditions"
              value={values.special_conditions ?? ''}
              onChange={(event) => set('special_conditions', event.target.value)}
            />
          </Field>

          <label className="flex cursor-pointer items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-1"
              checked={values.auto_generate_contracts}
              onChange={(event) => set('auto_generate_contracts', event.target.checked)}
            />
            <span>
              <span className="font-medium">Generate one draft contract per role on acceptance</span>
              <span className="block text-xs text-muted-foreground">
                Drafts still go through send and accept before they become active.
              </span>
            </span>
          </label>

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