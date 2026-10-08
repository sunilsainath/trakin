'use client'

import * as React from 'react'
import { z } from 'zod'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import type { Me } from '@/lib/types'
import { PageHeader, PageShell } from '@/components/page'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert } from '@/components/ui'
import { ErrorState, LoadingBlock } from '@/components/query'

const profileSchema = z.object({
  first_name: z.string().min(1, 'Enter your first name.').max(100),
  last_name: z.string().min(1, 'Enter your last name.').max(100),
  phone_e164: z.string().max(20).optional().or(z.literal('')),
  headline: z.string().max(200).optional().or(z.literal('')),
  bio: z.string().max(5000).optional().or(z.literal('')),
  location_city: z.string().max(100).optional().or(z.literal('')),
  location_country: z.string().max(2).optional().or(z.literal('')),
  timezone: z.string().max(64).optional().or(z.literal('')),
})

type ProfileValues = z.infer<typeof profileSchema>

const profileFields: FieldConfig[] = [
  { name: 'first_name', label: 'First name' },
  { name: 'last_name', label: 'Last name' },
  { name: 'phone_e164', label: 'Phone', type: 'tel', hint: 'E.164 format, e.g. +14155550132' },
  { name: 'headline', label: 'Professional headline', placeholder: 'Senior Java Developer' },
  { name: 'bio', label: 'Bio' },
  { name: 'location_city', label: 'City' },
  { name: 'location_country', label: 'Country code', placeholder: 'IN', maxLength: 2 },
  { name: 'timezone', label: 'Timezone', placeholder: 'Asia/Kolkata' },
]

const VISIBILITIES = ['PUBLIC', 'CONNECTIONS', 'PRIVATE'] as const

export default function ProfileSettingsPage() {
  const { me, loading, error, refresh } = useCompany()
  const [notice, setNotice] = React.useState<string | null>(null)
  const [visibility, setVisibility] = React.useState<Record<string, string>>({})
  const [savingVisibility, setSavingVisibility] = React.useState(false)

  React.useEffect(() => {
    const privacy = me?.settings?.privacy
    if (privacy && typeof privacy === 'object') {
      setVisibility(privacy as Record<string, string>)
    }
  }, [me])

  if (loading) {
    return (
      <PageShell>
        <LoadingBlock />
      </PageShell>
    )
  }

  if (error || !me) {
    return (
      <PageShell>
        <ErrorState error={error} onRetry={() => void refresh()} />
      </PageShell>
    )
  }

  const onSubmit = async (values: ProfileValues) => {
    setNotice(null)
    const changes: Record<string, string | null> = {}
    for (const [key, value] of Object.entries(values)) {
      changes[key] = value === '' ? null : value
    }
    await api.patch<Me>('/users/me', changes)
    setNotice('Profile saved.')
    await refresh()
  }

  const saveVisibility = async () => {
    setSavingVisibility(true)
    try {
      await api.put('/users/me/privacy', visibility)
      setNotice('Visibility saved.')
      await refresh()
    } finally {
      setSavingVisibility(false)
    }
  }

  return (
    <PageShell>
      <PageHeader title="Profile" description="How you appear across the platform." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Personal information</CardTitle>
          </CardHeader>
          <CardContent>
            {notice ? (
              <div className="mb-4">
                <Alert tone="info">{notice}</Alert>
              </div>
            ) : null}
            <SchemaForm<ProfileValues>
              schema={profileSchema}
              fields={profileFields}
              defaultValues={{
                first_name: me.first_name ?? '',
                last_name: me.last_name ?? '',
                phone_e164: me.phone_e164 ?? '',
                headline: me.headline ?? '',
                bio: me.bio ?? '',
                location_city: me.location_city ?? '',
                location_country: me.country_code ?? '',
                timezone: me.timezone ?? '',
              }}
              submitLabel="Save profile"
              onSubmit={onSubmit}
              banner={null}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Field visibility</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Control who sees contact and location fields. Your company membership list
              is never visible to other users.
            </p>
            {['contact.email', 'contact.phone', 'location'].map((field) => (
              <label key={field} className="flex items-center justify-between gap-3 text-sm">
                <span className="font-medium">{field}</span>
                <select
                  value={visibility[field] ?? 'CONNECTIONS'}
                  onChange={(event) =>
                    setVisibility((current) => ({ ...current, [field]: event.target.value }))
                  }
                  className="h-9 rounded-md border border-input bg-surface px-2 text-sm"
                >
                  {VISIBILITIES.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </label>
            ))}
            <button
              type="button"
              disabled={savingVisibility}
              onClick={() => void saveVisibility()}
              className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              {savingVisibility ? 'Saving…' : 'Save visibility'}
            </button>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  )
}
