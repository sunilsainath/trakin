'use client'

import * as React from 'react'
import Link from 'next/link'
import { ChevronRight, Home } from 'lucide-react'

import { cn } from '@/lib/utils'

export interface Crumb {
  label: string
  /** Omit on the final crumb, which is the current page and must not link. */
  href?: string
  /** Rendered in monospace, for a public id used as the label. */
  mono?: boolean
}

/**
 * Breadcrumb trail.
 *
 * Every detail page has one, because a record is reachable from several lists and
 * without a trail the user cannot get back to the collection they came from. The
 * final crumb is plain text: linking the current page to itself invites a reload
 * that discards unsaved filters.
 */
export function Breadcrumbs({ items, className }: { items: Crumb[]; className?: string }) {
  if (items.length === 0) return null

  return (
    <nav aria-label="Breadcrumb" className={cn('min-w-0', className)}>
      <ol className="flex flex-wrap items-center gap-1 text-sm text-muted-foreground">
        <li className="flex items-center">
          <Link
            href="/dashboard"
            className="flex items-center gap-1 rounded transition-colors hover:text-foreground"
          >
            <Home aria-hidden className="size-3.5" />
            <span className="sr-only sm:not-sr-only">Workspace</span>
          </Link>
        </li>

        {items.map((crumb, index) => {
          const isLast = index === items.length - 1

          return (
            <li key={`${crumb.label}-${index}`} className="flex min-w-0 items-center gap-1">
              <ChevronRight aria-hidden className="size-3.5 shrink-0 text-subtle-foreground" />
              {crumb.href && !isLast ? (
                <Link
                  href={crumb.href}
                  className={cn(
                    'truncate transition-colors hover:text-foreground',
                    crumb.mono && 'font-mono text-xs',
                  )}
                >
                  {crumb.label}
                </Link>
              ) : (
                <span
                  aria-current={isLast ? 'page' : undefined}
                  className={cn(
                    'truncate',
                    isLast && 'font-medium text-foreground',
                    crumb.mono && 'font-mono text-xs',
                  )}
                >
                  {crumb.label}
                </span>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}