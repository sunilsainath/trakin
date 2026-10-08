/**
 * The single API client.
 *
 * Rules enforced here so no component has to remember them:
 *   * the Supabase access token is attached as a Bearer header
 *   * company context travels in X-Company-Public-Id, never in the URL
 *   * every error is normalised to the documented envelope, so callers never
 *     have to guess at a server's error shape
 *   * nothing is cached client-side for authenticated responses
 */

import { createClient, type SupabaseClient } from '@supabase/supabase-js'

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
const API_PREFIX = '/api/v1'

/* -------------------------------------------------------------------------- */
/* Supabase browser client (anon key only)                                     */
/* -------------------------------------------------------------------------- */

let supabase: SupabaseClient | null = null

export function getSupabase(): SupabaseClient {
  if (supabase) return supabase

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY

  if (!url || !anonKey) {
    throw new Error(
      'Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.',
    )
  }

  supabase = createClient(url, anonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      // Short refresh window; the API still enforces its own session policy.
      flowType: 'pkce',
    },
  })

  return supabase
}

/** The access token for the current session, or null when signed out. */
export async function getAccessToken(): Promise<string | null> {
  try {
    const { data } = await getSupabase().auth.getSession()
    return data.session?.access_token ?? null
  } catch {
    return null
  }
}

/* -------------------------------------------------------------------------- */
/* Errors                                                                     */
/* -------------------------------------------------------------------------- */

export type ApiErrorCode =
  | 'AUTHENTICATION_FAILED'
  | 'INVALID_TOKEN'
  | 'EMAIL_NOT_VERIFIED'
  | 'ACCOUNT_DISABLED'
  | 'PERMISSION_DENIED'
  | 'NOT_A_COMPANY_MEMBER'
  | 'COMPANY_CONTEXT_REQUIRED'
  | 'NOT_FOUND'
  | 'RESOURCE_NOT_FOUND'
  | 'CONFLICT'
  | 'INVALID_STATE_TRANSITION'
  | 'SEGREGATION_OF_DUTIES'
  | 'VALIDATION_ERROR'
  | 'BUSINESS_RULE_VIOLATION'
  | 'ALLOCATION_EXCEEDS_CAPACITY'
  | 'MSA_REQUIRED'
  | 'CONTRACT_NOT_ACTIVE'
  | 'IDEMPOTENCY_KEY_REQUIRED'
  | 'IDEMPOTENCY_KEY_REUSED'
  | 'RATE_LIMIT_EXCEEDED'
  | 'INTEGRATION_NOT_CONFIGURED'
  | 'AI_BUDGET_EXCEEDED'
  | 'AI_ACTION_NOT_APPROVED'
  | 'UPLOAD_REJECTED'
  | 'MALWARE_DETECTED'
  | 'FILE_TOO_LARGE'
  | 'UNSUPPORTED_MEDIA_TYPE'
  | 'INTERNAL_ERROR'
  | 'NETWORK_ERROR'

/** A user-facing message for each code. The server's text is a fallback. */
const MESSAGES: Record<ApiErrorCode, string> = {
  AUTHENTICATION_FAILED: 'Please sign in to continue.',
  INVALID_TOKEN: 'Your session is no longer valid. Please sign in again.',
  EMAIL_NOT_VERIFIED: 'Please verify your email address to continue.',
  ACCOUNT_DISABLED: 'This account is not currently active.',
  PERMISSION_DENIED: 'You do not have permission to do that.',
  NOT_A_COMPANY_MEMBER: 'You are not a member of this company.',
  COMPANY_CONTEXT_REQUIRED: 'Select a company to continue.',
  NOT_FOUND: 'That could not be found.',
  RESOURCE_NOT_FOUND: 'That could not be found.',
  CONFLICT: 'That action conflicts with the current state.',
  INVALID_STATE_TRANSITION: 'That change is not allowed right now.',
  SEGREGATION_OF_DUTIES:
    'Someone else must complete this step, because you initiated it.',
  VALIDATION_ERROR: 'Please check the highlighted fields.',
  BUSINESS_RULE_VIOLATION: 'That would break a business rule.',
  ALLOCATION_EXCEEDS_CAPACITY: 'That allocation exceeds the role capacity.',
  MSA_REQUIRED: 'An active Master Service Agreement is required first.',
  CONTRACT_NOT_ACTIVE: 'This contract is not active yet.',
  IDEMPOTENCY_KEY_REQUIRED: 'This request needs an idempotency key.',
  IDEMPOTENCY_KEY_REUSED: 'This request was already submitted with different data.',
  RATE_LIMIT_EXCEEDED: 'Too many requests. Please wait a moment.',
  INTEGRATION_NOT_CONFIGURED: 'This feature is not available in this environment.',
  AI_BUDGET_EXCEEDED: 'The AI usage budget has been reached.',
  AI_ACTION_NOT_APPROVED: 'This action needs human approval first.',
  UPLOAD_REJECTED: 'That file was rejected.',
  MALWARE_DETECTED: 'That file failed its security scan.',
  FILE_TOO_LARGE: 'That file is too large.',
  UNSUPPORTED_MEDIA_TYPE: 'That file type is not supported.',
  INTERNAL_ERROR: 'Something went wrong. Please try again.',
  NETWORK_ERROR: 'Cannot reach the server. Check your connection.',
}

export class ApiError extends Error {
  readonly code: ApiErrorCode
  readonly status: number
  readonly requestId: string | null
  readonly details: Record<string, unknown> | undefined

  constructor(
    code: ApiErrorCode,
    message: string,
    status: number,
    requestId: string | null = null,
    details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.requestId = requestId
    this.details = details
  }

  /** The message to show a user, with the request id for support. */
  get userMessage(): string {
    return MESSAGES[this.code] ?? this.message
  }

  /** Copy for a support ticket: actionable without leaking internals. */
  get supportMessage(): string {
    return this.requestId
      ? `${this.userMessage} (reference: ${this.requestId})`
      : this.userMessage
  }
}

/* -------------------------------------------------------------------------- */
/* Request helpers                                                            */
/* -------------------------------------------------------------------------- */

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  body?: unknown
  companyPublicId?: string | null
  /** Required for any endpoint that moves money or creates a record. */
  idempotencyKey?: string
  signal?: AbortSignal
  /** Internal request for the auth bootstrap, which runs before a company exists. */
  skipAuth?: boolean
}

async function authHeaders(
  options: RequestOptions,
): Promise<Record<string, string>> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }

  if (!options.skipAuth) {
    const token = await getAccessToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }
  if (options.companyPublicId) {
    headers['X-Company-Public-Id'] = options.companyPublicId
  }
  if (options.idempotencyKey) {
    headers['Idempotency-Key'] = options.idempotencyKey
  }
  return headers
}

async function parseError(response: Response): Promise<ApiError> {
  let code: ApiErrorCode = 'INTERNAL_ERROR'
  let message = 'Something went wrong.'
  let requestId: string | null = response.headers.get('x-request-id')
  let details: Record<string, unknown> | undefined

  try {
    const payload = (await response.json()) as {
      error?: { code?: string; message?: string; request_id?: string; details?: Record<string, unknown> }
    }
    if (payload.error) {
      code = (payload.error.code as ApiErrorCode) ?? code
      message = payload.error.message ?? message
      requestId = payload.error.request_id ?? requestId
      details = payload.error.details
    }
  } catch {
    // A non-JSON body (a proxy error page, for example) keeps the defaults.
  }

  return new ApiError(code, message, response.status, requestId, details)
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const url = `${API_BASE_URL}${API_PREFIX}${path.startsWith('/') ? path : `/${path}`}`

  let response: Response
  try {
    response = await fetch(url, {
      method: options.method ?? 'GET',
      headers: await authHeaders(options),
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      // Authenticated data must never sit in a shared cache.
      cache: 'no-store',
    })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError('NETWORK_ERROR', 'Cannot reach the server.', 0)
  }

  if (!response.ok) {
    throw await parseError(response)
  }

  if (response.status === 204) return undefined as T

  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as T
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: 'PUT', body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    apiRequest<T>(path, { ...options, method: 'DELETE' }),
}

/**
 * A stable idempotency key for a user-initiated action.
 *
 * Generated once per form instance and reused across retries, so a double click
 * or a network retry cannot create two financial records.
 */
export function createIdempotencyKey(prefix = 'op'): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `${prefix}_${random.replace(/-/g, '')}`
}

/** The public id formats the platform issues, used for client-side validation. */
export const PUBLIC_ID_PATTERNS = {
  user: /^U[0-9A-HJKMNP-TV-Z]{8}$/,
  company: /^CO[0-9A-HJKMNP-TV-Z]{8}$/,
  project: /^P[0-9A-HJKMNP-TV-Z]{8}$/,
  role: /^R[0-9A-HJKMNP-TV-Z]{8}$/,
  sow: /^S[0-9A-HJKMNP-TV-Z]{8}$/,
  contract: /^C[0-9A-HJKMNP-TV-Z]{8}$/,
  invoice: /^I[0-9A-HJKMNP-TV-Z]{8}$/,
} as const

export type PublicIdKind = keyof typeof PUBLIC_ID_PATTERNS

export function isPublicId(kind: PublicIdKind, value: string): boolean {
  return PUBLIC_ID_PATTERNS[kind].test(value)
}
