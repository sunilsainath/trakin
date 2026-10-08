import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'MyTrakin — the enterprise operating platform',
  description:
    'Professional networking, contracting, workforce, billing and AI business intelligence behind one authorization model.',
}

const MODULES = [
  {
    key: 'WORK',
    title: 'Work',
    text: 'Contracts, assignments, timesheets with multi-level approvals, and leave — no orphan work, every hour traceable to a contract.',
  },
  {
    key: 'BUSINESS',
    title: 'Business',
    text: 'Companies, roles with granular permissions, verified documents, and MSA-gated business relationships.',
  },
  {
    key: 'CORE',
    title: 'Core',
    text: 'Projects, SOW allocations with capacity guardrails, contracts with acceptance workflows, and an idempotent billing engine.',
  },
  {
    key: 'PAYMENTS',
    title: 'Payments',
    text: 'Bank connections, transaction sync, confidence-scored reconciliation with human confirmation, and partial-payment tracking.',
  },
  {
    key: 'AI',
    title: 'AI',
    text: 'A vendor-neutral gateway, permission-first retrieval, contract and document intelligence, and agents that require human approval.',
  },
]

const ASSURANCES = [
  {
    title: 'Tenant isolation',
    text: 'PostgreSQL Row Level Security on every company table, verified by an automated cross-tenant attack suite.',
  },
  {
    title: 'Real money rules',
    text: 'Server-side decimal arithmetic, derived invoice totals, idempotent billing, and append-only financial ledgers.',
  },
  {
    title: 'Auditable everything',
    text: 'Immutable audit trail with actor, diff, reason, request id and IP on every sensitive operation.',
  },
  {
    title: 'AI that respects permissions',
    text: 'Retrieval is permission-filtered before the model ever sees context. Agents cannot execute without approval.',
  },
]

export default function HomePage() {
  return (
    <main className="min-h-dvh bg-background text-foreground">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <span
            aria-hidden
            className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-2xs font-bold text-primary-foreground"
          >
            MT
          </span>
          <span className="text-sm font-semibold tracking-tight">MyTrakin</span>
          <div className="flex-1" />
          <a href="/login" className="text-sm text-muted-foreground hover:text-foreground">
            Sign in
          </a>
          <a
            href="/signup"
            className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary-strong"
          >
            Sign up
          </a>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-4 pb-14 pt-16 text-center">
        <p className="text-2xs font-semibold uppercase tracking-wider text-primary-strong">
          Professional network · Contracting · Workforce · Finance · AI
        </p>
        <h1 className="mx-auto mt-3 max-w-3xl text-3xl font-semibold tracking-tight sm:text-4xl">
          The enterprise operating platform for teams that build, contract and get paid
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-base text-muted-foreground">
          One authorization model from timesheet to invoice to payment. Supabase Auth,
          PostgreSQL Row Level Security, and an AI gateway that never sees data you may
          not see.
        </p>
        <div className="mt-6 flex items-center justify-center gap-3">
          <a
            href="/signup"
            className="inline-flex h-10 items-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground hover:bg-primary-strong"
          >
            Create account
          </a>
          <a
            href="/login"
            className="inline-flex h-10 items-center rounded-md border border-border bg-surface px-5 text-sm font-medium hover:bg-muted"
          >
            Sign in
          </a>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 pb-14">
        <h2 className="text-lg font-semibold tracking-tight">Five modules, one platform</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {MODULES.map((module) => (
            <article key={module.key} className="rounded-lg border border-border bg-surface p-4">
              <p className="text-2xs font-semibold uppercase tracking-wider text-primary-strong">
                {module.key}
              </p>
              <h3 className="mt-1 text-sm font-semibold">{module.title}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{module.text}</p>
            </article>
          ))}
          <article className="rounded-lg border border-border bg-surface p-4">
            <p className="text-2xs font-semibold uppercase tracking-wider text-primary-strong">
              Network
            </p>
            <h3 className="mt-1 text-sm font-semibold">Professional graph</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Profiles, connections, feed and messaging scoped by privacy and relationship.
            </p>
          </article>
        </div>
      </section>

      <section className="border-y border-border bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-14">
          <h2 className="text-lg font-semibold tracking-tight">Security is the architecture</h2>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {ASSURANCES.map((item) => (
              <article key={item.title} className="rounded-lg border border-border bg-background p-4">
                <h3 className="text-sm font-semibold">{item.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{item.text}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-14 text-center">
        <h2 className="text-lg font-semibold tracking-tight">Start with your work email</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Email verification is required. Google sign-in is supported. Access is limited
          to invited organisations.
        </p>
        <div className="mt-5 flex items-center justify-center gap-3">
          <a
            href="/signup"
            className="inline-flex h-10 items-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground hover:bg-primary-strong"
          >
            Sign up
          </a>
          <a href="/request-access" className="text-sm text-primary hover:underline">
            Request access
          </a>
        </div>
      </section>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-4 py-6 text-2xs text-subtle-foreground sm:flex-row sm:items-center">
          <span className="font-semibold text-foreground">MyTrakin</span>
          <span>Enterprise operating platform.</span>
          <div className="flex-1" />
          <a href="/login" className="hover:text-foreground">
            Sign in
          </a>
          <a href="/request-access" className="hover:text-foreground">
            Contact
          </a>
        </div>
      </footer>
    </main>
  )
}
