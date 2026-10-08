'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { z } from 'zod'

import { api, createIdempotencyKey } from '@/lib/api'
import { uploadFoundingW9 } from '@/hooks/use-mutations'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert, Button } from '@/components/ui'
import { AuthShell } from '@/components/auth-shell'
import {
  TAX_CLASSIFICATIONS,
  TIN_TYPES,
  validateW9,
  type W9FieldKey,
  type W9FormValues,
} from '@/lib/w9'
import { W9Review, type W9IntakeState } from '@/components/w9-review'

const profileSchema = z.object({
  first_name: z.string().min(1, 'Enter your first name.').max(100),
  last_name: z.string().min(1, 'Enter your last name.').max(100),
})

const profileFields: FieldConfig[] = [
  { name: 'first_name', label: 'First name', autoComplete: 'given-name' },
  { name: 'last_name', label: 'Last name', autoComplete: 'family-name' },
]

const companySchema = z.object({
  legal_name: z.string().min(1, 'Enter the legal name.').max(200),
  display_name: z.string().min(1, 'Enter the display name.').max(120),
  country_code: z.string().length(2, 'Two-letter country code.'),
  default_currency: z.string().length(3, 'Three-letter currency.'),
  tax_classification: z.string().min(1, 'Select the Line 3a classification.'),
  tin_type: z.string().min(1, 'Select the Part I TIN type.'),
  tin_last4: z.string().min(4, 'Enter the last 4 of the TIN.').max(4),
  address_line1: z.string().min(1, 'Enter the Line 5 street address.').max(200),
  city: z.string().min(1, 'Enter the Line 6 city.').max(100),
  region: z.string().min(1, 'Enter the Line 6 state.').max(100),
  postal_code: z.string().min(1, 'Enter the Line 6 ZIP.').max(20),
})

type CompanyValues = z.infer<typeof companySchema>

const companyFields: FieldConfig[] = [
  { name: 'legal_name', label: 'Legal name (Line 1)' },
  { name: 'display_name', label: 'Display name' },
  {
    name: 'tax_classification',
    label: 'Tax classification (Line 3a)',
    options: TAX_CLASSIFICATIONS,
  },
  { name: 'tin_type', label: 'TIN type (Part I)', options: TIN_TYPES },
  { name: 'tin_last4', label: 'TIN last 4 (Part I)', placeholder: '4821' },
  { name: 'address_line1', label: 'Street address (Line 5)' },
  { name: 'city', label: 'City (Line 6)' },
  { name: 'region', label: 'State (Line 6)' },
  { name: 'postal_code', label: 'ZIP (Line 6)' },
  { name: 'country_code', label: 'Country', placeholder: 'US' },
  { name: 'default_currency', label: 'Currency', placeholder: 'USD' },
]

export default function OnboardingPage() {
  const router = useRouter()
  const [step, setStep] = React.useState(0)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [w9, setW9] = React.useState<{ public_id: string; name: string } | null>(null)
  const [uploadingW9, setUploadingW9] = React.useState(false)
  const [finishing, setFinishing] = React.useState(false)
  const [review, setReview] = React.useState<{
    values: CompanyValues
    errors: Partial<Record<W9FieldKey, string>>
    intake: W9IntakeState | null
  } | null>(null)
  const [creating, setCreating] = React.useState(false)

  const saveProfile = async (values: { first_name: string; last_name: string }) => {
    setNotice(null)
    await api.patch('/users/me', values)
    setStep(1)
  }

  const uploadW9 = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploadingW9(true)
    setNotice(null)
    try {
      const uploaded = await uploadFoundingW9(file)
      setW9({ public_id: uploaded.public_id, name: file.name })
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'That upload did not work.')
    } finally {
      setUploadingW9(false)
    }
  }

  const createCompany = async (values: CompanyValues) => {
    setNotice(null)
    if (!w9) {
      setNotice('Upload your W-9 first — a company cannot be created without one.')
      return
    }
    // Review before creating: per-line validation mirrors the server, and the
    // screen shows the intake pipeline state honestly.
    const w9values: W9FormValues = {
      legal_name: values.legal_name,
      tax_classification: values.tax_classification,
      tin_type: values.tin_type,
      tin_last4: values.tin_last4,
      address_line1: values.address_line1,
      city: values.city,
      region: values.region,
      postal_code: values.postal_code,
    }
    let intake: W9IntakeState | null = null
    try {
      intake = await api.get<W9IntakeState>(
        `/companies/w9-intake/${encodeURIComponent(w9.public_id)}`,
      )
    } catch {
      intake = null
    }
    setReview({ values, errors: validateW9(w9values, values.country_code), intake })
  }

  const confirmCompany = async () => {
    if (!review || !w9) return
    const values = review.values
    setCreating(true)
    setNotice(null)
    try {
      await api.post(
        '/companies',
        {
          ...values,
          country_code: values.country_code.toUpperCase(),
          default_currency: values.default_currency.toUpperCase(),
          w9_document_public_id: w9.public_id,
        },
        { idempotencyKey: createIdempotencyKey('company') },
      )
      setReview(null)
      setStep(2)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'The company could not be created.')
      setReview(null)
    } finally {
      setCreating(false)
    }
  }

  const finish = async () => {
    setFinishing(true)
    setNotice(null)
    try {
      await api.post('/users/me/onboarding')
      router.replace('/dashboard')
      router.refresh()
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'Could not finish onboarding.')
      setFinishing(false)
    }
  }

  const skipCompany = async () => {
    setNotice(null)
    setFinishing(true)
    try {
      // Company founding is optional: professionals join to network first and
      // found (or join) a company later from onboarding or the workspace.
      await api.post('/users/me/onboarding')
      router.replace('/network')
      router.refresh()
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'Could not continue.')
      setFinishing(false)
    }
  }

  return (
    <AuthShell title="Welcome to MyTrakin" subtitle="Your profile, then company if you want one.">
      <div className="space-y-4">
        {notice ? <Alert tone="info">{notice}</Alert> : null}

        {step === 0 ? (
          <SchemaForm<{ first_name: string; last_name: string }>
            schema={profileSchema}
            fields={profileFields}
            defaultValues={{ first_name: '', last_name: '' }}
            submitLabel="Continue"
            onSubmit={saveProfile}
            banner={null}
          />
        ) : null}

        {step === 1 ? (
          review ? (
            <div className="space-y-4">
              <W9Review
                values={{
                  legal_name: review.values.legal_name,
                  tax_classification: review.values.tax_classification,
                  tin_type: review.values.tin_type,
                  tin_last4: review.values.tin_last4,
                  address_line1: review.values.address_line1,
                  city: review.values.city,
                  region: review.values.region,
                  postal_code: review.values.postal_code,
                }}
                errors={review.errors}
                fileName={w9?.name ?? null}
                intake={review.intake}
              />
              <Button className="w-full" disabled={creating} onClick={() => void confirmCompany()}>
                {creating ? 'Creating…' : 'Confirm and create company'}
              </Button>
              <button
                type="button"
                disabled={creating}
                onClick={() => setReview(null)}
                className="w-full text-center text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                Back to edit
              </button>
            </div>
          ) : (
          <div className="space-y-4">
            <div className="rounded-md border border-border p-3">
              <p className="text-sm font-medium">1. Upload your W-9 (PDF or image)</p>
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
            </div>
            <SchemaForm<CompanyValues>
              schema={companySchema}
              fields={companyFields}
              defaultValues={{
                legal_name: '',
                display_name: '',
                country_code: 'US',
                default_currency: 'USD',
                tax_classification: '',
                tin_type: '',
                tin_last4: '',
                address_line1: '',
                city: '',
                region: '',
                postal_code: '',
              }}
              submitLabel="Review W-9"
              onSubmit={createCompany}
              banner={null}
            />
            <button
              type="button"
              disabled={finishing}
              onClick={() => void skipCompany()}
              className="w-full text-center text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              {finishing ? 'Continuing…' : 'Skip for now — I just want to network'}
            </button>
          </div>
          )
        ) : null}

        {step === 2 ? (
          <div className="space-y-3 text-center">
            <p className="text-sm text-muted-foreground">
              Profile saved and company created. You are its super admin.
            </p>
            <Button className="w-full" disabled={finishing} onClick={() => void finish()}>
              {finishing ? 'Finishing…' : 'Enter your workspace'}
            </Button>
          </div>
        ) : null}
      </div>
    </AuthShell>
  )
}
