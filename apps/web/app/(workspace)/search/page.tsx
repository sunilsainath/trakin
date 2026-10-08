'use client'

import * as React from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Search as SearchIcon } from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import { useCompanyQuery } from '@/components/query'
import type { Page as PageEnvelope, SearchHit } from '@/lib/types'
import { Badge, Button, EmptyState, Input } from '@/components/ui'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock } from '@/components/query'

const ENTITY_TYPES = [
  'PERSON',
  'COMPANY',
  'PROJECT',
  'SOW',
  'CONTRACT',
  'INVOICE',
  'DOCUMENT',
  'POST',
] as const

/**
 * Global search across everything the caller may read.
 *
 * The API filters by permission inside SQL before ranking, so a hit the caller
 * cannot open is never returned and then hidden — filtering on the client would
 * still disclose that it exists. The term is debounced, and the query is only
 * sent once it is long enough to be meaningful.
 */
export default function SearchPage() {
  return (
    <React.Suspense fallback={<PageShell><LoadingBlock rows={6} /></PageShell>}>
      <Search />
    </React.Suspense>
  )
}

function Search() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { activeCompanyPublicId } = useCompany()

  const initial = searchParams.get('q') ?? ''
  const [term, setTerm] = React.useState(initial)
  const [submitted, setSubmitted] = React.useState(initial)
  const [types, setTypes] = React.useState<string[]>([])
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [history, setHistory] = React.useState<Array<string | null>>([null])

  React.useEffect(() => {
    const fromUrl = searchParams.get('q') ?? ''
    setTerm(fromUrl)
    setSubmitted(fromUrl)
    setCursor(null)
    setHistory([null])
  }, [searchParams])

  const query = useCompanyQuery<PageEnvelope<SearchHit>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['search', submitted, types.join(','), cursor],
    path: '/search',
    queryParams: React.useMemo(() => {
      const params = new URLSearchParams()
      if (submitted) params.set('q', submitted)
      params.set('limit', '20')
      if (types.length > 0) params.set('types', types.join(','))
      if (cursor) params.set('cursor', cursor)
      return `?${params.toString()}`
    }, [submitted, types, cursor]),
    enabled: submitted.trim().length >= 2,
  })

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    const next = term.trim()
    setSubmitted(next)
    setCursor(null)
    setHistory([null])
    // The term is reflected in the URL so a search can be shared or bookmarked.
    router.replace(next ? `/search?q=${encodeURIComponent(next)}` : '/search')
  }

  const toggleType = (type: string) => {
    setTypes((previous) =>
      previous.includes(type)
        ? previous.filter((value) => value !== type)
        : [...previous, type],
    )
    setCursor(null)
    setHistory([null])
  }

  const rows = query.data?.data ?? []

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Search' }]}
          title="Search everything"
          description="People, companies, projects, statements of work, contracts, invoices and documents you are permitted to read."
        />

        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
          <div className="min-w-0 flex-1">
            <label htmlFor="global-search" className="sr-only">
              Search
            </label>
            <Input
              id="global-search"
              type="search"
              value={term}
              onChange={(event) => setTerm(event.target.value)}
              placeholder="Search by name, title or reference"
              className="h-11"
            />
          </div>
          <Button type="submit" size="lg" disabled={term.trim().length < 2}>
            <SearchIcon aria-hidden />
            Search
          </Button>
        </form>

        <div className="flex flex-wrap gap-1.5">
          {ENTITY_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => toggleType(type)}
              aria-pressed={types.includes(type)}
              className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                types.includes(type)
                  ? 'border-primary/40 bg-primary-soft text-primary-strong'
                  : 'border-border text-muted-foreground hover:bg-muted'
              }`}
            >
              {type.toLowerCase()}
            </button>
          ))}
          {types.length > 0 ? (
            <button
              type="button"
              onClick={() => {
                setTypes([])
                setCursor(null)
                setHistory([null])
              }}
              className="rounded-full px-2.5 py-1 text-xs font-medium text-primary hover:underline"
            >
              Clear
            </button>
          ) : null}
        </div>

        {submitted.trim().length < 2 ? (
          <EmptyState
            icon={<SearchIcon aria-hidden />}
            title="Type at least two characters"
            description="Search covers everything in the current company you are allowed to read, plus public people and companies for discovery."
          />
        ) : query.isPending ? (
          <LoadingBlock rows={6} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<SearchIcon aria-hidden />}
            title="Nothing found"
            description={`No record you can read matches “${submitted}”. Try a shorter term, a public id, or clear the type filters.`}
            action={
              types.length > 0 ? (
                <Button variant="outline" onClick={() => setTypes([])}>
                  Clear type filters
                </Button>
              ) : undefined
            }
          />
        ) : (
          <>
            <ul className="divide-y divide-border/60 rounded-lg border border-border bg-surface">
              {rows.map((hit) => (
                <li key={`${hit.entity_type}:${hit.public_id}`}>
                  <Link
                    href={hrefForHit(hit)}
                    className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-primary-soft/40"
                  >
                    <Badge tone="outline" className="shrink-0">
                      {hit.entity_type.toLowerCase()}
                    </Badge>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">{hit.title}</span>
                      <span className="block truncate font-mono text-2xs text-subtle-foreground">
                        {hit.public_id}
                      </span>
                    </span>
                    {hit.subtitle ? (
                      <span className="hidden shrink-0 truncate text-xs text-muted-foreground sm:block">
                        {hit.subtitle}
                      </span>
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>

            <div className="flex items-center justify-between border-t border-border pt-3">
              <p className="text-xs text-muted-foreground">
                {rows.length} {rows.length === 1 ? 'result' : 'results'} on this page
                {query.data?.meta.has_more ? ' · more available' : ''}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={history.length <= 1}
                  onClick={() => {
                    const next = history.slice(0, -1)
                    setHistory(next)
                    setCursor(next[next.length - 1] ?? null)
                  }}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!query.data?.meta.has_more || !query.data.meta.next_cursor}
                  onClick={() => {
                    const next = query.data?.meta.next_cursor ?? null
                    if (!next) return
                    setCursor(next)
                    setHistory((previous) => [...previous, next])
                  }}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </PageShell>
  )
}

/** Map a search hit to the screen that renders it. */
function hrefForHit(hit: SearchHit): string {
  switch (hit.entity_type) {
    case 'PERSON':
      return '/people'
    case 'COMPANY':
      return '/companies'
    case 'PROJECT':
      return `/projects/${hit.public_id}`
    case 'SOW':
      return `/sows/${hit.public_id}`
    case 'CONTRACT':
      return `/contracts/${hit.public_id}`
    case 'INVOICE':
      return `/invoices/${hit.public_id}`
    case 'DOCUMENT':
      return '/documents'
    case 'POST':
      return '/feed'
    default:
      return '/search'
  }
}