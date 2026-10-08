import { describe, expect, it } from 'vitest'

import {
  formatCurrency,
  formatHours,
  formatPercent,
  initials,
  permissionLabel,
  titleCase,
  truncate,
} from '@/lib/utils'

describe('formatCurrency', () => {
  it('formats a decimal string without losing cents', () => {
    expect(formatCurrency('1234.50')).toBe('$1,234.50')
  })

  it('handles a negative amount as a credit', () => {
    expect(formatCurrency('-200.00')).toBe('-$200.00')
  })

  it('renders zero rather than NaN for an unparseable value', () => {
    expect(formatCurrency('not-a-number')).toBe('$0.00')
  })

  it('supports a non-USD currency', () => {
    expect(formatCurrency('100', 'EUR')).toBe('€100.00')
  })

  it('can show an explicit plus sign', () => {
    expect(formatCurrency('50', 'USD', { showSign: true })).toBe('+$50.00')
  })

  it('compacts large values for metric tiles', () => {
    expect(formatCurrency('1250000', 'USD', { compact: true })).toBe('$1.3M')
  })
})

describe('formatHours', () => {
  it('drops a trailing zero', () => {
    expect(formatHours('8')).toBe('8h')
    expect(formatHours('7.5')).toBe('7.5h')
  })

  it('is defensive about bad input', () => {
    expect(formatHours('')).toBe('0h')
  })
})

describe('formatPercent', () => {
  it('formats a ratio', () => {
    expect(formatPercent(0.856)).toBe('86%')
    expect(formatPercent(0.856, 1)).toBe('85.6%')
  })
})

describe('initials', () => {
  it('takes the first and last name', () => {
    expect(initials('Ada Lovelace')).toBe('AL')
  })

  it('falls back to the first two characters of one word', () => {
    expect(initials('platform')).toBe('PL')
  })

  it('returns a placeholder for an empty name', () => {
    expect(initials('   ')).toBe('?')
  })
})

describe('text helpers', () => {
  it('title-cases snake and kebab input', () => {
    expect(titleCase('PENDING_REVIEW')).toBe('Pending Review')
    expect(titleCase('in-progress')).toBe('In Progress')
  })

  it('labels a permission by its action', () => {
    expect(permissionLabel('billing.invoices:approve')).toBe('Approve')
    expect(permissionLabel('identity.users:view')).toBe('View')
  })

  it('truncates without overflowing', () => {
    expect(truncate('abcdefghij', 5)).toBe('abcd…')
    expect(truncate('abc', 5)).toBe('abc')
  })
})