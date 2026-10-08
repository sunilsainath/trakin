/**
 * Status vocabulary to badge tone.
 *
 * The API sends SNAKE_CASE enums. Every screen needs the same mapping from a
 * status string to a tone, so it lives here as a pure function and is covered by
 * tests: an "OVERDUE" invoice must never render in the same colour as a "DRAFT"
 * one, and that rule cannot be left to whichever page happens to render it.
 *
 * Unknown statuses fall back to `neutral` rather than guessing. A new enum value
 * added by the API should look unstyled, not wrong.
 */

export type BadgeTone =
  | 'neutral'
  | 'primary'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info'
  | 'outline'

/**
 * Statuses that share a meaning across modules.
 *
 * Keys are uppercased. A status that appears in two modules under the same name
 * almost always means the same thing, which is why this is one map rather than
 * one per module.
 */
const TONES: Record<string, BadgeTone> = {
  // projects, SOWs, contracts and generic lifecycle states
  DRAFT: 'neutral',
  PLANNING: 'info',
  ACTIVE: 'success',
  ON_HOLD: 'warning',
  COMPLETED: 'success',
  CANCELLED: 'neutral',
  CLOSED: 'neutral',
  SENT: 'info',
  PENDING: 'warning',
  PENDING_APPROVAL: 'warning',
  UNDER_REVIEW: 'info',
  APPROVED: 'success',
  ACCEPTED: 'success',
  REJECTED: 'danger',
  DECLINED: 'danger',
  EXPIRED: 'warning',
  TERMINATED: 'danger',

  // project roles
  OPEN: 'primary',
  FILLED: 'success',
  ON_LEAVE: 'warning',

  // money
  OVERDUE: 'danger',
  DISPUTED: 'danger',
  PARTIALLY_PAID: 'warning',
  PAID: 'success',
  REFUNDED: 'neutral',
  PARTIALLY_REFUNDED: 'neutral',
  SUBMITTED: 'info',
  SCHEDULED: 'info',
  INITIATED: 'info',
  PROCESSING: 'info',
  FAILED: 'danger',

  // timesheets
  LOCKED: 'neutral',
  REVISED: 'warning',

  // master service agreements
  NO_MSA: 'warning',
  REQUESTED: 'info',
  WITHDRAWN: 'neutral',
  SUPERSEDED: 'neutral',

  // bank reconciliation
  UNMATCHED: 'warning',
  SUGGESTED: 'info',
  MATCHED: 'success',
  PARTIALLY_MATCHED: 'warning',
  IGNORED: 'neutral',
  MATCH: 'success',
  REVIEW: 'warning',

  // company and verification
  VERIFIED: 'success',
  UNVERIFIED: 'warning',
  SUSPENDED: 'danger',
  DEACTIVATED: 'neutral',

  // insight severity
  CRITICAL: 'danger',
  HIGH: 'danger',
  MEDIUM: 'warning',
  LOW: 'info',
}

/** The badge tone for a status string. Every table and detail page uses this. */
export function statusTone(status: string | null | undefined): BadgeTone {
  if (!status) return 'neutral'
  return TONES[status.trim().toUpperCase()] ?? 'neutral'
}

/** `PENDING_APPROVAL` -> `Pending approval`. */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  const spaced = status.replace(/[_-]+/g, ' ').trim().toLowerCase()
  if (!spaced) return '—'
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

/** True when a status is one the platform treats as settled and read-only. */
export function isTerminalStatus(status: string): boolean {
  return ['CANCELLED', 'CLOSED', 'TERMINATED', 'REJECTED', 'DECLINED'].includes(
    status.trim().toUpperCase(),
  )
}