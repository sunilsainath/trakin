'use client'

import * as React from 'react'

import { buildQuery, type QueryParams } from '@/lib/query'
import type { PageMeta } from '@/lib/types'
import { Button } from '@/components/ui'
import { useCompanyQuery } from '@/components/query'

/* -------------------------------------------------------------------------- */
/* Cursor list hook                                                           */
/* -------------------------------------------------------------------------- */

export type FilterValue = string | number | boolean | null

export interface CursorListState<T> {
  query: ReturnType<typeof useCompanyQuery<T>>
  filters: QueryParams
  setFilter: (key: string, value: FilterValue) => void
  clearFilters: () => void
  next: () => void
  previous: () => void
  canGoBack: boolean
  activeFilterCount: number
}

/**
 * The state every cursor-paginated list screen needs.
 *
 * Three things have to stay in step, and each is easy to get wrong on its own:
 *
 *   * the filters and the cursor. A cursor is only meaningful against the filter
 *     set that produced it, so changing any filter resets to the first page.
 *     Forgetting that is how a user ends up on "page 2" of a search they just
 *     changed.
 *   * the cursor and the cache key. The cursor is part of the key, otherwise React
 *     Query serves page 1's rows while page 2 is on screen.
 *   * Previous and the cursor history. The API is keyset-paginated, so there is no
 *     offset to subtract; a history of cursors is the only honest way back, and it
 *     returns the user to exactly the rows they were looking at.
 */
export function useCursorList<T>({
  companyPublicId,
  path,
  queryKey,
  initialFilters,
  limit = 25,
  enabled = true,
}: {
  companyPublicId: string | null
  path: string
  queryKey: readonly unknown[]
  initialFilters?: QueryParams
  limit?: number
  enabled?: boolean
}): CursorListState<T> {
  const [filters, setFilters] = React.useState<QueryParams>(initialFilters ?? {})
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [history, setHistory] = React.useState<Array<string | null>>([null])

  // A stable string, so the effect below runs on content change and not on a new
  // object identity every render.
  const filterKey = JSON.stringify(filters)

  React.useEffect(() => {
    setCursor(null)
    setHistory([null])
  }, [filterKey])

  const queryParams = buildQuery({ ...filters, limit, ...(cursor ? { cursor } : {}) })

  const query = useCompanyQuery<T>({
    companyPublicId,
    queryKey: [...queryKey, cursor],
    path,
    queryParams,
    enabled,
  })

  const setFilter = React.useCallback((key: string, value: FilterValue) => {
    setFilters((previous) => ({ ...previous, [key]: value === '' ? null : value }))
  }, [])

  const clearFilters = React.useCallback(() => setFilters({}), [])

  const next = React.useCallback(() => {
    const nextCursor = (query.data as { meta?: PageMeta } | undefined)?.meta?.next_cursor
    if (!nextCursor) return
    setCursor(nextCursor)
    setHistory((previousRows) => [...previousRows, nextCursor])
  }, [query.data])

  const previous = React.useCallback(() => {
    setHistory((previousRows) => {
      if (previousRows.length <= 1) return previousRows
      const trimmed = previousRows.slice(0, -1)
      setCursor(trimmed[trimmed.length - 1] ?? null)
      return trimmed
    })
  }, [])

  const activeFilterCount = Object.values(filters).filter(
    (value) => value !== null && value !== undefined && value !== '',
  ).length

  return {
    query,
    filters,
    setFilter,
    clearFilters,
    next,
    previous,
    canGoBack: history.length > 1,
    activeFilterCount,
  }
}

/* -------------------------------------------------------------------------- */
/* Pagination footer                                                          */
/* -------------------------------------------------------------------------- */

/** The Next / Previous row beneath a table. */
export function CursorFooter({
  meta,
  count,
  onNext,
  onPrevious,
  canGoBack,
  busy,
  noun = 'records',
}: {
  meta: PageMeta | undefined
  count: number
  onNext: () => void
  onPrevious: () => void
  canGoBack: boolean
  busy?: boolean
  noun?: string
}) {
  const hasMore = Boolean(meta?.has_more)
  const nextCursor = meta?.next_cursor ?? null

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
      <p className="text-xs text-muted-foreground">
        {count} {count === 1 ? singular(noun) : noun} on this page
        {hasMore ? ' · more available' : ''}
      </p>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onPrevious} disabled={!canGoBack || busy}>
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onNext}
          disabled={!hasMore || !nextCursor || busy}
        >
          Next
        </Button>
      </div>
    </div>
  )
}

function singular(noun: string): string {
  return noun.endsWith('s') ? noun.slice(0, -1) : noun
}

/** The Next / Previous row for endpoints that use `limit`/`offset`. */
export function OffsetFooter({
  limit,
  offset,
  meta,
  onOffsetChange,
  busy,
}: {
  limit: number
  offset: number
  meta: PageMeta | undefined
  onOffsetChange: (offset: number) => void
  busy?: boolean
}) {
  const hasMore = Boolean(meta?.has_more)
  const total = meta?.total ?? null
  const from = offset + 1
  const to = offset + limit

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
      <p className="text-xs text-muted-foreground">
        {total === null
          ? `Showing ${from}–${to}${hasMore ? '+' : ''}`
          : `Showing ${Math.min(from, total)}–${Math.min(to, total)} of ${total}`}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
          disabled={offset === 0 || busy}
        >
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onOffsetChange(offset + limit)}
          disabled={!hasMore || busy}
        >
          Next
        </Button>
      </div>
    </div>
  )
}