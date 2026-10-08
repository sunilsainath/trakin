'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { z } from 'zod'

import { api, createIdempotencyKey } from '@/lib/api'
import { uploadFoundingW9 } from '@/hooks/use-mutations'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert, Button } from '@/components/ui'
import { AuthShell } from '@/components/auth-shell'

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
})

const companyFields: FieldConfig[] = [
  { name: 'legal_name', label: 'Legal name' },
  { name: 'display_name', label: 'Display name' },
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

  const createCompany = async (values: {
    legal_name: string
    display_name: string
    country_code: string
    default_currency: string
  }) => {
    setNotice(null)
    if (!w9) {
      setNotice('Upload your W-9 first — a company cannot be created without one.')
      return
    }
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
    setStep(2)
  }

  const finish = async () => {
    setFinishing(true)
    setNotice(null)
    try {
      await api.post('/users/me/onboarding')
      router.replace('/feed')
      router.refresh()
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'Could not finish onboarding.')
      setFinishing(false)
    }
  }

  return (
    <AuthShell title="Welcome to MyTrakin" subtitle="Three steps and you are in.">
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
            <SchemaForm<{
              legal_name: string
              display_name: string
              country_code: string
              default_currency: string
            }>
              schema={companySchema}
              fields={companyFields}
              defaultValues={{
                legal_name: '',
                display_name: '',
                country_code: 'US',
                default_currency: 'USD',
              }}
              submitLabel="Create company"
              onSubmit={createCompany}
              banner={null}
            />
          </div>
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
