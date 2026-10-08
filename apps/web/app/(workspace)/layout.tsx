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

/** Routes that render usefully with no company selected. */
const COMPANYLESS_ROUTES = new Set(['/feed', '/companies'])

function WorkspaceGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { loading, error, activeCompany, companies } = useCompany()

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

  // Company membership is never forced. The feed and the Business module
  // stay open so anyone can enter, connect and found a company; every other
  // company-scoped screen explains itself instead of spinning forever.
  if ((companies.length === 0 || !activeCompany) && !COMPANYLESS_ROUTES.has(pathname)) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background p-6">
        <Card className="max-w-md p-6 text-center">
          <h1 className="text-base font-semibold">You are not part of a company yet</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Not a company owner? No problem — most workspace screens unlock once
            you belong to a company. Start from the feed or head to Business.
          </p>
          <div className="mt-4 flex justify-center gap-2">
            <Button asChild>
              <a href="/feed">Go to feed</a>
            </Button>
            <Button variant="outline" asChild>
              <a href="/companies">Go to Business</a>
            </Button>
          </div>
        </Card>
      </div>
    )
  }

  return <AppShell>{children}</AppShell>
}
