'use client'

import * as React from 'react'

import { cn } from '@/lib/utils'
import { Breadcrumbs, type Crumb } from '@/components/breadcrumbs'

/**
 * The page frame every workspace screen renders inside.
 *
 * The shell already provides the sidebar and the header, so a page only supplies
 * content. Centralising the container here keeps the horizontal padding identical
 * across forty routes, which is otherwise the first thing that drifts.
 */
export function PageShell({
  children,
  className,
  width = 'default',
}: {
  children: React.ReactNode
  className?: string
  width?: 'default' | 'wide'
}) {
  return (
    <div
      className={cn(
        'mx-auto w-full px-4 py-6 sm:px-6 lg:px-8',
        width === 'wide' ? 'max-w-[110rem]' : 'max-w-7xl',
        className,
      )}
    >
      {children}
    </div>
  )
}

/**
 * The header block of a screen: breadcrumb trail, title, description, actions.
 *
 * `actions` holds the primary call to action, so every list screen offers the
 * same "create" affordance in the same place.
 */
export function PageHeader({
  crumbs,
  title,
  description,
  actions,
  meta,
  className,
}: {
  crumbs?: Crumb[]
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
  /** Badges or counters rendered beside the title. */
  meta?: React.ReactNode
  className?: string
}) {
  return (
    <header className={cn('space-y-3', className)}>
      {crumbs && crumbs.length > 0 ? <Breadcrumbs items={crumbs} /> : null}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
            {meta}
          </div>
          {description ? (
            <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  )
}

/** Vertical rhythm between the sections of a page. */
export function PageSections({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('space-y-6', className)}>{children}</div>
}