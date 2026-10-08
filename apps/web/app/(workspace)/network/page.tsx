'use client'

import * as React from 'react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { PageHeader, PageShell } from '@/components/page'
import { Alert, Button, Card, CardContent, CardHeader, CardTitle } from '@/components/ui'
import { ErrorState, LoadingBlock } from '@/components/query'
import { notifyError } from '@/components/toast'
import { ProfessionalFeed } from '@/components/post-feed'

interface Connection {
  public_id: string
  display_name: string | null
  headline: string | null
}

export default function NetworkPage() {
  const { me } = useCompany()
  const [connections, setConnections] = React.useState<Connection[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [invite, setInvite] = React.useState('')

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const directory = await api.get<{ data: Connection[] }>('/connections?limit=100')
      setConnections(directory.data)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  const connect = async () => {
    const target = invite.trim()
    if (!target) return
    setNotice(null)
    try {
      await api.post('/connections/requests', { user_id: target })
      setInvite('')
      setNotice('Connection request sent.')
    } catch (cause) {
      notifyError(cause)
    }
  }

  return (
    <PageShell>
      <PageHeader title="Network" description="Posts and professional connections." />
      {notice ? (
        <div className="mb-4">
          <Alert tone="info">{notice}</Alert>
        </div>
      ) : null}
      {error ? (
        <ErrorState error={error} onRetry={() => void load()} />
      ) : connections === null ? (
        <LoadingBlock />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <ProfessionalFeed mePublicId={me?.public_id ?? null} />

          <Card>
            <CardHeader>
              <CardTitle>Connections · {connections.length}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex gap-2">
                <input
                  value={invite}
                  onChange={(event) => setInvite(event.target.value)}
                  placeholder="User ID (U…)"
                  aria-label="User public id"
                  className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
                />
                <Button size="sm" onClick={() => void connect()}>
                  Connect
                </Button>
              </div>
              <div className="space-y-2">
                {connections.map((connection) => (
                  <div key={connection.public_id} className="text-sm">
                    <p className="font-medium">
                      {connection.display_name ?? connection.public_id}
                    </p>
                    {connection.headline ? (
                      <p className="text-xs text-muted-foreground">{connection.headline}</p>
                    ) : null}
                  </div>
                ))}
                {connections.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No connections yet.</p>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </PageShell>
  )
}
