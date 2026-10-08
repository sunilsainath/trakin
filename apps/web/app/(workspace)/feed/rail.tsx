'use client'

import * as React from 'react'
import Link from 'next/link'

import { api } from '@/lib/api'
import { initials } from '@/lib/utils'
import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { ErrorState, LoadingBlock, PermissionState, isPermissionError } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

interface SuggestedPerson {
  user_public_id: string
  display_name: string | null
  headline: string | null
  mutual_count: number
}

interface SuggestedCompany {
  company_public_id: string
  name: string
  connections_count: number
}

interface SuggestionsResponse {
  people: SuggestedPerson[]
  companies: SuggestedCompany[]
}

/**
 * The feed's right rail: who to connect with, and where your network works.
 *
 * People come from mutual connections; companies are the ACTIVE employers of
 * those connections, excluding your own. Both lists are live graph reads —
 * an empty graph renders empty states, never invented rows. Connecting
 * removes the person from the list, because a pending request excludes them
 * server-side on the next fetch.
 */
export function RightRail() {
  const [suggestions, setSuggestions] = React.useState<SuggestionsResponse | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [connecting, setConnecting] = React.useState<string | null>(null)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const rows = await api.get<SuggestionsResponse>('/connections/suggestions')
      setSuggestions(rows)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  const connect = async (userPublicId: string) => {
    setConnecting(userPublicId)
    try {
      await api.post('/connections/requests', { user_id: userPublicId })
      notifySuccess('Connection request sent.')
      await load()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setConnecting(null)
    }
  }

  if (error) {
    return isPermissionError(error) ? (
      <PermissionState error={error} />
    ) : (
      <ErrorState error={error} onRetry={() => void load()} />
    )
  }
  if (suggestions === null) {
    return (
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Connection suggestions</CardTitle>
          </CardHeader>
          <CardContent>
            <LoadingBlock rows={3} />
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Connection suggestions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {suggestions.people.length === 0 ? (
            <EmptyState
              title="No suggestions right now"
              description="Suggestions appear once your network grows — every new connection teaches it who to introduce next."
            />
          ) : (
            <ul className="space-y-3">
              {suggestions.people.map((person) => (
                <li key={person.user_public_id} className="flex items-start gap-2.5">
                  <span
                    aria-hidden
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary text-2xs font-semibold text-secondary-foreground"
                  >
                    {initials(person.display_name ?? person.user_public_id)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {person.display_name ?? person.user_public_id}
                    </p>
                    {person.headline ? (
                      <p className="truncate text-xs text-muted-foreground">{person.headline}</p>
                    ) : null}
                    <p className="text-xs text-muted-foreground">
                      {person.mutual_count} mutual connection
                      {person.mutual_count === 1 ? '' : 's'}
                    </p>
                    <Button
                      size="sm"
                      variant="outline"
                      className="mt-1.5"
                      disabled={connecting !== null}
                      onClick={() => void connect(person.user_public_id)}
                    >
                      {connecting === person.user_public_id ? 'Sending…' : 'Connect'}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Ad Centre</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Suggested for you — companies where your connections work.
          </p>
          {suggestions.companies.length === 0 ? (
            <EmptyState
              title="Nothing promoted right now"
              description="Company spotlights appear once your connections span more organisations."
            />
          ) : (
            <ul className="space-y-3">
              {suggestions.companies.map((company) => (
                <li key={company.company_public_id} className="flex items-start gap-2.5">
                  <span
                    aria-hidden
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary-soft text-2xs font-bold text-primary-strong"
                  >
                    {initials(company.name)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{company.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {company.connections_count} connection
                      {company.connections_count === 1 ? '' : 's'} work here
                    </p>
                    <Button size="sm" variant="ghost" className="mt-1 h-7 px-2" asChild>
                      <Link href="/companies">View</Link>
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
