/**
 * Query-string construction for list endpoints.
 *
 * Every list endpoint takes optional filters plus a keyset cursor. Building the
 * string by hand in each page is where the bugs live: a filter that is never
 * cleared, a `false` flag serialised as the string `"false"` (which the API reads
 * as true), and a cursor appended to an already-encoded query.
 *
 * `buildQuery` is pure and tested; `appendCursor` is the only place that knows
 * cursors are appended rather than replaced.
 */

/** A value that may be sent as a query parameter. */
export type QueryValue = string | number | boolean | null | undefined

export type QueryParams = Record<string, QueryValue>

/**
 * Build a `?a=1&b=2` suffix.
 *
 * Rules, each of which exists because the opposite breaks the API:
 *   * `null`, `undefined` and `''` are omitted entirely, so a cleared filter does
 *     not narrow the result set to the literal empty string;
 *   * booleans become `true` / `false`, never `1` / an empty value;
 *   * keys are emitted in insertion order, which keeps URLs stable and
 *     therefore keeps React Query keys stable.
 */
export function buildQuery(params: QueryParams): string {
  const parts: string[] = []

  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue

    if (typeof value === 'boolean') {
      parts.push(`${encodeURIComponent(key)}=${value ? 'true' : 'false'}`)
      continue
    }

    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  }

  return parts.length > 0 ? `?${parts.join('&')}` : ''
}

/**
 * Append the cursor for the next page to an already-built path.
 *
 * Returns the path unchanged when there is no cursor, so callers can pass the
 * result straight into `api.get` without branching. A cursor is a keyset token
 * from `meta.next_cursor`; it is opaque and must never be parsed client-side.
 */
export function appendCursor(path: string, cursor: string | null | undefined): string {
  if (!cursor) return path
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}cursor=${encodeURIComponent(cursor)}`
}

/** Path plus filters plus cursor, the shape `api.get` wants. */
export function listPath(
  basePath: string,
  params: QueryParams = {},
  cursor?: string | null,
): string {
  return appendCursor(`${basePath}${buildQuery(params)}`, cursor)
}

/**
 * Strip a keyset cursor and return the offset it stood for.
 *
 * Cursor pagination is forward-only by design, so "page 1" always means the
 * first page rather than the first twenty rows of the current scroll position.
 * Callers keep their own cursor history for a Back affordance.
 */
export function resetCursor(params: QueryParams): QueryParams {
  const { cursor: _cursor, ...rest } = params as QueryParams & { cursor?: string }
  return rest
}

/** Sort keys sent to the API. Endpoints that do not sort simply ignore this. */
export type SortDirection = 'asc' | 'desc'

/**
 * A client-side comparator for a column.
 *
 * Sorting a fetched page is not the same as sorting on the server: it only
 * reorders the rows currently loaded. It is therefore used only where the API
 * offers no sort parameter, and the control is labelled as sorting the visible
 * rows.
 */
export function compareBy<T>(
  accessor: (row: T) => string | number | null | undefined,
  direction: SortDirection = 'asc',
): (a: T, b: T) => number {
  const sign = direction === 'desc' ? -1 : 1

  return (a, b) => {
    const left = accessor(a)
    const right = accessor(b)

    // Missing values sort last in both directions: an absent due date should
    // never lead an "oldest first" list.
    if (left === null || left === undefined) return right === null || right === undefined ? 0 : 1
    if (right === null || right === undefined) return -1

    if (typeof left === 'number' && typeof right === 'number') {
      return (left - right) * sign
    }

    return String(left).localeCompare(String(right), undefined, { numeric: true }) * sign
  }
}

/** Cycle a column header between ascending, descending and unsorted. */
export function nextSortDirection(
  current: SortDirection | null,
): SortDirection | null {
  if (current === null) return 'asc'
  if (current === 'asc') return 'desc'
  return null
}