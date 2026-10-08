import type { Metadata } from 'next'

import { AuthShell } from '@/components/auth-shell'

export const metadata: Metadata = { title: 'Request access' }

export default function RequestAccessPage() {
  return (
    <AuthShell
      title="Request access"
      subtitle="MyTrakin is limited to invited organisations."
    >
      <div className="space-y-4 text-sm text-muted-foreground">
        <p>
          Ask your company administrator for an invitation, or create your own company
          account — company creators become its administrator automatically.
        </p>
        <div className="flex flex-col gap-2">
          <a
            href="/signup"
            className="inline-flex h-10 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary-strong"
          >
            Create account
          </a>
          <a
            href="/login"
            className="inline-flex h-10 items-center justify-center rounded-md border border-border bg-surface px-4 text-sm font-medium text-foreground hover:bg-muted"
          >
            Sign in
          </a>
        </div>
      </div>
    </AuthShell>
  )
}
