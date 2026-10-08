'use client'

import * as React from 'react'
import * as LabelPrimitive from '@radix-ui/react-label'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/* ------------------------------------------------------------------ Button */

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ' +
    'transition-[background-color,border-color,color,box-shadow] duration-150 ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ' +
    'focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 ' +
    '[&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary:
          'bg-primary text-primary-foreground shadow-sm hover:bg-primary-strong active:bg-primary-strong',
        secondary:
          'bg-secondary text-secondary-foreground border border-border hover:bg-muted',
        outline:
          'border border-border bg-surface text-foreground hover:bg-primary-soft hover:border-primary/40',
        ghost: 'text-muted-foreground hover:bg-muted hover:text-foreground',
        subtle: 'bg-primary-soft text-primary-strong hover:bg-primary-soft/70',
        danger: 'bg-danger text-danger-foreground shadow-sm hover:bg-danger/90',
        success: 'bg-success text-success-foreground shadow-sm hover:bg-success/90',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        xs: 'h-7 px-2 text-xs [&_svg]:size-3.5',
        sm: 'h-8 px-3 text-sm',
        md: 'h-10 px-4',
        lg: 'h-11 px-6 text-[15px]',
        icon: 'h-9 w-9',
        'icon-sm': 'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  loading?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant, size, asChild = false, loading = false, children, disabled, ...props },
  ref,
) {
  const Comp = asChild ? Slot : 'button'
  return (
    <Comp
      ref={ref}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? (
        <>
          <span
            aria-hidden
            className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
          />
          {children}
        </>
      ) : (
        children
      )}
    </Comp>
  )
})
Button.displayName = 'Button'

export { buttonVariants }

/* ------------------------------------------------------------------- Input */

const inputVariants = cva(
  'flex w-full rounded-md border border-input bg-surface px-3 text-sm text-foreground ' +
    'placeholder:text-muted-foreground/80 transition-colors ' +
    'focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring/40 ' +
    'focus-visible:ring-offset-0 ' +
    'disabled:cursor-not-allowed disabled:opacity-60 disabled:bg-muted/50 ' +
    'aria-[invalid=true]:border-danger aria-[invalid=true]:ring-danger/30',
  {
    variants: {
      size: { sm: 'h-8 text-xs', md: 'h-10', lg: 'h-11' },
    },
    defaultVariants: { size: 'md' },
  },
)

/** `size` is omitted from the native attributes because the HTML attribute is
 *  a character count, while here it is a height token. */
export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size'> {
  size?: 'sm' | 'md' | 'lg'
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, size, type = 'text', ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      className={cn(inputVariants({ size }), className)}
      {...props}
    />
  )
})
Input.displayName = 'Input'

/* ---------------------------------------------------------------- Textarea */

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, rows = 4, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(inputVariants(), 'resize-y py-2 leading-relaxed', className)}
      {...props}
    />
  )
})
Textarea.displayName = 'Textarea'

/* -------------------------------------------------------------------- Label */

export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(function Label({ className, ...props }, ref) {
  return (
    <LabelPrimitive.Root
      ref={ref}
      className={cn(
        'text-sm font-medium text-foreground peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
        className,
      )}
      {...props}
    />
  )
})
Label.displayName = 'Label'

/* ------------------------------------------------------------------ Select */

const selectVariants = cva(
  'flex w-full rounded-md border border-input bg-surface px-3 text-sm text-foreground ' +
    'transition-colors focus-visible:border-primary focus-visible:outline-none ' +
    'focus-visible:ring-2 focus-visible:ring-ring/40 ' +
    'disabled:cursor-not-allowed disabled:opacity-60 disabled:bg-muted/50 ' +
    'aria-[invalid=true]:border-danger',
  {
    variants: { size: { sm: 'h-8 text-xs', md: 'h-10' } },
    defaultVariants: { size: 'md' },
  },
)

/** `size` is omitted from the native attributes because the HTML attribute is the
 *  number of visible rows, while here it is a height token. */
export interface SelectProps
  extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  size?: 'sm' | 'md'
}

/**
 * A native select.
 *
 * Deliberately not a Radix `Select`: this product filters long status
 * enumerations, and a native listbox is keyboard- and screen-reader-correct for
 * that without a portal, and it works on touch.
 */
export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, size = 'md', children, ...props },
  ref,
) {
  return (
    <select ref={ref} className={cn(selectVariants({ size }), className)} {...props}>
      {children}
    </select>
  )
})
Select.displayName = 'Select'

/* ------------------------------------------------------------------- Badge */

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ' +
    'whitespace-nowrap transition-colors',
  {
    variants: {
      tone: {
        neutral: 'bg-muted text-muted-foreground border-border',
        primary: 'bg-primary-soft text-primary-strong border-primary/25',
        success: 'bg-success-soft text-success border-success/25',
        warning: 'bg-warning-soft text-warning border-warning/30',
        danger: 'bg-danger-soft text-danger border-danger/25',
        info: 'bg-info-soft text-info border-info/25',
        outline: 'bg-transparent text-muted-foreground border-border',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />
}

export { badgeVariants }

/* -------------------------------------------------------------------- Card */

export const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean }
>(function Card({ className, interactive = false, ...props }, ref) {
  return (
    <div
      ref={ref}
      className={cn(
        'rounded-lg border border-border bg-surface shadow-card',
        interactive && 'transition-shadow hover:shadow-card-hover',
        className,
      )}
      {...props}
    />
  )
})
Card.displayName = 'Card'

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex flex-col gap-1 p-5 pb-3', className)} {...props} />
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn('text-base font-semibold leading-tight text-foreground', className)}
      {...props}
    />
  )
}

export function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn('text-sm text-muted-foreground', className)} {...props} />
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('p-5 pt-0', className)} {...props} />
}

export function CardFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex items-center gap-2 p-5 pt-0', className)} {...props} />
}

/* ---------------------------------------------------------------- Skeleton */

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div aria-hidden className={cn('skeleton', className)} {...props} />
}

/* ----------------------------------------------------------------- Alert */

const alertVariants = cva('flex gap-3 rounded-lg border p-4 text-sm', {
  variants: {
    tone: {
      info: 'bg-info-soft border-info/30 text-foreground',
      success: 'bg-success-soft border-success/30 text-foreground',
      warning: 'bg-warning-soft border-warning/35 text-foreground',
      danger: 'bg-danger-soft border-danger/30 text-foreground',
    },
  },
  defaultVariants: { tone: 'info' },
})

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof alertVariants> {
  icon?: React.ReactNode
  title?: string
}

export function Alert({ className, tone, icon, title, children, ...props }: AlertProps) {
  return (
    <div role="alert" className={cn(alertVariants({ tone }), className)} {...props}>
      {icon ? <div className="shrink-0 [&_svg]:size-4">{icon}</div> : null}
      <div className="min-w-0 flex-1 space-y-1">
        {title ? <p className="font-semibold leading-tight">{title}</p> : null}
        <div className="leading-relaxed text-foreground/85">{children}</div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- Avatar */

export function Avatar({
  src,
  name,
  size = 'md',
  className,
}: {
  src?: string | null
  name: string
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
}) {
  const label = name.trim().slice(0, 2).toUpperCase() || '?'
  const sizes = {
    xs: 'h-6 w-6 text-2xs',
    sm: 'h-8 w-8 text-xs',
    md: 'h-10 w-10 text-sm',
    lg: 'h-14 w-14 text-base',
  } as const

  if (src) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={src}
        alt=""
        className={cn('shrink-0 rounded-full object-cover', sizes[size], className)}
      />
    )
  }

  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-full bg-primary-soft font-semibold text-primary-strong',
        sizes[size],
        className,
      )}
    >
      {label}
    </span>
  )
}

/* ----------------------------------------------------------------- Dialog */

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  className,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children?: React.ReactNode
  footer?: React.ReactNode
  className?: string
}) {
  const panelRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (!open) return

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onOpenChange(false)
    }
    document.addEventListener('keydown', onKey)

    // Move focus into the dialog so keyboard users are not left behind it.
    const timer = window.setTimeout(() => {
      const focusable = panelRef.current?.querySelector<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      focusable?.focus()
    }, 0)

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    return () => {
      document.removeEventListener('keydown', onKey)
      window.clearTimeout(timer)
      document.body.style.overflow = previousOverflow
    }
  }, [open, onOpenChange])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-foreground/40 backdrop-blur-[2px] animate-fade-in"
        onClick={() => onOpenChange(false)}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'relative z-10 w-full max-w-lg rounded-xl border border-border bg-surface p-5 shadow-popover animate-slide-up',
          className,
        )}
      >
        <div className="mb-4 space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{title}</h2>
          {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
        </div>
        {children}
        {footer ? <div className="mt-5 flex justify-end gap-2">{footer}</div> : null}
      </div>
    </div>
  )
}

/* ----------------------------------------------------------------- Tabs */

export function Tabs({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: { key: string; label: string; badge?: number }[]
  active: string
  onChange: (key: string) => void
  className?: string
}) {
  return (
    <div role="tablist" className={cn('flex gap-1 border-b border-border', className)}>
      {tabs.map((tab) => {
        const isActive = tab.key === active
        return (
          <button
            key={tab.key}
            role="tab"
            type="button"
            aria-selected={isActive}
            onClick={() => onChange(tab.key)}
            className={cn(
              'relative -mb-px flex items-center gap-2 border-b-2 px-3.5 py-2 text-sm font-medium transition-colors',
              isActive
                ? 'border-primary text-primary-strong'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
            {typeof tab.badge === 'number' && tab.badge > 0 ? (
              <span className="rounded-full bg-primary-soft px-1.5 text-2xs font-semibold text-primary-strong">
                {tab.badge > 99 ? '99+' : tab.badge}
              </span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}

/* ------------------------------------------------------------- EmptyState */

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-3 px-6 py-14 text-center', className)}>
      {icon ? (
        <div className="flex h-11 w-11 items-center justify-center rounded-full bg-primary-soft text-primary-strong [&_svg]:size-5">
          {icon}
        </div>
      ) : null}
      <div className="space-y-1">
        <p className="font-medium text-foreground">{title}</p>
        {description ? (
          <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {action}
    </div>
  )
}

/* ------------------------------------------------------------- ProgressBar */

export function ProgressBar({
  value,
  max = 100,
  tone = 'primary',
  label,
}: {
  value: number
  max?: number
  tone?: 'primary' | 'success' | 'warning' | 'danger'
  label?: string
}) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  const tones = {
    primary: 'bg-primary',
    success: 'bg-success',
    warning: 'bg-warning',
    danger: 'bg-danger',
  } as const

  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      <div
        className={cn('h-full rounded-full transition-[width] duration-300', tones[tone])}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}