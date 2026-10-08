'use client'

import * as React from 'react'
import { z } from 'zod'
import { Building2, Check } from 'lucide-react'

import { api, createIdempotencyKey } from '@/lib/api'
import { uploadFoundingW9 } from '@/hooks/use-mutations'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatDate } from '@/lib/utils'
import type { Company } from '@/lib/types'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
  Dialog,
  EmptyState,
  Input,
  Select,
  Skeleton,
} from '@/components/ui'
import { CurrencySelect, Field } from '@/components/forms'
import { ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'
import { PublicId } from '@/components/public-id'
import { ErrorState } from '@/components/query'
import { PageHeader, PageShell } from '@/components/page'
import {
  TAX_CLASSIFICATIONS,
  TIN_TYPES,
  validateW9,
  type W9FieldKey,
  type W9FormValues,
} from '@/lib/w9'
import { W9Review, type W9IntakeState } from '@/components/w9-review'

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export function CompaniesScreen() {
  const { companies, activeCompanyPublicId, loading, error, refresh } = useCompany()

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Companies' }]}
          title="Companies"
          description="Every company you belong to. Your role and resolved permissions differ in each, which is what decides what you can see and do."
          actions={<CreateCompanyDialog />}
        />

        {loading ? (
          <CompanySkeletons />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refresh()} />
        ) : companies.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Building2 aria-hidden />}
              title="You are not a member of any company"
              description="Create a company to start working, or ask a colleague to invite you."
              action={<CreateCompanyDialog triggerLabel="Create a company" />}
            />
          </Card>
        ) : (
          <>
            <div className="grid gap-4 md:grid-cols-2">
              {companies.map((company) => (
                <CompanyCard
                  key={company.public_id}
                  company={company}
                  active={company.public_id === activeCompanyPublicId}
                />
              ))}
            </div>

            <p className="text-xs text-muted-foreground">
              Billing requires a W-9 uploaded as a document. Verification stays
              pending until an external check runs, so a company created here is
              never shown as verified.
            </p>
          </>
        )}
      </div>
    </PageShell>
  )
}

function CompanySkeletons() {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {[0, 1].map((key) => (
        <Card key={key}>
          <CardContent className="space-y-3 pt-5">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-3 w-28" />
            <Skeleton className="h-20 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function CompanyCard({ company, active }: { company: Company; active: boolean }) {
  const { switchCompany, switching, can, refresh } = useCompany()
  const [archiving, setArchiving] = React.useState(false)

  const archive = useCompanyMutation<unknown, string>({
    context: { companyPublicId: company.public_id },
    mutationFn: (reason) =>
      api.delete(`/companies/current?reason=${encodeURIComponent(reason)}`, {
        companyPublicId: company.public_id,
      }),
    invalidate: [],
    onSuccess: () => {
      notifySuccess('Company archived.', `${company.display_name} is archived.`)
      setArchiving(false)
      void refresh()
    },
  })

  return (
    <>
      <Card className={active ? 'border-primary/40 ring-1 ring-primary/20' : undefined}>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <CardTitle className="flex items-center gap-2">
                <Building2 aria-hidden className="size-4 shrink-0 text-primary" />
                <span className="truncate">{company.display_name}</span>
              </CardTitle>
              <CardDescription className="mt-1 flex flex-wrap items-center gap-2">
                <PublicId value={company.public_id} kind="company" />
                <VerificationBadge state={company.verification_state} />
              </CardDescription>
            </div>
            {active ? <Badge tone="primary">Active</Badge> : null}
          </div>
        </CardHeader>

        <CardContent className="space-y-4">
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <Detail label="Legal name" value={company.legal_name ?? '—'} />
            <Detail label="Country" value={company.country_code ?? '—'} />
            <Detail label="Default currency" value={company.default_currency} />
            <Detail label="Member since" value={formatDate(company.created_at)} />
            <Detail label="Status" value={company.status} />
            <Detail label="Permissions" value={`${company.my_permissions.length} granted`} />
          </dl>

          <div>
            <p className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
              Your roles here
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {company.my_role_keys.length === 0 ? (
                <span className="text-sm text-muted-foreground">None</span>
              ) : (
                company.my_role_keys.map((key) => (
                  <Badge key={key} tone="outline">
                    {key}
                  </Badge>
                ))
              )}
            </div>
          </div>
        </CardContent>

        <CardFooter>
          {active ? (
            <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <Check aria-hidden className="size-4 text-success" />
              You are working in this company
            </span>
          ) : (
            <Button
              variant="outline"
              size="sm"
              disabled={switching}
              onClick={() => void switchCompany(company.public_id)}
            >
              Switch to this company
            </Button>
          )}

          {can('companies.delete') ? (
            <Button
              variant="ghost"
              size="sm"
              className="ml-auto text-danger hover:bg-danger-soft"
              onClick={() => setArchiving(true)}
            >
              Archive
            </Button>
          ) : null}
        </CardFooter>
      </Card>

      <ReasonDialog
        open={archiving}
        onOpenChange={setArchiving}
        title={`Archive ${company.display_name}`}
        description="Archiving hides this company from your switcher. Its projects, contracts and financial history are retained for audit, and the server records who archived it and why."
        confirmLabel="Archive company"
        label="Reason for archiving"
        busy={archive.isPending}
        error={archive.isError ? archive.error : null}
        onConfirm={(reason) => archive.mutate(reason)}
      />
    </>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        {label}
      </dt>
      <dd className="truncate">{value}</dd>
    </div>
  )
}

function VerificationBadge({ state }: { state: string }) {
  if (state === 'VERIFIED') return <Badge tone="success">Verified</Badge>
  // Stated plainly: OCR of a W-9 is not legal or IRS verification.
  if (state === 'UNVERIFIED') return <Badge tone="warning">Verification pending</Badge>
  return <Badge tone="neutral">{state.toLowerCase()}</Badge>
}

/* -------------------------------------------------------------------------- */
/* Create company                                                             */
/* -------------------------------------------------------------------------- */

const companySchema = z.object({
  legal_name: z.string().trim().min(2, 'Enter the registered legal name.').max(200),
  display_name: z.string().trim().min(2, 'Enter a display name.').max(200),
  country_code: z
    .string()
    .trim()
    .length(2, 'Use a two-letter ISO country code, for example US.')
    .transform((value) => value.toUpperCase()),
  default_currency: z
    .string()
    .trim()
    .length(3, 'Choose a currency.')
    .transform((value) => value.toUpperCase()),
  city: z.string().trim(),
  region: z.string().trim(),
  tax_classification: z.string().trim(),
  tin_type: z.string().trim(),
  tin_last4: z.string().trim(),
  address_line1: z.string().trim(),
  postal_code: z.string().trim(),
})

type CompanyFormValues = z.infer<typeof companySchema>

const EMPTY: CompanyFormValues = {
  legal_name: '',
  display_name: '',
  country_code: 'US',
  default_currency: 'USD',
  city: '',
  region: '',
  tax_classification: '',
  tin_type: '',
  tin_last4: '',
  address_line1: '',
  postal_code: '',
}

/**
 * Create a company.
 *
 * A founding W-9 is uploaded first through the same intake the onboarding
 * wizard uses; the server refuses to create the company without it, so the
 * dialog requires the upload rather than letting the submit fail.
 *
 * The idempotency key is generated per submit rather than per render: a retried
 * submit after a network failure must reuse the same key, but a fresh form
 * instance must not.
 */
export function CreateCompanyDialog({ triggerLabel = 'New company' }: { triggerLabel?: string }) {
  const { activeCompanyPublicId, refresh } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [values, setValues] = React.useState<CompanyFormValues>(EMPTY)
  const [errors, setErrors] = React.useState<Partial<Record<string, string>>>({})
  const [w9, setW9] = React.useState<{ public_id: string; name: string } | null>(null)
  const [uploadingW9, setUploadingW9] = React.useState(false)
  const [w9Error, setW9Error] = React.useState<string | null>(null)
  const [step, setStep] = React.useState<'form' | 'review'>('form')
  const [intake, setIntake] = React.useState<W9IntakeState | null>(null)
  const [fieldErrors, setFieldErrors] = React.useState<Partial<Record<W9FieldKey, string>>>({})

  React.useEffect(() => {
    if (open) {
      setValues(EMPTY)
      setErrors({})
      setW9(null)
      setW9Error(null)
      setUploadingW9(false)
      setStep('form')
      setIntake(null)
      setFieldErrors({})
    }
  }, [open])

  const uploadW9 = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploadingW9(true)
    setW9Error(null)
    try {
      const uploaded = await uploadFoundingW9(file)
      setW9({ public_id: uploaded.public_id, name: file.name })
    } catch (cause) {
      setW9Error(cause instanceof Error ? cause.message : 'That upload did not work.')
    } finally {
      setUploadingW9(false)
    }
  }

  const create = useCompanyMutation<Company, CompanyFormValues>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (form) => {
      const idempotencyKey = createIdempotencyKey('company')
      return api.post<Company>(
        '/companies',
        {
          legal_name: form.legal_name,
          display_name: form.display_name,
          country_code: form.country_code,
          default_currency: form.default_currency,
          city: form.city || undefined,
          region: form.region || undefined,
          address_line1: form.address_line1 || undefined,
          postal_code: form.postal_code || undefined,
          tax_classification: form.tax_classification || undefined,
          tin_type: form.tin_type || undefined,
          tin_last4: form.tin_last4 || undefined,
          w9_document_public_id: w9?.public_id,
        },
        { companyPublicId: activeCompanyPublicId, idempotencyKey },
      )
    },
    invalidate: [],
    onSuccess: (company) => {
      notifySuccess('Company created.', `${company.display_name} · ${company.public_id}`)
      setOpen(false)
      void refresh()
    },
  })

  const toW9Values = (form: CompanyFormValues): W9FormValues => ({
    legal_name: form.legal_name,
    tax_classification: form.tax_classification,
    tin_type: form.tin_type,
    tin_last4: form.tin_last4,
    address_line1: form.address_line1,
    city: form.city,
    region: form.region,
    postal_code: form.postal_code,
  })

  const set = <K extends keyof CompanyFormValues>(key: K, value: CompanyFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()

    if (!w9) {
      setW9Error('Upload a W-9 first — the company cannot be created without one.')
      return
    }

    const parsed = companySchema.safeParse(values)
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

    // Field-level W-9 validation mirrors the server: the review screen shows
    // one message per line rather than a single rejection.
    const w9Problems = validateW9(toW9Values(parsed.data), parsed.data.country_code)
    setFieldErrors(w9Problems)

    try {
      const state = await api.get<W9IntakeState>(
        `/companies/w9-intake/${encodeURIComponent(w9.public_id)}`,
      )
      setIntake(state)
    } catch {
      setIntake(null)
    }
    setStep('review')
  }

  const confirm = async () => {
    setErrors({})

    try {
      await create.mutateAsync(values)
    } catch (cause) {
      // The server is authoritative: map its field errors back onto the form
      // and drop to the form step so each line can be corrected.
      const fields =
        cause instanceof Error && 'details' in cause
          ? (cause as { details?: { fields?: Partial<Record<W9FieldKey, string>> } }).details
              ?.fields
          : undefined
      if (fields && Object.keys(fields).length > 0) {
        setFieldErrors(fields)
        setStep('form')
        notifyError(cause, 'Review the highlighted W-9 fields.')
      } else {
        notifyError(cause, 'The company could not be created.')
      }
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Building2 aria-hidden />
        {triggerLabel}
      </Button>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title={step === 'review' ? 'Review the W-9 information' : 'Create a company'}
        description={
          step === 'review'
            ? 'Confirm every line before the company is created. Only the last four of the TIN are stored.'
            : 'You become its first super admin. A founding W-9 is required: it is stored as the company\u2019s first document and billing stays disabled until it is processed.'
        }
        footer={
          step === 'review' ? (
            <>
              <Button variant="ghost" onClick={() => setStep('form')} disabled={create.isPending}>
                Back to edit
              </Button>
              <Button onClick={() => void confirm()} loading={create.isPending}>
                Confirm and create
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
                Cancel
              </Button>
              <Button type="submit" form="create-company" loading={false} disabled={uploadingW9}>
                Review W-9
              </Button>
            </>
          )
        }
      >
        {step === 'review' ? (
          <W9Review
            values={toW9Values(values)}
            errors={fieldErrors}
            fileName={w9?.name ?? null}
            intake={intake}
          />
        ) : (
        <form id="create-company" onSubmit={submit} className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
          <Field
            label="Founding W-9"
            error={w9Error ?? undefined}
            required
            hint="PDF or image. Scanned, validated and stored privately before the company exists."
          >
            {w9 ? (
              <p className="text-sm">
                Attached: <span className="font-medium">{w9.name}</span>{' '}
                <button
                  type="button"
                  className="text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
                  onClick={() => setW9(null)}
                >
                  Replace
                </button>
              </p>
            ) : (
              <Input
                id="company-w9"
                type="file"
                accept="application/pdf,image/*"
                disabled={uploadingW9}
                onChange={(event) => void uploadW9(event)}
                aria-invalid={Boolean(w9Error)}
              />
            )}
            {uploadingW9 ? (
              <p className="mt-1 text-xs text-muted-foreground">Uploading…</p>
            ) : null}
          </Field>

          <Field label="Legal name" htmlFor="company-legal-name" error={errors.legal_name ?? fieldErrors.legal_name} required>
            <Input
              id="company-legal-name"
              value={values.legal_name}
              onChange={(event) => set('legal_name', event.target.value)}
              aria-invalid={Boolean(errors.legal_name ?? fieldErrors.legal_name)}
            />
          </Field>

          <Field label="Display name" htmlFor="company-display-name" error={errors.display_name} required>
            <Input
              id="company-display-name"
              value={values.display_name}
              onChange={(event) => set('display_name', event.target.value)}
              aria-invalid={Boolean(errors.display_name)}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tax classification (Line 3a)" htmlFor="company-tax-classification" error={fieldErrors.tax_classification} required>
              <Select
                id="company-tax-classification"
                value={values.tax_classification}
                onChange={(event) => set('tax_classification', event.target.value)}
                aria-invalid={Boolean(fieldErrors.tax_classification)}
              >
                <option value="">Select…</option>
                {TAX_CLASSIFICATIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="TIN type (Part I)" htmlFor="company-tin-type" error={fieldErrors.tin_type} required>
              <Select
                id="company-tin-type"
                value={values.tin_type}
                onChange={(event) => set('tin_type', event.target.value)}
                aria-invalid={Boolean(fieldErrors.tin_type)}
              >
                <option value="">Select…</option>
                {TIN_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="TIN last 4 (Part I)"
              error={fieldErrors.tin_last4}
              required
              hint="Only the last four are stored."
            >
              <Input
                id="company-tin-last4"
                value={values.tin_last4}
                maxLength={4}
                inputMode="numeric"
                onChange={(event) => set('tin_last4', event.target.value.replace(/\D/g, ''))}
                aria-invalid={Boolean(fieldErrors.tin_last4)}
                className="font-mono"
                placeholder="4821"
              />
            </Field>
            <Field label="Street address (Line 5)" htmlFor="company-address" error={fieldErrors.address_line1} required>
              <Input
                id="company-address"
                value={values.address_line1}
                onChange={(event) => set('address_line1', event.target.value)}
                aria-invalid={Boolean(fieldErrors.address_line1)}
              />
            </Field>
            <Field label="Country code" htmlFor="company-country" error={errors.country_code} required hint="Two-letter ISO code">
              <Input
                id="company-country"
                value={values.country_code}
                maxLength={2}
                onChange={(event) => set('country_code', event.target.value.toUpperCase())}
                className="font-mono"
              />
            </Field>
            <Field label="Default currency" htmlFor="company-currency" error={errors.default_currency} required>
              <CurrencySelect
                id="company-currency"
                value={values.default_currency}
                onChange={(value) => set('default_currency', value)}
              />
            </Field>
            <Field label="City (Line 6)" htmlFor="company-city" error={fieldErrors.city} required>
              <Input
                id="company-city"
                value={values.city}
                onChange={(event) => set('city', event.target.value)}
                aria-invalid={Boolean(fieldErrors.city)}
              />
            </Field>
            <Field label="Region or state (Line 6)" htmlFor="company-region" error={fieldErrors.region} required>
              <Input
                id="company-region"
                value={values.region}
                onChange={(event) => set('region', event.target.value)}
                aria-invalid={Boolean(fieldErrors.region)}
              />
            </Field>
            <Field label="ZIP (Line 6)" htmlFor="company-postal" error={fieldErrors.postal_code} required>
              <Input
                id="company-postal"
                value={values.postal_code}
                onChange={(event) => set('postal_code', event.target.value)}
                aria-invalid={Boolean(fieldErrors.postal_code)}
              />
            </Field>
          </div>
        </form>
        )}
      </Dialog>
    </>
  )
}