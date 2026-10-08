'use client'

import * as React from 'react'

import { api } from '@/lib/api'
import { PageHeader, PageShell } from '@/components/page'
import { Alert, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { ErrorState, LoadingBlock } from '@/components/query'

interface Preference {
  category: string
  in_app: boolean
  email: boolean
  push: boolean
}

const CHANNELS = [
  { key: 'in_app', label: 'In app' },
  { key: 'email', label: 'Email' },
  { key: 'push', label: 'Push' },
] as const

export default function NotificationSettingsPage() {
  const [preferences, setPreferences] = React.useState<Preference[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [saving, setSaving] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      setPreferences(await api.get<Preference[]>('/users/me/notification-preferences'))
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  const toggle = (category: string, channel: (typeof CHANNELS)[number]['key']) => {
    setPreferences((current) =>
      (current ?? []).map((row) =>
        row.category === category ? { ...row, [channel]: !row[channel] } : row,
      ),
    )
  }

  const save = async () => {
    if (!preferences) return
    setSaving(true)
    setNotice(null)
    try {
      const updated = await api.put<Preference[]>('/users/me/notification-preferences', preferences)
      setPreferences(updated)
      setNotice('Notification preferences saved.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <PageShell>
      <PageHeader
        title="Notifications"
        description="Choose which channels deliver each category."
      />
      {notice ? (
        <div className="mb-4">
          <Alert tone="info">{notice}</Alert>
        </div>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle>Delivery channels</CardTitle>
        </CardHeader>
        <CardContent>
          {error ? (
            <ErrorState error={error} onRetry={() => void load()} />
          ) : preferences === null ? (
            <LoadingBlock />
          ) : preferences.length === 0 ? (
            <EmptyState title="No preferences" description="Nothing to configure yet." />
          ) : (
            <div className="space-y-1">
              <div className="grid grid-cols-[1fr_repeat(3,5rem)] items-center gap-2 px-3 pb-2 text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                <span>Category</span>
                {CHANNELS.map((channel) => (
                  <span key={channel.key} className="text-center">
                    {channel.label}
                  </span>
                ))}
              </div>
              {preferences.map((row) => (
                <div
                  key={row.category}
                  className="grid grid-cols-[1fr_repeat(3,5rem)] items-center gap-2 rounded-md px-3 py-1.5 hover:bg-muted"
                >
                  <span className="text-sm font-medium">{row.category}</span>
                  {CHANNELS.map((channel) => (
                    <input
                      key={channel.key}
                      type="checkbox"
                      aria-label={`${row.category} ${channel.label}`}
                      checked={row[channel.key]}
                      onChange={() => toggle(row.category, channel.key)}
                      className="mx-auto h-4 w-4 accent-primary"
                    />
                  ))}
                </div>
              ))}
              <div className="pt-3">
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void save()}
                  className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  {saving ? 'Saving…' : 'Save preferences'}
                </button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </PageShell>
  )
}
