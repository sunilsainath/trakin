import { describe, expect, it } from 'vitest'

import { appendCursor, buildQuery, compareBy, listPath, nextSortDirection } from '@/lib/query'

describe('buildQuery', () => {
  it('returns an empty string when there is nothing to filter on', () => {
    expect(buildQuery({})).toBe('')
  })

  it('omits values that would narrow the result to nothing useful', () => {
    // A cleared filter must not become `q=` on the wire: the API would then look
    // for the literal empty string.
    expect(buildQuery({ q: '', status: null, owner: undefined })).toBe('')
  })

  it('serialises booleans as true and false, not as a bare key', () => {
    expect(buildQuery({ overdue_only: true })).toBe('?overdue_only=true')
    expect(buildQuery({ overdue_only: false })).toBe('?overdue_only=false')
  })

  it('url-encodes values', () => {
    expect(buildQuery({ q: 'Acme & Sons' })).toBe('?q=Acme%20%26%20Sons')
  })

  it('keeps insertion order so the query key is stable', () => {
    expect(buildQuery({ status: 'ACTIVE', limit: 25, q: 'x' })).toBe(
      '?status=ACTIVE&limit=25&q=x',
    )
  })

  it('keeps a zero, which is a real filter value', () => {
    expect(buildQuery({ offset: 0 })).toBe('?offset=0')
  })
})

describe('appendCursor', () => {
  it('adds the cursor to a path with no query', () => {
    expect(appendCursor('/projects', 'abc')).toBe('/projects?cursor=abc')
  })

  it('appends with an ampersand when a query is already present', () => {
    expect(appendCursor('/projects?status=ACTIVE', 'abc')).toBe(
      '/projects?status=ACTIVE&cursor=abc',
    )
  })

  it('leaves the path alone when there is no cursor', () => {
    expect(appendCursor('/projects?status=ACTIVE', null)).toBe('/projects?status=ACTIVE')
    expect(appendCursor('/projects', undefined)).toBe('/projects')
  })

  it('encodes an opaque cursor rather than assuming it is safe', () => {
    expect(appendCursor('/projects', 'a+b/c=')).toBe('/projects?cursor=a%2Bb%2Fc%3D')
  })
})

describe('listPath', () => {
  it('combines filters and a cursor in the right order', () => {
    expect(listPath('/invoices', { status: 'OVERDUE' }, 'next')).toBe(
      '/invoices?status=OVERDUE&cursor=next',
    )
  })

  it('works with filters alone', () => {
    expect(listPath('/invoices', { limit: 25 })).toBe('/invoices?limit=25')
  })
})

describe('compareBy', () => {
  const rows = [
    { name: 'Beta', amount: 10 },
    { name: 'alpha', amount: 2 },
    { name: 'Gamma', amount: null },
  ]

  it('sorts strings case-insensitively and numerically aware', () => {
    expect([...rows].sort(compareBy((row) => row.name)).map((row) => row.name)).toEqual([
      'alpha',
      'Beta',
      'Gamma',
    ])
  })

  it('sorts numbers by value, not lexicographically', () => {
    expect([...rows].sort(compareBy((row) => row.amount)).map((row) => row.amount)).toEqual([
      2, 10, null,
    ])
  })

  it('puts missing values last in both directions', () => {
    // An absent due date must never lead an "oldest first" list.
    expect(
      [...rows].sort(compareBy((row) => row.amount, 'desc')).map((row) => row.name),
    ).toEqual(['Beta', 'alpha', 'Gamma'])
  })

  it('handles null on both sides without throwing', () => {
    expect(compareBy((row: { a: number | null }) => row.a)({ a: null }, { a: null })).toBe(0)
  })
})

describe('nextSortDirection', () => {
  it('cycles ascending, descending and back to unsorted', () => {
    expect(nextSortDirection(null)).toBe('asc')
    expect(nextSortDirection('asc')).toBe('desc')
    expect(nextSortDirection('desc')).toBeNull()
  })
})