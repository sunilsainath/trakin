import type { ReactNode } from 'react'

/** Shared frame for sign-in, sign-up, and password recovery. */
export function AuthShell({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <main className="flex min-h-dvh items-center justify-center bg-background px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <span
            aria-hidden
            className="mb-3 flex h-11 w-11 items-center justify-center rounded-lg bg-primary text-sm font-bold text-primary-foreground"
          >
            MT
          </span>
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
        </div>

        {children}

        {footer ? <div className="mt-6 text-center text-sm">{footer}</div> : null}

        <p className="mt-8 text-center text-2xs text-subtle-foreground">
          Access is limited to invited organisations.
        </p>
      </div>
    </main>
  )
}