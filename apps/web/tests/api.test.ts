import { describe, expect, it } from 'vitest'

import {
  ApiError,
  PUBLIC_ID_PATTERNS,
  createIdempotencyKey,
  isPublicId,
} from '@/lib/api'

describe('isPublicId', () => {
  it('accepts well-formed public ids', () => {
    // Prefix plus exactly eight Crockford base32 characters.
    expect(isPublicId('user', 'U01H8KM2Q')).toBe(true)
    expect(isPublicId('company', 'CO7X9BC4TR')).toBe(true)
  })

  it('rejects an id of the wrong kind', () => {
    expect(isPublicId('company', 'U01H8KM2Q')).toBe(false)
  })

  it('rejects ambiguous characters that Crockford base32 excludes', () => {
    // I, L, O and U are excluded so an id cannot be misread aloud or retyped.
    expect(PUBLIC_ID_PATTERNS.user.test('U01H8KMIQ')).toBe(false)
    expect(PUBLIC_ID_PATTERNS.user.test('U01H8KMOQ')).toBe(false)
    expect(PUBLIC_ID_PATTERNS.user.test('U01H8KMUQ')).toBe(false)
    expect(PUBLIC_ID_PATTERNS.user.test('U01H8KMLQ')).toBe(false)
  })

  it('rejects a lowercase or wrong-length id', () => {
    expect(isPublicId('user', 'u01h8km2qw')).toBe(false)
    expect(isPublicId('user', 'U01H8KM2')).toBe(false)
    expect(isPublicId('user', 'U01H8KM2QWE')).toBe(false)
  })
})

describe('createIdempotencyKey', () => {
  it('is unique across calls', () => {
    const keys = new Set(Array.from({ length: 50 }, () => createIdempotencyKey('invoice')))
    expect(keys.size).toBe(50)
  })

  it('is prefixed and URL-safe', () => {
    const key = createIdempotencyKey('invoice')
    expect(key.startsWith('invoice_')).toBe(true)
    expect(key).toMatch(/^[a-z0-9_]+$/)
  })
})

describe('ApiError', () => {
  it('maps a code to a user-facing message', () => {
    const error = new ApiError('PERMISSION_DENIED', 'raw server text', 403, 'req_1')
    expect(error.userMessage).toBe('You do not have permission to do that.')
  })

  it('includes the request id in the support message', () => {
    const error = new ApiError('INTERNAL_ERROR', 'boom', 500, 'req_abc')
    expect(error.supportMessage).toContain('req_abc')
  })

  it('falls back to the server message for an unknown code', () => {
    const error = new ApiError('SOMETHING_NEW' as never, 'server said this', 400)
    expect(error.userMessage).toBe('server said this')
  })

  it('does not expose the raw server message to a user by default', () => {
    const error = new ApiError('INTERNAL_ERROR', 'connection to db-primary refused', 500)
    expect(error.userMessage).toBe('Something went wrong. Please try again.')
  })
})
