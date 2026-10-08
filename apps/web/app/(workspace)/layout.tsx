'use client'

import * as React from 'react'
import { usePathname } from 'next/navigation'
import { AlertTriangle, Loader2 } from 'lucide-react'

import { AppShell } from '@/components/app-shell'
import { Button, Card } from '@/components/ui'
import { CompanyProvider, useCompany } from '@/hooks/use-company'

/**
 * The gate every authenticated route renders inside.
 *
 * It waits for the company context to resolve before mounting the shell, so
 * no screen can briefly render data scoped to the wrong tenant. Auth and
 * permission failures are handled explicitly instead of showing an empty page.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <CompanyProvider>
      <WorkspaceGate>{children}</WorkspaceGate>
    </CompanyProvider>
  )
}

function WorkspaceGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { loading, error, activeCompany, companies, me } = useCompany()

  // Onboarding is unfinished: send the user there instead of into the app.
  React.useEffect(() => {
    if (!loading && me && !me.onboarding_completed && !pathname.startsWith('/onboarding')) {
      window.location.href = '/onboarding'
    }
  }, [loading, me, pathname])

  // Company-less users are first-class: the network is personal, so they land
  // on the feed instead of a company dashboard that cannot load for them.
  React.useEffect(() => {
    if (!loading && me && me.onboarding_completed && companies.length === 0) {
      if (pathname === '/dashboard' || pathname === '/') {
        window.location.href = '/network'
      }
    }
  }, [loading, me, companies, pathname])

  if (loading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background">
        <div role="status" aria-live="polite" className="flex items-center gap-3 text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin" />
          <span className="text-sm">Loading your workspace…</span>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background p-6">
        <Card className="max-w-md p-6 text-center">
          <span className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-danger-soft text-danger">
            <AlertTriangle aria-hidden className="size-5" />
          </span>
          <h1 className="text-base font-semibold">We could not load your workspace</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">{error.userMessage}</p>
          {error.requestId ? (
            <p className="mt-2 font-mono text-2xs text-subtle-foreground">
              reference: {error.requestId}
            </p>
          ) : null}
          <Button className="mt-4" variant="outline" onClick={() => window.location.reload()}>
            Try again
          </Button>
        </Card>
      </div>
    )
  }

  // Company-less users enter the shell: the network, messages and search are
  // personal. Company modules show their own empty/permission states, and the
  // effect above steers them away from the company dashboard.
  if (!activeCompany && companies.length > 0) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background p-6">
        <Card className="max-w-md p-6 text-center">
          <h1 className="text-base font-semibold">Select a company to continue</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Your memberships loaded, but none is active right now.
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button asChild>
              <a href="/companies">Choose a company</a>
            </Button>
            <Button variant="outline" asChild>
              <a href="/network">Go to the network instead</a>
            </Button>
          </div>
        </Card>
      </div>
    )
  }

  return <AppShell>{children}</AppShell>
}
