'use client'

import * as React from 'react'
import { Search, X } from 'lucide-react'

import { cn } from '@/lib/utils'
import { statusLabel } from '@/lib/status'

/* -------------------------------------------------------------------------- */
/* Controls                                                                   */
/* -------------------------------------------------------------------------- */

const controlClass =
  'flex h-10 w-full rounded-md border border-input bg-surface px-3 text-sm text-foreground ' +
  'transition-colors placeholder:text-muted-foreground/80 ' +
  'focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

/** A labelled text or search input. */
export function FilterInput({
  id,
  label,
  value,
  onChange,
  placeholder,
  type = 'search',
  className,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  type?: string
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <div className="relative">
        {type === 'search' ? (
          <Search
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-subtle-foreground"
          />
        ) : null}
        <input
          id={id}
          type={type}
          value={value}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
          className={cn(controlClass, type === 'search' && 'pl-8')}
        />
      </div>
    </div>
  )
}

/**
 * A labelled select, for a status or enum filter.
 *
 * `options` accepts `{ value, label }` pairs; an empty `value` is always offered
 * as "All", so a filter can always be cleared.
 */
export function FilterSelect({
  id,
  label,
  value,
  onChange,
  options,
  allLabel = 'All',
  className,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  options: readonly string[] | readonly { value: string; label: string }[]
  allLabel?: string
  className?: string
}) {
  const normalised = options.map((option) =>
    typeof option === 'string' ? { value: option, label: statusLabel(option) } : option,
  )

  return (
    <div className={cn('min-w-0', className)}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={controlClass}
      >
        <option value="">{allLabel}</option>
        {normalised.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}

/** A labelled checkbox, for boolean filters such as "overdue only". */
export function FilterCheckbox({
  id,
  label,
  checked,
  onChange,
  className,
}: {
  id: string
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  className?: string
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        'flex h-10 cursor-pointer items-center gap-2 text-sm text-foreground',
        className,
      )}
    >
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring/40"
      />
      {label}
    </label>
  )
}

/** A labelled date input, for the billing-period filters. */
export function FilterDate({
  id,
  label,
  value,
  onChange,
  className,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={controlClass}
      />
    </div>
  )
}

/** A labelled number input, for an amount filter. */
export function FilterNumber({
  id,
  label,
  value,
  onChange,
  placeholder,
  step,
  className,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  step?: string
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        value={value}
        step={step ?? '0.01'}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={controlClass}
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Toolbar                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * The filter row above a list.
 *
 * Wrapping and a clear-all button are handled here because a filter that cannot
 * be cleared from the screen is the fastest way to make a list look broken.
 */
export function FilterBar({
  children,
  onClear,
  activeCount,
  className,
}: {
  children: React.ReactNode
  onClear?: () => void
  activeCount?: number
  className?: string
}) {
  return (
    <div className={cn('space-y-2', className)}>
      <div className="flex flex-wrap items-end gap-3">{children}</div>
      {onClear && activeCount && activeCount > 0 ? (
        <button
          type="button"
          onClick={onClear}
          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          <X aria-hidden className="size-3" />
          Clear {activeCount} {activeCount === 1 ? 'filter' : 'filters'}
        </button>
      ) : null}
    </div>
  )
}

/**
 * A search box that only fires after typing stops.
 *
 * Every keystroke would otherwise be a request; a 300 ms debounce is short enough
 * to feel immediate and long enough to keep the request count sane.
 */
export function useDebouncedValue<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value)

  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(timer)
  }, [value, delay])

  return debounced
}

/**
 * Cursor pagination state.
 *
 * The API paginates by keyset, so there is no page number to store: a history of
 * cursors is kept so Previous returns to exactly the rows that were on screen.
 * Changing any filter resets to the first page, because a cursor is only
 * meaningful against the filter that produced it.
 */
export function useCursorPagination(resetKey: string) {
  const [cursor, setCursor] = React.useState<string | null>(null)
  const [history, setHistory] = React.useState<Array<string | null>>([null])

  React.useEffect(() => {
    setCursor(null)
    setHistory([null])
  }, [resetKey])

  const next = React.useCallback((nextCursor: string | null) => {
    setCursor(nextCursor)
    setHistory((previous) => [...previous, nextCursor])
  }, [])

  const previous = React.useCallback(() => {
    setHistory((previous) => {
      if (previous.length <= 1) return previous
      const trimmed = previous.slice(0, -1)
      setCursor(trimmed[trimmed.length - 1] ?? null)
      return trimmed
    })
  }, [])

  return {
    cursor,
    next,
    previous,
    canGoBack: history.length > 1,
    reset: () => {
      setCursor(null)
      setHistory([null])
    },
  }
}

/** Offset pagination state, for the endpoints that use `limit`/`offset`. */
export function useOffsetPagination(resetKey: string, limit: number) {
  const [offset, setOffset] = React.useState(0)

  React.useEffect(() => {
    setOffset(0)
  }, [resetKey])

  return { offset, limit, setOffset }
}