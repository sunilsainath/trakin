'use client'

import * as React from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'

import { AppShell } from '@/components/app-shell'
import { Button, Card } from '@/components/ui'
import { CompanyProvider, useCompany } from '@/hooks/use-company'

/**
 * The shell every authenticated route renders inside.
 *
 * It waits for the session and company context to resolve before mounting the
 * shell, so no screen briefly renders against the wrong tenant. It never
 * requires a company: a signed-in user without one gets the full navigation,
 * and each company-scoped screen shows its own "needs a company" state rather
 * than a wall at the door. Auth failures are surfaced explicitly.
 */
export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <CompanyProvider>
      <WorkspaceGate>{children}</WorkspaceGate>
    </CompanyProvider>
  )
}

function WorkspaceGate({ children }: { children: React.ReactNode }) {
  const { loading, error } = useCompany()

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

  return <AppShell>{children}</AppShell>
}
