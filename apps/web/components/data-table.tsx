'use client'

import * as React from 'react'
import Link from 'next/link'
import { ArrowDown, ArrowUp, ArrowUpDown, Download } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button, Card, EmptyState } from '@/components/ui'
import { compareBy, nextSortDirection, type SortDirection } from '@/lib/query'
import { downloadCsv, toCsv, type CsvColumn } from '@/lib/csv'

/* -------------------------------------------------------------------------- */
/* Public ids                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * A public id, in monospace.
 *
 * Public ids are quoted on the phone and typed by hand, so they are given their
 * own typographic treatment rather than being left as body text. Copy-to-clipboard
 * is offered because retyping `C01H8KM2Q` from a screen is how a payment is
 * applied to the wrong contract.
 */
export function PublicId({
  value,
  href,
  copyable = true,
  className,
}: {
  value: string
  href?: string
  copyable?: boolean
  className?: string
}) {
  const [copied, setCopied] = React.useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard access can be refused by the browser; the id is still visible
      // and selectable, so this is not worth interrupting the user over.
    }
  }

  const content = (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-2xs tracking-tight',
        href && 'transition-colors hover:bg-primary-soft',
        className,
      )}
    >
      {value}
      {copyable ? (
        <button
          type="button"
          onClick={copy}
          aria-label={`Copy ${value}`}
          className="rounded text-subtle-foreground transition-colors hover:text-foreground"
        >
          <span className="sr-only">{copied ? 'Copied' : 'Copy'}</span>
          {copied ? (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden className="size-3">
              <path d="m20 6-11 11-5-5" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden className="size-3 opacity-0 group-hover:opacity-100">
              <rect x="9" y="9" width="11" height="11" rx="2" />
              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
            </svg>
          )}
        </button>
      ) : null}
    </span>
  )

  if (href) {
    return (
      <Link href={href} className="group inline-flex">
        {content}
      </Link>
    )
  }

  return content
}

/* -------------------------------------------------------------------------- */
/* Table chrome                                                               */
/* -------------------------------------------------------------------------- */

export interface Column<T> {
  key: string
  header: React.ReactNode
  /** Cell renderer. */
  cell: (row: T) => React.ReactNode
  /** Right-aligns and applies tabular numerals, for money and counts. */
  numeric?: boolean
  /** Omit on very small screens so a table stays readable while scrolling. */
  hideBelow?: 'sm' | 'md' | 'lg'
  /** Enables sorting on this column. Pass the value used to compare. */
  sortValue?: (row: T) => string | number | null | undefined
  className?: string
}

/**
 * A data table with a header, optional client-side sorting and CSV export.
 *
 * Sorting here is explicitly client-side and applies to the loaded page only. The
 * list endpoints are keyset-paginated and expose no sort parameter, so claiming
 * otherwise would be a lie; the header tooltip says what actually happened.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  emptyState,
  exportName,
  csv,
  sort,
  onSortChange,
  caption,
  className,
}: {
  columns: Column<T>[]
  rows: T[]
  /** Receives the row index as a fallback key when a row has no natural one. */
  rowKey: (row: T, index: number) => string
  onRowClick?: (row: T) => void
  emptyState?: React.ReactNode
  exportName?: string
  csv?: CsvColumn<T>[]
  sort?: { key: string; direction: SortDirection } | null
  onSortChange?: (key: string, direction: SortDirection | null) => void
  caption?: string
  className?: string
}) {
  const visible = React.useMemo(() => {
    if (!sort || !onSortChange) return rows
    const column = columns.find((c) => c.key === sort.key)
    if (!column?.sortValue) return rows
    return [...rows].sort(compareBy<T>(column.sortValue, sort.direction))
  }, [rows, sort, onSortChange, columns])

  const hideClass = (hideBelow?: Column<T>['hideBelow']) =>
    hideBelow === 'sm' ? 'hidden sm:table-cell' : hideBelow === 'md' ? 'hidden md:table-cell' : hideBelow === 'lg' ? 'hidden lg:table-cell' : ''

  return (
    <div className={cn('space-y-3', className)}>
      {exportName && csv ? (
        <div className="flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => downloadCsv(`${exportName}.csv`, toCsv(visible, csv))}
            disabled={visible.length === 0}
          >
            <Download aria-hidden />
            Export this page
          </Button>
        </div>
      ) : null}

      {rows.length === 0 ? (
        emptyState ?? (
          <EmptyState title="Nothing to show" description="There are no records here yet." />
        )
      ) : (
        <div className="overflow-x-auto scrollbar-thin rounded-lg border border-border bg-surface">
          <table className="data-table min-w-full">
            {caption ? <caption className="sr-only">{caption}</caption> : null}
            <thead>
              <tr>
                {columns.map((column) => {
                  const isSorted = sort?.key === column.key
                  // Narrowed to a concrete value so the sort button never has to
                  // handle a null direction.
                  const currentDirection: SortDirection | null = isSorted
                    ? (sort?.direction ?? null)
                    : null
                  const canSort = Boolean(column.sortValue) && onSortChange !== undefined

                  return (
                    <th
                      key={column.key}
                      scope="col"
                      aria-sort={
                        currentDirection === 'asc'
                          ? 'ascending'
                          : currentDirection === 'desc'
                            ? 'descending'
                            : canSort
                              ? 'none'
                              : undefined
                      }
                      className={cn(
                        column.numeric && 'text-right',
                        hideClass(column.hideBelow),
                        column.className,
                      )}
                    >
                      {canSort && onSortChange ? (
                        <button
                          type="button"
                          onClick={() =>
                            onSortChange(
                              column.key,
                              nextSortDirection(currentDirection),
                            )
                          }
                          title="Sorts the rows on this page"
                          className={cn(
                            'inline-flex items-center gap-1 transition-colors hover:text-foreground',
                            isSorted && 'text-primary-strong',
                            column.numeric && 'flex-row-reverse',
                          )}
                        >
                          {column.header}
                          {currentDirection === 'asc' ? (
                            <ArrowUp aria-hidden className="size-3" />
                          ) : currentDirection === 'desc' ? (
                            <ArrowDown aria-hidden className="size-3" />
                          ) : (
                            <ArrowUpDown aria-hidden className="size-3 opacity-40" />
                          )}
                        </button>
                      ) : (
                        column.header
                      )}
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {visible.map((row, rowIndex) => (
                <tr
                  key={rowKey(row, rowIndex)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(onRowClick && 'cursor-pointer')}
                >
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={cn(column.numeric && 'tabular', hideClass(column.hideBelow))}
                    >
                      {column.cell(row)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Pagination                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * Cursor pagination.
 *
 * The API is keyset-paginated, so a "page 2" that skipped records could not be
 * constructed honestly. The caller supplies the cursor from `meta.next_cursor`
 * and keeps its own history; Back therefore returns to exactly the rows that
 * were on screen before, which offset pagination cannot promise.
 */
export function CursorPagination({
  hasMore,
  nextCursor,
  onNext,
  onPrevious,
  canGoBack,
  count,
  busy,
}: {
  hasMore: boolean
  nextCursor: string | null
  onNext: () => void
  onPrevious: () => void
  canGoBack: boolean
  count: number
  busy?: boolean
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
      <p className="text-xs text-muted-foreground">
        {count} {count === 1 ? 'record' : 'records'} on this page
        {hasMore ? ' · more available' : ''}
      </p>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onPrevious} disabled={!canGoBack || busy}>
          Previous
        </Button>
        <Button variant="outline" size="sm" onClick={onNext} disabled={!hasMore || !nextCursor || busy}>
          Next
        </Button>
      </div>
    </div>
  )
}

/** Offset pagination, for the endpoints that use `limit`/`offset`. */
export function OffsetPagination({
  limit,
  offset,
  total,
  hasMore,
  onOffsetChange,
  busy,
}: {
  limit: number
  offset: number
  total: number | null
  hasMore: boolean
  onOffsetChange: (next: number) => void
  busy?: boolean
}) {
  const from = total === null ? offset + 1 : offset + 1
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

/* -------------------------------------------------------------------------- */
/* Toolbar                                                                    */
/* -------------------------------------------------------------------------- */

/** A card that wraps a filter toolbar above a table. */
export function TableCard({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return (
    <Card className={className}>
      <div className="p-4 sm:p-5">{children}</div>
    </Card>
  )
}