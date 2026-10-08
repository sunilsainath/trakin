'use client'

import * as React from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { AlertTriangle, RefreshCw, Lock } from 'lucide-react'

import { api, ApiError } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Alert, Button, Card, CardContent, EmptyState, Skeleton } from '@/components/ui'
import { useCompany } from '@/hooks/use-company'

/* -------------------------------------------------------------------------- */
/* Errors                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * The user-facing text for any error a query or mutation can produce.
 *
 * `ApiError.userMessage` is a curated string chosen per error code, so it is safe
 * to show and never leaks internals. A non-API error has no curated message, so
 * only its own text is used when it is a real `Error`.
 */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage
  if (error instanceof Error && error.message) return error.message
  return 'Something went wrong. Please try again.'
}

/** The request id, shown only when there is one, for support escalation. */
export function errorReference(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null
}

/**
 * An error panel with a retry control.
 *
 * Every failure in the app renders through this so a user is never left looking
 * at a blank region with no explanation and no way forward. `userMessage` carries
 * the copy; the raw server message never reaches the screen.
 */
export function ErrorState({
  error,
  onRetry,
  title = 'We could not load this',
  className,
}: {
  error: unknown
  onRetry?: () => void
  title?: string
  className?: string
}) {
  const reference = errorReference(error)

  return (
    <Alert tone="danger" title={title} icon={<AlertTriangle aria-hidden />} className={className}>
      <p>{errorMessage(error)}</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden />
            Try again
          </Button>
        ) : null}
        {reference ? (
          <span className="font-mono text-2xs text-subtle-foreground">reference: {reference}</span>
        ) : null}
      </div>
    </Alert>
  )
}

/**
 * The state shown when a query is refused because of permissions.
 *
 * Distinct from an error: nothing failed, the caller simply may not read this.
 * It says so, and links to the place where access is managed.
 */
export function PermissionState({
  error,
  className,
}: {
  error: unknown
  className?: string
}) {
  return (
    <EmptyState
      className={className}
      icon={<Lock aria-hidden />}
      title="You do not have access to this"
      description={errorMessage(error)}
    />
  )
}

/** True when the API refused for permission reasons rather than failing. */
export function isPermissionError(error: unknown): boolean {
  return error instanceof ApiError && (error.code === 'PERMISSION_DENIED' || error.code === 'NOT_A_COMPANY_MEMBER')
}

/* -------------------------------------------------------------------------- */
/* Loading                                                                    */
/* -------------------------------------------------------------------------- */

/** A block of skeleton lines sized to the region it replaces. */
export function LoadingBlock({
  rows = 4,
  className,
  label = 'Loading',
}: {
  rows?: number
  className?: string
  label?: string
}) {
  return (
    <div role="status" aria-live="polite" aria-label={label} className={cn('space-y-2', className)}>
      {Array.from({ length: rows }).map((_, index) => (
        <Skeleton key={index} className="h-9 w-full" />
      ))}
    </div>
  )
}

/** A skeleton shaped like a data table, so the layout does not jump on load. */
export function LoadingTable({ rows = 8, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div role="status" aria-live="polite" aria-label="Loading table" className="space-y-2">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex gap-3">
          {Array.from({ length: columns }).map((_, columnIndex) => (
            <Skeleton key={columnIndex} className={cn('h-5', columnIndex === 0 ? 'w-40' : 'flex-1')} />
          ))}
        </div>
      ))}
    </div>
  )
}

/** Skeleton tiles for a metric row. */
export function LoadingMetrics({ count = 4 }: { count?: number }) {
  return (
    <div role="status" aria-live="polite" aria-label="Loading metrics" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {Array.from({ length: count }).map((_, index) => (
        <Card key={index}>
          <CardContent className="space-y-2 pt-5">
            <Skeleton className="h-3 w-20" />
            <Skeleton className="h-6 w-28" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Query boundary                                                             */
/* -------------------------------------------------------------------------- */

export interface QueryBoundaryProps<T> {
  query: Pick<UseQueryResult<T>, 'isPending' | 'isError' | 'error' | 'refetch' | 'data'>
  children: (data: T) => React.ReactNode
  /** Rendered when the query succeeds but has nothing in it. */
  empty?: React.ReactNode
  loading?: React.ReactNode
  /** Defaults to a `PermissionState` for permission failures. */
  className?: string
}

/**
 * Renders loading, error and success in one place.
 *
 * `isPending` rather than `isLoading` is deliberate: `isLoading` is also true
 * while a background refetch has no data yet, which would blank a populated
 * table every time the window regains focus.
 */
export function QueryBoundary<T>({
  query,
  children,
  empty,
  loading,
  className,
}: QueryBoundaryProps<T>) {
  if (query.isPending) {
    return <>{loading ?? <LoadingBlock className={className} />}</>
  }

  if (query.isError) {
    return isPermissionError(query.error) ? (
      <PermissionState error={query.error} className={className} />
    ) : (
      <ErrorState error={query.error} onRetry={() => void query.refetch()} className={className} />
    )
  }

  if (empty) return <>{empty}</>
  return <>{children(query.data as T)}</>
}

/* -------------------------------------------------------------------------- */
/* Company-scoped query helper                                                */
/* -------------------------------------------------------------------------- */

export interface ScopedQueryOptions {
  /** Scopes the cache key to a company so a tenant switch cannot serve stale rows. */
  companyPublicId: string | null
  queryKey: readonly unknown[]
  path: string
  queryParams?: string
  enabled?: boolean
  staleTime?: number
}

/**
 * `useQuery` for a company-scoped GET, with the context header and cache scoping
 * handled once.
 *
 * The query key always begins with the company public id, so switching company
 * produces a different key and cannot render another tenant's rows while the new
 * request is in flight.
 */
export function useCompanyQuery<T>({
  companyPublicId,
  queryKey,
  path,
  queryParams = '',
  enabled = true,
  staleTime,
}: ScopedQueryOptions) {
  // Company-less users are first-class: once the workspace context has
  // resolved, the query fires with whatever context exists (possibly none)
  // and the server answers — data, an empty list, or a typed permission
  // error the page renders. Waiting on `loading` (rather than on a company id)
  // is what keeps skeletons on initial boot instead of error flashes.
  const { loading: companyLoading } = useCompany()
  const ready = enabled && !companyLoading

  return useQuery<T>({
    queryKey: ['company', companyPublicId, ...queryKey],
    queryFn: () =>
      api.get<T>(`${path}${queryParams}`, { companyPublicId, signal: undefined }),
    enabled: ready,
    ...(staleTime === undefined ? {} : { staleTime }),
  })
}