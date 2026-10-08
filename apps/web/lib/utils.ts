import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** Merge conditional class names, resolving Tailwind conflicts last-wins. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}

/** Currency display. Amounts arrive from the API as exact decimal strings. */
export function formatCurrency(
  amount: string | number,
  currency = 'USD',
  options: { compact?: boolean; showSign?: boolean } = {},
): string {
  const value = typeof amount === 'string' ? Number.parseFloat(amount) : amount
  const safe = Number.isFinite(value) ? value : 0

  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    notation: options.compact ? 'compact' : 'standard',
    maximumFractionDigits: options.compact ? 1 : 2,
    minimumFractionDigits: options.compact ? 0 : 2,
    signDisplay: options.showSign ? 'exceptZero' : 'auto',
  }).format(safe)
}

/** Hours with a single decimal, which is the precision timesheets use. */
export function formatHours(hours: string | number): string {
  const value = typeof hours === 'string' ? Number.parseFloat(hours) : hours
  if (!Number.isFinite(value)) return '0h'
  return `${value % 1 === 0 ? value.toFixed(0) : value.toFixed(1)}h`
}

export function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`
}

export function formatDate(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}

export function formatDateTime(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

/** Relative time for feeds and activity lists. */
export function formatRelative(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return '—'

  const diffMs = Date.now() - date.getTime()
  const minutes = Math.round(diffMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`

  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`

  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d ago`
  return formatDate(date)
}

/** Initials for avatars. Never render a raw email address in an avatar. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return (parts[0] ?? '').slice(0, 2).toUpperCase()
  return `${(parts[0] ?? '')[0] ?? ''}${(parts[1] ?? '')[0] ?? ''}`.toUpperCase()
}

/** Truncate for table cells without breaking layout. */
export function truncate(value: string, max = 60): string {
  if (value.length <= max) return value
  return `${value.slice(0, max - 1)}…`
}

/** Turn SNAKE_CASE or kebab-case into Title Case for display. */
export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

/**
 * `billing.invoices:approve` -> `Approve`.
 *
 * Permission keys are `module.resource:action`, so the action is whatever
 * follows the final colon. Splitting on `.` instead would yield "Invoices:Approve".
 */
export function permissionLabel(key: string): string {
  const afterColon = key.slice(key.lastIndexOf(':') + 1)
  return titleCase(afterColon || key)
}

/** A deterministic tint per entity, so avatars stay stable across renders. */
export function avatarTone(seed: string): 'primary' | 'accent' | 'info' | 'warning' {
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash << 5) - hash + seed.charCodeAt(i)
    hash |= 0
  }
  const tones = ['primary', 'accent', 'info', 'warning'] as const
  return tones[Math.abs(hash) % tones.length] ?? 'primary'
}
