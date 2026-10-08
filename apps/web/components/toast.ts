'use client'

import * as React from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/lib/api'
import { errorMessage } from '@/components/query'

/**
 * A stable idempotency key for a money-creating form.
 *
 * Generated once per form instance and reused across retries, so a double click
 * or a network retry cannot create two invoices or two payments. `rotate` is
 * called after a success so the next intentional submission gets a fresh key.
 */
export function useIdempotencyKey(prefix: string): [string, () => void] {
  const [key, setKey] = React.useState(() => createKey(prefix))

  const rotate = React.useCallback(() => setKey(createKey(prefix)), [prefix])

  return [key, rotate]
}

function createKey(prefix: string): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}_${random.replace(/-/g, '')}`
}

/** Success toast. One place, so every mutation announces itself the same way. */
export function notifySuccess(message: string, description?: string): void {
  toast.success(message, description ? { description } : undefined)
}

/**
 * Failure toast.
 *
 * The API's curated `userMessage` is shown rather than the raw server text, so an
 * internal message such as "connection to db-primary refused" can never reach the
 * screen. The request id rides along in the description, which is what a user
 * needs to quote in a support request.
 */
export function notifyError(error: unknown, fallback = 'That did not work.'): void {
  const description = error instanceof ApiError ? error.requestId : null
  toast.error(errorMessage(error) || fallback, {
    description: description ? `reference: ${description}` : undefined,
  })
}

/** A neutral toast, for an action that succeeded but changed nothing. */
export function notifyInfo(message: string, description?: string): void {
  toast.info(message, description ? { description } : undefined)
}