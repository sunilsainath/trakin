'use client'

import * as React from 'react'
import Link from 'next/link'
import { UserCheck, UserPlus, Users } from 'lucide-react'

import { api } from '@/lib/api'
import { Avatar, Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

interface ConnectionPerson {
  public_id: string
  display_name: string | null
  headline: string | null
  avatar_url?: string | null
  message?: string | null
  created_at?: string
}

interface RequestsResponse {
  incoming: ConnectionPerson[]
  outgoing: ConnectionPerson[]
}

/**
 * Connections: who asked to connect, who is waiting on you, and who you
 * are connected to.
 *
 * Accepting and declining run through the request endpoints; removing a
 * connection is a soft state change server-side, so history survives.
 */
export default function ConnectionsPage() {
  const [requests, setRequests] = React.useState<RequestsResponse | null>(null)
  const [connections, setConnections] = React.useState<ConnectionPerson[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [userId, setUserId] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const [pending, directory] = await Promise.all([
        api.get<RequestsResponse>('/connections/requests'),
        api.get<{ data: ConnectionPerson[] }>('/connections?limit=100'),
      ])
      setRequests(pending)
      setConnections(directory.data)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  const act = async (work: () => Promise<unknown>, ok: string, fail: string) => {
    setBusy(true)
    try {
      await work()
      notifySuccess(ok)
      await load()
    } catch (cause) {
      notifyError(cause, fail)
    } finally {
      setBusy(false)
    }
  }

  const connect = () => {
    const target = userId.trim().toUpperCase()
    if (!target) return
    void act(
      () => api.post('/connections/requests', { user_id: target }),
      'Connection request sent.',
      'The request could not be sent.',
    ).then(() => setUserId(''))
  }

  if (error) {
    return (
      <PageShell>
        <ErrorState error={error} onRetry={() => void load()} />
      </PageShell>
    )
  }

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Connections' }]}
          title="Connections"
          description="People you are connected to, requests waiting on you, and requests waiting on them."
        />

        {requests === null || connections === null ? (
          <LoadingBlock rows={6} />
        ) : (
          <>
            {requests.incoming.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <UserCheck aria-hidden className="size-4 text-primary" />
                    Waiting on you · {requests.incoming.length}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ul className="divide-y divide-border/60">
                    {requests.incoming.map((person) => (
                      <li key={person.public_id} className="flex items-center gap-3 py-2.5">
                        <Avatar name={person.display_name ?? person.public_id} size="sm" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {person.display_name ?? person.public_id}
                          </p>
                          {person.headline ? (
                            <p className="truncate text-xs text-muted-foreground">{person.headline}</p>
                          ) : null}
                          {person.message ? (
                            <p className="truncate text-xs text-muted-foreground">
                              “{person.message}”
                            </p>
                          ) : null}
                        </div>
                        <Badge tone="warning">Pending</Badge>
                        <Button
                          size="sm"
                          disabled={busy}
                          onClick={() =>
                            void act(
                              () => api.post(`/connections/requests/${person.public_id}/accept`, {}),
                              `Connected with ${person.display_name ?? 'them'}.`,
                              'The request could not be accepted.',
                            )
                          }
                        >
                          Accept
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={busy}
                          onClick={() =>
                            void act(
                              () => api.post(`/connections/requests/${person.public_id}/decline`, {}),
                              'Request declined.',
                              'The request could not be declined.',
                            )
                          }
                        >
                          Decline
                        </Button>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ) : null}

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Users aria-hidden className="size-4 text-primary" />
                  Your connections · {connections.length}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {connections.length === 0 ? (
                  <EmptyState
                    icon={<Users aria-hidden />}
                    title="No connections yet"
                    description="Accept a request above, or connect with someone by their user ID."
                  />
                ) : (
                  <ul className="grid gap-2 sm:grid-cols-2">
                    {connections.map((person) => (
                      <li
                        key={person.public_id}
                        className="flex items-center gap-2.5 rounded-md border border-border px-3 py-2"
                      >
                        <Avatar name={person.display_name ?? person.public_id} src={person.avatar_url} size="sm" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium">
                            {person.display_name ?? person.public_id}
                          </p>
                          {person.headline ? (
                            <p className="truncate text-xs text-muted-foreground">{person.headline}</p>
                          ) : null}
                        </div>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-danger hover:bg-danger-soft"
                          disabled={busy}
                          onClick={() =>
                            void act(
                              () => api.delete(`/connections/${person.public_id}`),
                              'Connection removed.',
                              'The connection could not be removed.',
                            )
                          }
                        >
                          Remove
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}

                {requests.outgoing.length > 0 ? (
                  <div className="mt-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-subtle-foreground">
                      Waiting on them · {requests.outgoing.length}
                    </p>
                    <ul className="mt-1.5 space-y-1">
                      {requests.outgoing.map((person) => (
                        <li key={person.public_id} className="text-sm text-muted-foreground">
                          {person.display_name ?? person.public_id}{' '}
                          <Badge tone="neutral">Sent</Badge>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="mt-4 flex gap-2 border-t border-border/60 pt-4">
                  <input
                    value={userId}
                    onChange={(event) => setUserId(event.target.value)}
                    placeholder="User ID (U…)"
                    aria-label="User public id"
                    className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
                  />
                  <Button size="sm" disabled={busy || !userId.trim()} onClick={connect}>
                    <UserPlus aria-hidden />
                    Connect
                  </Button>
                  <Link
                    href="/search"
                    className="inline-flex h-9 items-center rounded-md px-3 text-sm font-medium text-primary hover:underline"
                  >
                    Find people
                  </Link>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </PageShell>
  )
}
