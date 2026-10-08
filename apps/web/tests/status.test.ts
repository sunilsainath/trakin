import { describe, expect, it } from 'vitest'

import { isTerminalStatus, statusLabel, statusTone } from '@/lib/status'

describe('statusTone', () => {
  it('gives every status that means the same thing the same tone', () => {
    // A DRAFT contract and a DRAFT SOW must not look different.
    expect(statusTone('DRAFT')).toBe('neutral')
    expect(statusTone('PENDING_APPROVAL')).toBe('warning')
    expect(statusTone('OVERDUE')).toBe('danger')
    expect(statusTone('PAID')).toBe('success')
  })

  it('is case and whitespace insensitive', () => {
    expect(statusTone('active')).toBe(statusTone('ACTIVE'))
    expect(statusTone('  overdue ')).toBe('danger')
  })

  it('falls back to neutral for a status it does not recognise', () => {
    // A new enum value from the API should look unstyled rather than wrong.
    expect(statusTone('SOME_FUTURE_STATE')).toBe('neutral')
    expect(statusTone(null)).toBe('neutral')
    expect(statusTone(undefined)).toBe('neutral')
    expect(statusTone('')).toBe('neutral')
  })

  it('separates a settled invoice from an at-risk one', () => {
    expect(statusTone('OVERDUE')).not.toBe(statusTone('PAID'))
    expect(statusTone('PARTIALLY_PAID')).not.toBe(statusTone('APPROVED'))
    expect(statusTone('CANCELLED')).not.toBe(statusTone('SUSPENDED'))
  })

  it('maps reconciliation states to reviewable tones', () => {
    expect(statusTone('UNMATCHED')).toBe('warning')
    expect(statusTone('SUGGESTED')).toBe('info')
    expect(statusTone('MATCHED')).toBe('success')
  })
})

describe('statusLabel', () => {
  it('turns an enum into readable text', () => {
    expect(statusLabel('PENDING_APPROVAL')).toBe('Pending approval')
    expect(statusLabel('ON_HOLD')).toBe('On hold')
    expect(statusLabel('PARTIALLY_PAID')).toBe('Partially paid')
  })

  it('is defensive about an absent status', () => {
    expect(statusLabel(null)).toBe('—')
    expect(statusLabel(undefined)).toBe('—')
    expect(statusLabel('   ')).toBe('—')
  })
})

describe('isTerminalStatus', () => {
  it('identifies the states the platform treats as settled', () => {
    expect(isTerminalStatus('CANCELLED')).toBe(true)
    expect(isTerminalStatus('closed')).toBe(true)
    expect(isTerminalStatus('TERMINATED')).toBe(true)
  })

  it('does not treat a live state as terminal', () => {
    expect(isTerminalStatus('ACTIVE')).toBe(false)
    expect(isTerminalStatus('OVERDUE')).toBe(false)
  })
})