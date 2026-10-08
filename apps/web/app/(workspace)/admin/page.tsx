'use client'

import * as React from 'react'

import { api } from '@/lib/api'
import { PageHeader, PageShell } from '@/components/page'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui'
import { ErrorState, LoadingBlock, PermissionState, isPermissionError } from '@/components/query'

interface Overview {
  users: number
  pending_verification: number
  companies: number
  audit_24h: number
  ai_tokens_24h: number
  outbox_pending: number
}

interface Flag {
  key: string
  description: string
  enabled: boolean
  rollout_pct: number
  updated_at: string
}

export default function AdminPage() {
  const [overview, setOverview] = React.useState<Overview | null>(null)
  const [flags, setFlags] = React.useState<Flag[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const [metrics, flagList] = await Promise.all([
        api.get<Overview>('/admin/overview'),
        api.get<{ data: Flag[] }>('/admin/flags'),
      ])
      setOverview(metrics)
      setFlags(flagList.data)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  return (
    <PageShell>
      <PageHeader
        title="Platform administration"
        description="Support overview. Requires an explicit platform grant — company admins see a permission state here, never data."
      />
      {error ? (
        isPermissionError(error) ? (
          <PermissionState error={error} />
        ) : (
          <ErrorState error={error} onRetry={() => void load()} />
        )
      ) : overview === null ? (
        <LoadingBlock />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {(
              [
                ['Registered users', overview.users],
                ['Pending verification', overview.pending_verification],
                ['Active companies', overview.companies],
                ['Audit events (24h)', overview.audit_24h],
                ['AI tokens (24h)', overview.ai_tokens_24h],
                ['Outbox pending', overview.outbox_pending],
              ] as const
            ).map(([label, value]) => (
              <Card key={label}>
                <CardContent className="pt-5">
                  <p className="text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                    {label}
                  </p>
                  <p className="mt-1 text-2xl font-semibold">{value}</p>
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Feature flags</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {(flags ?? []).map((flag) => (
                  <div
                    key={flag.key}
                    className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm"
                  >
                    <div>
                      <p className="font-mono text-xs">{flag.key}</p>
                      <p className="text-xs text-muted-foreground">{flag.description}</p>
                    </div>
                    <span
                      className={`rounded-full px-2 py-0.5 text-2xs font-semibold ${
                        flag.enabled
                          ? 'bg-primary-soft text-primary-strong'
                          : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {flag.enabled ? `ON · ${flag.rollout_pct}%` : 'OFF'}
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </PageShell>
  )
}
