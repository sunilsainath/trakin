'use client'

import * as React from 'react'

import { Badge, ProgressBar } from '@/components/ui'
import { statusLabel, statusTone } from '@/lib/status'
import { formatPercent } from '@/lib/utils'

/**
 * A status rendered as a badge.
 *
 * Every status in the product goes through this so an "OVERDUE" invoice looks the
 * same on the dashboard, in a table and on a detail page. The mapping itself is
 * in `lib/status` and is unit-tested.
 */
export function StatusBadge({
  status,
  className,
}: {
  status: string | null | undefined
  className?: string
}) {
  return (
    <Badge tone={statusTone(status)} className={className}>
      {statusLabel(status)}
    </Badge>
  )
}

/**
 * A project's health score, shown as a bar with its numeric value.
 *
 * The number is always printed as well: a colour alone cannot be read by
 * everyone, and 0-100 is not an intuitive scale without a label.
 */
export function HealthScore({ score }: { score: number | null | undefined }) {
  if (score === null || score === undefined) {
    return <span className="text-sm text-muted-foreground">Not scored</span>
  }

  const tone = score >= 80 ? 'success' : score >= 60 ? 'warning' : 'danger'

  return (
    <div className="min-w-28 space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold tabular">{score}</span>
        <span className="text-2xs text-muted-foreground">/ 100</span>
      </div>
      <ProgressBar value={score} tone={tone} label={`Health score ${score} of 100`} />
    </div>
  )
}

/**
 * Role fill: how many of the required people are allocated.
 *
 * Over-allocation is called out rather than clamped, because a role allocated
 * beyond its requirement is a data-entry mistake worth seeing.
 */
export function RoleFill({
  required,
  allocated,
}: {
  required: number
  allocated: number
}) {
  const over = allocated > required

  return (
    <span className={over ? 'text-xs font-medium text-warning' : 'text-sm'}>
      <span className="tabular">{allocated}</span>
      <span className="text-muted-foreground">/{required}</span>
    </span>
  )
}

/**
 * A percentage rendered with an explicit sign where it helps: rate utilisation
 * and tax both read better as `+12.5%` than `12.5%`.
 */
export function Percent({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return '—'
  return formatPercent(value / 100, 1)
}

/** A small labelled metric, for the tiles at the top of a dashboard. */
export function Metric({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: React.ReactNode
  hint?: React.ReactNode
  tone?: 'default' | 'warning' | 'danger' | 'success'
}) {
  const valueTone =
    tone === 'danger'
      ? 'text-danger'
      : tone === 'warning'
        ? 'text-warning'
        : tone === 'success'
          ? 'text-success'
          : ''

  return (
    <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={`mt-1.5 text-2xl font-semibold tabular ${valueTone}`}>{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}