'use client'

import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query'

import { ApiError } from '@/lib/api'

/**
 * Company-scoped mutations.
 *
 * Every mutation in the app goes through this so three things cannot drift:
 * the company header travels with the request, a money-creating action carries
 * an idempotency key, and a success invalidates the query keys that actually show
 * the record. `useMutation` from react-query cannot do the header or the key on
 * its own, which is why this exists.
 */
export interface MutationContext {
  companyPublicId: string | null
}

export function useCompanyMutation<TResult, TInput = void>({
  context,
  mutationFn,
  onSuccess,
  invalidate = [],
}: {
  context: MutationContext
  mutationFn: (input: TInput) => Promise<TResult>
  onSuccess?: (result: TResult, input: TInput) => void
  /** Query keys to invalidate, relative to the company scope. */
  invalidate?: readonly unknown[][]
}) {
  const queryClient = useQueryClient()

  return useMutation<TResult, ApiError, TInput>({
    mutationFn: (input) =>
      mutationFn(input).catch((cause) => {
        throw cause instanceof ApiError ? cause : new ApiError('INTERNAL_ERROR', String(cause), 0)
      }),
    onSuccess: async (result, input) => {
      // Invalidate before the toast: the next screen should already be fresh.
      await Promise.all(
        invalidate.map((key) =>
          queryClient.invalidateQueries({
            queryKey: ['company', context.companyPublicId, ...key] as QueryKey,
          }),
        ),
      )
      onSuccess?.(result, input)
    },
  })
}

/**
 * Options for a POST that creates a financial record.
 *
 * `idempotencyKey` must come from `useIdempotencyKey` so it is stable across
 * retries of the same user intent, and rotates only after a success.
 */
export function useMoneyMutation<TResult, TInput = void>({
  context,
  mutationFn,
  idempotencyKey,
  onSuccess,
  invalidate = [],
}: {
  context: MutationContext
  mutationFn: (input: TInput & { idempotencyKey: string }) => Promise<TResult>
  idempotencyKey: string
  onSuccess?: (result: TResult, input: TInput) => void
  invalidate?: readonly unknown[][]
}) {
  const queryClient = useQueryClient()

  return useMutation<TResult, ApiError, TInput>({
    mutationFn: (input) =>
      mutationFn({ ...input, idempotencyKey }).catch((cause) => {
        throw cause instanceof ApiError ? cause : new ApiError('INTERNAL_ERROR', String(cause), 0)
      }),
    onSuccess: async (result, input) => {
      await Promise.all(
        invalidate.map((key) =>
          queryClient.invalidateQueries({
            queryKey: ['company', context.companyPublicId, ...key] as QueryKey,
          }),
        ),
      )
      onSuccess?.(result, input)
    },
  })
}

/**
 * Upload a document.
 *
 * `POST /documents` is multipart, so it cannot go through `api.post`, which
 * always serialises JSON. Auth and the company header are assembled the same way
 * so this path is not a second, weaker client.
 */
export async function uploadDocument({
  companyPublicId,
  file,
  title,
  docType,
  description,
  relatedType,
  relatedPublicId,
  visibility,
  idempotencyKey,
}: {
  companyPublicId: string | null
  file: File
  title: string
  docType: string
  description?: string
  relatedType?: string
  relatedPublicId?: string
  visibility?: string
  idempotencyKey?: string
}): Promise<{ public_id: string }> {
  const { getAccessToken } = await import('@/lib/api')

  const form = new FormData()
  form.append('file', file)
  form.append('title', title)
  form.append('doc_type', docType)
  if (description) form.append('description', description)
  if (relatedType) form.append('related_type', relatedType)
  if (relatedPublicId) form.append('related_public_id', relatedPublicId)
  if (visibility) form.append('visibility', visibility)

  const headers: Record<string, string> = {}
  const token = await getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (companyPublicId) headers['X-Company-Public-Id'] = companyPublicId
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey

  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
  const response = await fetch(`${base}/api/v1/documents`, {
    method: 'POST',
    headers,
    body: form,
    cache: 'no-store',
  })

  if (!response.ok) {
    let code = 'INTERNAL_ERROR'
    let message = 'That upload did not work.'
    let requestId = response.headers.get('x-request-id')
    try {
      const payload = (await response.json()) as {
        error?: { code?: string; message?: string; request_id?: string }
      }
      code = payload.error?.code ?? code
      message = payload.error?.message ?? message
      requestId = payload.error?.request_id ?? requestId
    } catch {
      // A non-JSON error body keeps the defaults.
    }
    throw new ApiError(
      code as never,
      message,
      response.status,
      requestId,
    )
  }

  return (await response.json()) as { public_id: string }
}

/**
 * Upload a founding W-9 before any company exists.
 *
 * Like `uploadDocument` but without a company header: founders have no company
 * yet by definition. Auth and error handling are identical.
 */
export async function uploadFoundingW9(file: File): Promise<{ public_id: string }> {
  const { getAccessToken, ApiError: ApiErrorClass } = await import('@/lib/api')

  const form = new FormData()
  form.append('file', file)

  const headers: Record<string, string> = {}
  const token = await getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
  const response = await fetch(`${base}/api/v1/companies/w9-upload`, {
    method: 'POST',
    headers,
    body: form,
    cache: 'no-store',
  })

  if (!response.ok) {
    let code = 'INTERNAL_ERROR'
    let message = 'That upload did not work.'
    let requestId = response.headers.get('x-request-id')
    try {
      const payload = (await response.json()) as {
        error?: { code?: string; message?: string; request_id?: string }
      }
      code = payload.error?.code ?? code
      message = payload.error?.message ?? message
      requestId = payload.error?.request_id ?? requestId
    } catch {
      // A non-JSON error body keeps the defaults.
    }
    throw new ApiErrorClass(
      code as never,
      message,
      response.status,
      requestId,
    )
  }

  return (await response.json()) as { public_id: string }
}