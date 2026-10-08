'use client'

import * as React from 'react'
import Link from 'next/link'
import { Check, Copy } from 'lucide-react'

import { cn } from '@/lib/utils'
import type { PublicIdKind } from '@/lib/api'
import { isPublicId } from '@/lib/api'

/**
 * A public id, in monospace.
 *
 * Public ids are quoted on the phone and typed by hand, so they get their own
 * typographic treatment rather than being left as body text — that is the reason
 * a `C01H8KM2Q` is never confused with a `CO1H8KM2Q` on a printed order.
 *
 * Copy-to-clipboard is offered because retyping a contract id from a screen is
 * how a payment gets applied to the wrong invoice. It is a button rather than a
 * click handler on the whole element so keyboard and screen-reader users get an
 * announced action.
 */
export function PublicId({
  value,
  kind,
  href,
  copyable = true,
  className,
  size = 'default',
}: {
  value: string | null | undefined
  kind?: PublicIdKind
  href?: string
  copyable?: boolean
  className?: string
  size?: 'default' | 'lg'
}) {
  const [copied, setCopied] = React.useState(false)
  const timer = React.useRef<number | undefined>(undefined)

  React.useEffect(() => () => window.clearTimeout(timer.current), [])

  if (!value) {
    return <span className="font-mono text-2xs text-subtle-foreground">—</span>
  }

  // A value that does not match its expected shape is surfaced rather than
  // trusted: it usually means the client is showing an id of the wrong kind.
  const wellFormed = kind ? isPublicId(kind, value) : true

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // Clipboard access can be refused by the browser. The id is still visible
      // and selectable, so this is not worth interrupting the user over.
    }
  }

  const body = (
    <span
      className={cn(
        'group/id inline-flex items-center gap-1.5 rounded bg-surface-sunken font-mono tracking-tight',
        size === 'lg' ? 'px-2 py-1 text-xs' : 'px-1.5 py-0.5 text-2xs',
        href && 'transition-colors hover:bg-primary-soft',
        !wellFormed && 'ring-1 ring-warning/40',
        className,
      )}
      title={wellFormed ? `Public id ${value}` : `Unexpected id format: ${value}`}
    >
      {value}
      {copyable ? (
        <button
          type="button"
          onClick={(event) => {
            event.preventDefault()
            event.stopPropagation()
            void copy()
          }}
          aria-label={copied ? `${value} copied` : `Copy ${value}`}
          className="rounded text-subtle-foreground transition-colors hover:text-primary"
        >
          {copied ? (
            <Check aria-hidden className="size-3 text-success" />
          ) : (
            <Copy aria-hidden className="size-3 opacity-0 transition-opacity group-hover/id:opacity-100" />
          )}
        </button>
      ) : null}
    </span>
  )

  if (href) {
    return (
      <Link href={href} className="inline-flex">
        {body}
      </Link>
    )
  }

  return body
}

/** A block of labelled id/reference pairs, for a detail sidebar. */
export function IdList({
  items,
  className,
}: {
  items: { label: string; value: string | null | undefined; kind?: PublicIdKind; href?: string }[]
  className?: string
}) {
  return (
    <dl className={cn('space-y-2.5', className)}>
      {items.map((item) => (
        <div key={item.label} className="flex items-center justify-between gap-3">
          <dt className="text-xs text-muted-foreground">{item.label}</dt>
          <dd className="min-w-0 text-right">
            <PublicId value={item.value} kind={item.kind} href={item.href} />
          </dd>
        </div>
      ))}
    </dl>
  )
}