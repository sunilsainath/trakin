'use client'

import * as React from 'react'
import { useSearchParams } from 'next/navigation'
import { z } from 'zod'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import {
  BILLING_BASES,
  BILLING_FREQUENCIES,
  RATE_TYPES,
  type Project,
  type ProjectRole,
  type Sow,
} from '@/lib/domain-types'
import { Button, Dialog } from '@/components/ui'
import { CurrencySelect, DateInput, Field } from '@/components/forms'
import { LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

interface RoleLine {
  key: string
  project_role_id: string
  quantity: string
  rate: string
  rate_type: string
  currency: string
  billing_basis: string
  billing_frequency: string
  notes: string
}

const EMPTY_LINE: RoleLine = {
  key: '',
  project_role_id: '',
  quantity: '1',
  rate: '',
  rate_type: 'HOURLY',
  currency: 'USD',
  billing_basis: '',
  billing_frequency: '',
  notes: '',
}

const draftSchema = z.object({
  title: z.string().trim().min(2, 'Give the SOW a title.').max(200),
  project_id: z.string().min(1, 'Pick a project.'),
  counterparty: z.string().min(1, 'Pick a counterparty.'),
})

/**
 * Entry point that also honours the `?project_id=&new=1` links from project
 * pages: arriving with those parameters opens the dialog prefilled, so the
 * links are creation shortcuts rather than dead ends.
 */
export function CreateSowOpener() {
  const searchParams = useSearchParams()
  const [open, setOpen] = React.useState(false)
  const [initialProject, setInitialProject] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (searchParams.get('new') === '1') {
      setInitialProject(searchParams.get('project_id'))
      setOpen(true)
    }
  }, [searchParams])

  return (
    <>
      <Button onClick={() => { setInitialProject(null); setOpen(true) }}>New SOW</Button>
      {open ? (
        <CreateSowDialog
          initialProjectId={initialProject}
          onClose={() => setOpen(false)}
          onCreated={(sow) => {
            setOpen(false)
            window.location.href = `/sows/${sow.public_id}`
          }}
        />
      ) : null}
    </>
  )
}

function CreateSowDialog({
  initialProjectId,
  onClose,
  onCreated,
}: {
  initialProjectId: string | null
  onClose: () => void
  onCreated: (sow: Sow) => void
}) {
  const { activeCompanyPublicId, companies } = useCompany()
  const [projectId, setProjectId] = React.useState(initialProjectId ?? '')
  const [title, setTitle] = React.useState('')
  const [description, setDescription] = React.useState('')
  const [partyKind, setPartyKind] = React.useState<'COMPANY' | 'INDIVIDUAL'>('COMPANY')
  const [companyId, setCompanyId] = React.useState('')
  const [userId, setUserId] = React.useState('')
  const [startDate, setStartDate] = React.useState('')
  const [endDate, setEndDate] = React.useState('')
  const [currency, setCurrency] = React.useState('USD')
  const [paymentTerms, setPaymentTerms] = React.useState('30')
  const [billingBasis, setBillingBasis] = React.useState('TIMESHEET')
  const [billingFrequency, setBillingFrequency] = React.useState('MONTHLY')
  const [invoiceFrequency, setInvoiceFrequency] = React.useState('MONTHLY')
  const [paymentMethod, setPaymentMethod] = React.useState('')
  const [specialConditions, setSpecialConditions] = React.useState('')
  const [maxTotal, setMaxTotal] = React.useState('')
  const [lines, setLines] = React.useState<RoleLine[]>([])
  const [errors, setErrors] = React.useState<Record<string, string>>({})
  const [saving, setSaving] = React.useState(false)
  const [serverError, setServerError] = React.useState<unknown>(null)

  const projects = useCompanyQuery<{ data: Project[] }>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['sow-create', 'projects'],
    path: '/projects?limit=100',
  })
  const projectOptions = projects.data?.data ?? []

  const roles = useCompanyQuery<{ data: ProjectRole[] }>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['sow-create', 'roles', projectId],
    path: `/project-roles?project_id=${encodeURIComponent(projectId)}&limit=100`,
    enabled: projectId !== '',
  })
  const roleOptions = React.useMemo(() => roles.data?.data ?? [], [roles.data])
  const roleById = React.useMemo(
    () => new Map(roleOptions.map((role) => [role.public_id, role])),
    [roleOptions],
  )

  const addLine = () => {
    setLines((previous) => [
      ...previous,
      { ...EMPTY_LINE, key: `${Date.now()}-${previous.length}`, currency },
    ])
  }

  const setLine = (key: string, patch: Partial<RoleLine>) => {
    setLines((previous) => previous.map((line) => (line.key === key ? { ...line, ...patch } : line)))
  }

  const removeLine = (key: string) => {
    setLines((previous) => previous.filter((line) => line.key !== key))
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const parsed = draftSchema.safeParse({
      title,
      project_id: projectId,
      counterparty: partyKind === 'COMPANY' ? companyId : userId,
    })
    const next: Record<string, string> = {}
    if (!parsed.success) {
      for (const issue of parsed.error.issues) {
        const key = String(issue.path[0] ?? '')
        if (key && !next[key]) next[key] = issue.message
      }
    }
    if (startDate && endDate && endDate < startDate) {
      next['end_date'] = 'The end date must not precede the start date.'
    }
    const rolePayload = []
    for (const [index, line] of lines.entries()) {
      if (!line.project_role_id) {
        next[`roles[${index}]`] = 'Pick a role for every line.'
        continue
      }
      const role = roleById.get(line.project_role_id)
      const available = role ? role.required_count - role.allocated_count : null
      const quantity = Number(line.quantity)
      if (!Number.isInteger(quantity) || quantity < 1) {
        next[`roles[${index}]`] = 'Quantity must be a whole number of at least 1.'
        continue
      }
      if (available !== null && quantity > available) {
        next[`roles[${index}]`] =
          `Only ${available} of “${role?.title}” remain available — requested ${quantity}.`
        continue
      }
      rolePayload.push({
        project_role_id: line.project_role_id,
        quantity,
        rate: line.rate === '' ? null : line.rate,
        rate_type: line.rate_type,
        currency: line.currency || currency,
        billing_basis: line.billing_basis === '' ? null : line.billing_basis,
        billing_frequency: line.billing_frequency === '' ? null : line.billing_frequency,
        notes: line.notes === '' ? null : line.notes,
      })
    }
    setErrors(next)
    if (Object.keys(next).length > 0 || !parsed.success) return

    setSaving(true)
    setServerError(null)
    try {
      const created = await api.post<Sow>(
        `/sows?project_id=${encodeURIComponent(projectId)}`,
        {
          title: title.trim(),
          description: description.trim() || null,
          sow_type: partyKind,
          counterparty_company_id: partyKind === 'COMPANY' ? companyId : null,
          counterparty_user_id: partyKind === 'INDIVIDUAL' ? userId.trim() : null,
          start_date: startDate || null,
          end_date: endDate || null,
          currency,
          billing_basis: billingBasis,
          billing_frequency: billingFrequency,
          invoice_frequency: invoiceFrequency,
          payment_terms_days: Number(paymentTerms) || 30,
          payment_method: paymentMethod.trim() || null,
          special_conditions: specialConditions.trim() || null,
          max_total_amount: maxTotal === '' ? null : maxTotal,
          roles: rolePayload,
        },
        { companyPublicId: activeCompanyPublicId },
      )
      notifySuccess('Statement of work created.')
      onCreated(created)
    } catch (cause) {
      setServerError(cause)
      notifyError(cause, 'The SOW could not be created.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open onOpenChange={(next) => { if (!next) onClose() }} title="Create a statement of work">
      <form onSubmit={(event) => void submit(event)} className="space-y-4">
        <Field label="Project" error={errors['project_id']} required>
          {projects.isPending ? (
            <LoadingBlock rows={1} />
          ) : (
            <select
              aria-label="Project"
              value={projectId}
              onChange={(event) => {
                setProjectId(event.target.value)
                setLines([])
              }}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="">Select a project…</option>
              {projectOptions.map((project) => (
                <option key={project.public_id} value={project.public_id}>
                  {project.name} ({project.public_id})
                </option>
              ))}
            </select>
          )}
        </Field>

        <Field label="Title" error={errors['title']} required>
          <input
            aria-label="Title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={200}
            placeholder="e.g. Backend squad, Q1"
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
          />
        </Field>

        <Field label="Description">
          <textarea
            aria-label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            className="flex min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </Field>

        <div className="flex gap-2" role="group" aria-label="Counterparty type">
          {(['COMPANY', 'INDIVIDUAL'] as const).map((kind) => (
            <Button
              key={kind}
              type="button"
              variant={partyKind === kind ? 'primary' : 'outline'}
              size="sm"
              onClick={() => setPartyKind(kind)}
            >
              {kind === 'COMPANY' ? 'Company → Company' : 'Company → Individual'}
            </Button>
          ))}
        </div>

        {partyKind === 'COMPANY' ? (
          <Field label="Counterparty company" error={errors['counterparty']} required>
            <select
              aria-label="Counterparty company"
              value={companyId}
              onChange={(event) => setCompanyId(event.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="">Select a company…</option>
              {companies.map((company) => (
                <option key={company.public_id} value={company.public_id}>
                  {company.display_name} ({company.public_id})
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field
            label="Counterparty user ID"
            error={errors['counterparty']}
            required
            hint="The individual's public user ID (U…)."
          >
            <input
              aria-label="Counterparty user ID"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              placeholder="U…"
              className="h-10 w-full rounded-md border border-input bg-background px-3 font-mono text-sm"
            />
          </Field>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Start date">
            <DateInput id="sow-start" value={startDate} onChange={setStartDate} />
          </Field>
          <Field label="End date" error={errors['end_date']}>
            <DateInput id="sow-end" value={endDate} onChange={setEndDate} />
          </Field>
          <Field label="Currency">
            <CurrencySelect id="sow-currency" value={currency} onChange={setCurrency} />
          </Field>
          <Field label="Payment terms (days)">
            <input
              aria-label="Payment terms days"
              type="number"
              min={0}
              max={365}
              value={paymentTerms}
              onChange={(event) => setPaymentTerms(event.target.value)}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            />
          </Field>
          <Field label="Billing basis">
            <select
              aria-label="Billing basis"
              value={billingBasis}
              onChange={(event) => setBillingBasis(event.target.value)}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              {BILLING_BASES.map((basis) => (
                <option key={basis} value={basis}>{basis}</option>
              ))}
            </select>
          </Field>
          <Field label="Billing frequency">
            <select
              aria-label="Billing frequency"
              value={billingFrequency}
              onChange={(event) => setBillingFrequency(event.target.value)}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              {BILLING_FREQUENCIES.map((frequency) => (
                <option key={frequency} value={frequency}>{frequency}</option>
              ))}
            </select>
          </Field>
          <Field label="Invoice frequency">
            <select
              aria-label="Invoice frequency"
              value={invoiceFrequency}
              onChange={(event) => setInvoiceFrequency(event.target.value)}
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              {BILLING_FREQUENCIES.map((frequency) => (
                <option key={frequency} value={frequency}>{frequency}</option>
              ))}
            </select>
          </Field>
          <Field label="Max total amount">
            <input
              aria-label="Max total amount"
              type="number"
              min={0}
              step="0.01"
              value={maxTotal}
              onChange={(event) => setMaxTotal(event.target.value)}
              placeholder="No cap"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            />
          </Field>
        </div>

        <Field label="Payment method">
          <input
            aria-label="Payment method"
            value={paymentMethod}
            onChange={(event) => setPaymentMethod(event.target.value)}
            maxLength={64}
            placeholder="e.g. Bank transfer"
            className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
          />
        </Field>

        <Field label="Special conditions">
          <textarea
            aria-label="Special conditions"
            value={specialConditions}
            onChange={(event) => setSpecialConditions(event.target.value)}
            rows={3}
            className="flex min-h-20 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </Field>

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">Roles &amp; allocation</p>
            <Button type="button" size="sm" variant="outline" onClick={addLine} disabled={projectId === ''}>
              Add role
            </Button>
          </div>
          {lines.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Roles can be added now or later — but activation requires at least one.
              Quantities can never exceed what each role still has available.
            </p>
          ) : null}
          {lines.map((line, index) => {
            const role = roleById.get(line.project_role_id)
            const available = role ? role.required_count - role.allocated_count : null
            const lineError = errors[`roles[${index}]`]
            return (
              <div key={line.key} className="space-y-2 rounded-md border border-border p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Role</span>
                    <select
                      aria-label="Role"
                      value={line.project_role_id}
                      onChange={(event) => setLine(line.key, { project_role_id: event.target.value })}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    >
                      <option value="">Select a role…</option>
                      {roleOptions.map((option) => (
                        <option key={option.public_id} value={option.public_id}>
                          {option.title} (avail {option.required_count - option.allocated_count}/{option.required_count})
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Quantity</span>
                    <input
                      aria-label="Quantity"
                      type="number"
                      min={1}
                      value={line.quantity}
                      onChange={(event) => setLine(line.key, { quantity: event.target.value })}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    />
                  </label>
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Rate</span>
                    <input
                      aria-label="Rate"
                      type="number"
                      min={0}
                      step="0.01"
                      value={line.rate}
                      onChange={(event) => setLine(line.key, { rate: event.target.value })}
                      placeholder="Role default"
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    />
                  </label>
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Currency</span>
                    <input
                      aria-label="Role currency"
                      value={line.currency}
                      onChange={(event) => setLine(line.key, { currency: event.target.value.toUpperCase() })}
                      maxLength={3}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 font-mono text-sm"
                    />
                  </label>
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Rate type</span>
                    <select
                      aria-label="Rate type"
                      value={line.rate_type}
                      onChange={(event) => setLine(line.key, { rate_type: event.target.value })}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    >
                      {RATE_TYPES.map((rateType) => (
                        <option key={rateType} value={rateType}>{rateType}</option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Billing basis (role)</span>
                    <select
                      aria-label="Role billing basis"
                      value={line.billing_basis}
                      onChange={(event) => setLine(line.key, { billing_basis: event.target.value })}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    >
                      <option value="">SOW default</option>
                      {BILLING_BASES.map((basis) => (
                        <option key={basis} value={basis}>{basis}</option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-xs">
                    <span className="mb-1 block font-medium">Billing frequency (role)</span>
                    <select
                      aria-label="Role billing frequency"
                      value={line.billing_frequency}
                      onChange={(event) => setLine(line.key, { billing_frequency: event.target.value })}
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                    >
                      <option value="">SOW default</option>
                      {BILLING_FREQUENCIES.map((frequency) => (
                        <option key={frequency} value={frequency}>{frequency}</option>
                      ))}
                    </select>
                  </label>
                </div>
                {available !== null ? (
                  <p className="text-xs text-muted-foreground">
                    Required {role?.required_count}, allocated {role?.allocated_count}, available {available}.
                  </p>
                ) : null}
                {lineError ? (
                  <p role="alert" className="text-xs font-medium text-danger">{lineError}</p>
                ) : null}
                <div className="flex justify-end">
                  <Button type="button" size="sm" variant="ghost" onClick={() => removeLine(line.key)}>
                    Remove
                  </Button>
                </div>
              </div>
            )
          })}
        </div>

        {serverError ? (
          <p role="alert" className="text-xs font-medium text-danger">
            The SOW could not be created.
          </p>
        ) : null}

        <div className="flex items-center justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" loading={saving}>
            Create SOW
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
