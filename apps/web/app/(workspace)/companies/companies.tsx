'use client'

import * as React from 'react'
import { z } from 'zod'
import { Building2, Check } from 'lucide-react'

import { api, createIdempotencyKey } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation, uploadFoundingW9 } from '@/hooks/use-mutations'
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
  Skeleton,
} from '@/components/ui'
import { CurrencySelect, Field } from '@/components/forms'
import { ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'
import { PublicId } from '@/components/public-id'
import { ErrorState } from '@/components/query'
import { PageHeader, PageShell } from '@/components/page'

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
})

type CompanyFormValues = z.infer<typeof companySchema>

const EMPTY: CompanyFormValues = {
  legal_name: '',
  display_name: '',
  country_code: 'US',
  default_currency: 'USD',
  city: '',
  region: '',
}

/**
 * Create a company.
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

  React.useEffect(() => {
    if (open) {
      setValues(EMPTY)
      setErrors({})
      setW9(null)
    }
  }, [open])

  const uploadW9 = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploadingW9(true)
    try {
      const uploaded = await uploadFoundingW9(file)
      setW9({ public_id: uploaded.public_id, name: file.name })
      setErrors((previous) => ({ ...previous, w9: undefined }))
    } catch (cause) {
      setErrors((previous) => ({
        ...previous,
        w9: cause instanceof Error ? cause.message : 'That upload did not work.',
      }))
    } finally {
      setUploadingW9(false)
    }
  }

  const create = useCompanyMutation<Company, CompanyFormValues & { w9_document_public_id: string }>({
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
          w9_document_public_id: form.w9_document_public_id,
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

  const set = <K extends keyof CompanyFormValues>(key: K, value: CompanyFormValues[K]) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()

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
    if (!w9) {
      setErrors({ w9: 'Upload the founding W-9 first — a company cannot be created without one.' })
      return
    }

    setErrors({})

    try {
      await create.mutateAsync({ ...parsed.data, w9_document_public_id: w9.public_id })
    } catch (cause) {
      notifyError(cause, 'The company could not be created.')
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
        title="Create a company"
        description="You become its first super admin. Billing stays disabled until a W-9 has been uploaded and processed."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="create-company" loading={create.isPending}>
              Create company
            </Button>
          </>
        }
      >
        <form id="create-company" onSubmit={submit} className="space-y-4">
          <div className="rounded-md border border-border p-3">
            <p className="text-sm font-medium">Founding W-9 (PDF or image)</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {w9 ? `Attached: ${w9.name}` : 'Required before the company can be created.'}
            </p>
            <input
              type="file"
              accept="application/pdf,image/*"
              disabled={uploadingW9}
              onChange={(event) => void uploadW9(event)}
              className="mt-2 text-sm"
              aria-label="W-9 file"
            />
            {uploadingW9 ? (
              <p className="mt-1 text-xs text-muted-foreground">Uploading…</p>
            ) : null}
            {errors.w9 ? (
              <p role="alert" className="mt-1 text-xs font-medium text-danger">
                {errors.w9}
              </p>
            ) : null}
          </div>
          <Field label="Legal name" error={errors.legal_name} required>
            <Input
              id="company-legal-name"
              value={values.legal_name}
              onChange={(event) => set('legal_name', event.target.value)}
              aria-invalid={Boolean(errors.legal_name)}
            />
          </Field>

          <Field label="Display name" error={errors.display_name} required>
            <Input
              id="company-display-name"
              value={values.display_name}
              onChange={(event) => set('display_name', event.target.value)}
              aria-invalid={Boolean(errors.display_name)}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Country code" error={errors.country_code} required hint="Two-letter ISO code">
              <Input
                id="company-country"
                value={values.country_code}
                maxLength={2}
                onChange={(event) => set('country_code', event.target.value.toUpperCase())}
                className="font-mono"
              />
            </Field>
            <Field label="Default currency" error={errors.default_currency} required>
              <CurrencySelect
                id="company-currency"
                value={values.default_currency}
                onChange={(value) => set('default_currency', value)}
              />
            </Field>
            <Field label="City">
              <Input
                id="company-city"
                value={values.city}
                onChange={(event) => set('city', event.target.value)}
              />
            </Field>
            <Field label="Region or state">
              <Input
                id="company-region"
                value={values.region}
                onChange={(event) => set('region', event.target.value)}
              />
            </Field>
          </div>
        </form>
      </Dialog>
    </>
  )
}