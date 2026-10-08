'use client'

import * as React from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { z } from 'zod'
import { FileText, Plus } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate } from '@/lib/utils'
import { statusLabel } from '@/lib/status'
import {
  BILLING_BASES,
  BILLING_FREQUENCIES,
  CONTRACT_STATUSES,
  type Contract,
  type Page as PageEnvelope,
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
  FilterNumber,
  FilterSelect,
  useDebouncedValue,
} from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable } from '@/components/query'

/**
 * Every contract in the company.
 *
 * `expiring_within_days` is sent as a number, not a string: the API validates it
 * as an integer between 1 and 365, so an empty filter must be omitted entirely
 * rather than sent blank.
 */
export default function ContractsPage() {
  const { activeCompanyPublicId } = useCompany()
  const [search, setSearch] = React.useState('')
  const debouncedSearch = useDebouncedValue(search.trim())

  // Deep links (e.g. /contracts?status=ACTIVE from the CODE rail) initialize
  // the filters; unrecognized values are ignored, never sent to the API.
  const initialContractFilters = React.useMemo(() => {
    if (typeof window === 'undefined') return {}
    const params = new URLSearchParams(window.location.search)
    const filters: Record<string, string> = {}
    const status = params.get('status') ?? ''
    if ((CONTRACT_STATUSES as readonly string[]).includes(status)) {
      filters.status = status
    }
    const expiring = params.get('expiring_within_days') ?? ''
    if (/^\d{1,3}$/.test(expiring)) {
      filters.expiring_within_days = expiring
    }
    return filters
  }, [])

  const list = useCursorList<PageEnvelope<Contract>>({
    companyPublicId: activeCompanyPublicId,
    path: '/contracts',
    queryKey: ['contracts', 'list'],
    initialFilters: initialContractFilters,
  })

  React.useEffect(() => {
    list.setFilter('q', debouncedSearch || null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])

  const rows = list.query.data?.data ?? []

  const columns: Column<Contract>[] = [
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
      key: 'value',
      header: 'Value',
      numeric: true,
      cell: (row) =>
        row.contract_value
          ? formatCurrency(row.contract_value, row.currency, { compact: true })
          : '—',
    },
    {
      key: 'outstanding',
      header: 'Outstanding',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => formatCurrency(row.outstanding_total, row.currency, { compact: true }),
    },
    {
      key: 'end',
      header: 'Ends',
      hideBelow: 'md',
      cell: (row) => (row.end_date ? formatDate(row.end_date) : '—'),
    },
  ]

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Contracts' }]}
          title="Contracts"
          description="What has been agreed, with whom, and where each one stands. The action buttons on a contract come from the transitions the server will accept."
          actions={<CreateContractDialog />}
        />

        <FilterBar
          activeCount={list.activeFilterCount}
          onClear={() => {
            setSearch('')
            list.clearFilters()
          }}
        >
          <FilterInput
            id="contract-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Title or counterparty"
            className="min-w-56 flex-1"
          />
          <FilterSelect
            id="contract-status"
            label="Status"
            value={(list.filters.status as string) ?? ''}
            onChange={(value) => list.setFilter('status', value)}
            options={[...CONTRACT_STATUSES]}
            className="w-48"
          />
          <FilterNumber
            id="contract-expiring"
            label="Expiring within (days)"
            value={(list.filters.expiring_within_days as string) ?? ''}
            onChange={(value) => list.setFilter('expiring_within_days', value)}
            placeholder="e.g. 60"
            step="1"
            className="w-44"
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
              caption="Contracts in this company"
              exportName="contracts"
              csv={[
                { header: 'Contract ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Status', value: (row) => row.status },
                { header: 'Project ID', value: (row) => row.project_id },
                { header: 'Project', value: (row) => row.project_name },
                { header: 'SOW ID', value: (row) => row.sow_id },
                { header: 'Counterparty', value: (row) => row.counterparty_company_name },
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
                  title={
                    list.activeFilterCount > 0 ? 'No contracts match those filters' : 'No contracts yet'
                  }
                  description={
                    list.activeFilterCount > 0
                      ? 'Try a different search term, status or expiry window.'
                      : 'Contracts are generated from an approved statement of work, or created directly against one.'
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
                      <CreateContractDialog triggerLabel="Create the first contract" />
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
              noun="contracts"
            />
          </>
        )}
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Create contract                                                              */
/* -------------------------------------------------------------------------- */

const contractSchema = z
  .object({
    sow_id: z.string().min(1, 'Choose the SOW this contract belongs to.'),
    title: z.string().trim().min(2, 'Give the contract a title.').max(200),
    contract_type: z.enum(['COMPANY', 'INDIVIDUAL']),
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

type ContractFormValues = z.infer<typeof contractSchema>

const EMPTY_CONTRACT: ContractFormValues = {
  sow_id: '',
  title: '',
  contract_type: 'COMPANY',
  counterparty_company_id: '',
  currency: 'USD',
  billing_basis: 'TIMESHEET',
  billing_frequency: 'MONTHLY',
  start_date: '',
  end_date: '',
}

function CreateContractDialog({ triggerLabel = 'New contract' }: { triggerLabel?: string }) {
  const router = useRouter()
  const { activeCompanyPublicId, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [values, setValues] = React.useState<ContractFormValues>(EMPTY_CONTRACT)
  const [errors, setErrors] = React.useState<Partial<Record<string, string>>>({})
  const [sows, setSows] = React.useState<Sow[]>([])

  React.useEffect(() => {
    if (!open) return
    setValues(EMPTY_CONTRACT)
    setErrors({})
    void api
      .get<PageEnvelope<Sow>>('/sows?limit=100', { companyPublicId: activeCompanyPublicId })
      .then((page) => setSows(page.data ?? []))
      .catch(() => setSows([]))
  }, [open, activeCompanyPublicId])

  const create = useCompanyMutation<Contract, ContractFormValues>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (form) => {
      const sow = sows.find((s) => s.public_id === form.sow_id)
      if (!sow) throw new Error('Choose a SOW first.')
      return api.post<Contract>(
        '/contracts',
        {
          project_id: sow.project_id,
          sow_id: form.sow_id,
          title: form.title,
          contract_type: form.contract_type,
          counterparty_company_id: form.counterparty_company_id?.trim() || undefined,
          currency: form.currency,
          billing_basis: form.billing_basis,
          billing_frequency: form.billing_frequency,
          start_date: form.start_date || undefined,
          end_date: form.end_date || undefined,
        },
        { companyPublicId: activeCompanyPublicId },
      )
    },
    invalidate: [['contracts']],
    onSuccess: (contract) => {
      notifySuccess('Contract created.', `${contract.title} · ${contract.public_id}`)
      setOpen(false)
      router.push(`/contracts/${contract.public_id}`)
    },
  })

  const set = <K extends keyof ContractFormValues>(key: K, value: ContractFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const parsed = contractSchema.safeParse(values)
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
      notifyError(cause, 'The contract could not be created.')
    }
  }

  if (!can('contracts.create')) {
    return (
      <p className="text-sm text-muted-foreground">
        You do not have permission to create contracts in this company.
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
        title="New contract"
        description="A contract references a project and its SOW. The project is taken from the chosen SOW."
        className="max-w-2xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="create-contract" loading={create.isPending}>
              Create contract
            </Button>
          </>
        }
      >
        <form
          id="create-contract"
          onSubmit={submit}
          className="max-h-[65vh] space-y-4 overflow-y-auto pr-1"
        >
          <Field label="SOW" error={errors.sow_id} required>
            <Select
              id="contract-sow"
              value={values.sow_id}
              onChange={(event) => set('sow_id', event.target.value)}
              aria-invalid={Boolean(errors.sow_id)}
            >
              <option value="">Choose a SOW…</option>
              {sows.map((sow) => (
                <option key={sow.public_id} value={sow.public_id}>
                  {sow.title} · {sow.public_id}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Title" error={errors.title} required>
            <Input
              id="contract-title"
              value={values.title}
              onChange={(event) => set('title', event.target.value)}
              aria-invalid={Boolean(errors.title)}
              placeholder="Backend team contract"
            />
          </Field>

          <FieldGrid>
            <Field label="Type">
              <Select
                id="contract-type"
                value={values.contract_type}
                onChange={(event) =>
                  set('contract_type', event.target.value as ContractFormValues['contract_type'])
                }
              >
                <option value="COMPANY">Company</option>
                <option value="INDIVIDUAL">Individual</option>
              </Select>
            </Field>
            <Field label="Counterparty company ID" error={errors.counterparty_company_id}>
              <Input
                id="contract-counterparty"
                value={values.counterparty_company_id ?? ''}
                onChange={(event) => set('counterparty_company_id', event.target.value)}
                placeholder="CO… (optional)"
              />
            </Field>
          </FieldGrid>

          <FieldGrid>
            <Field label="Currency">
              <CurrencySelect
                id="contract-currency"
                value={values.currency}
                onChange={(value) => set('currency', value)}
              />
            </Field>
            <Field label="Billing basis">
              <Select
                id="contract-basis"
                value={values.billing_basis}
                onChange={(event) =>
                  set('billing_basis', event.target.value as ContractFormValues['billing_basis'])
                }
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
                id="contract-frequency"
                value={values.billing_frequency}
                onChange={(event) =>
                  set('billing_frequency', event.target.value as ContractFormValues['billing_frequency'])
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
                id="contract-start"
                value={values.start_date ?? ''}
                onChange={(value) => set('start_date', value)}
              />
            </Field>
            <Field label="End date" error={errors.end_date}>
              <DateInput
                id="contract-end"
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