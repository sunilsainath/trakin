'use client'

import * as React from 'react'
import { useSearchParams } from 'next/navigation'
import { AlertTriangle, BookOpen, Lightbulb, Send, Sparkles, Zap } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatDateTime } from '@/lib/utils'
import type {
  AiAction,
  AiAutomation,
  AiInsight,
  AiKnowledge,
  AssistantAnswer,
  Page as PageEnvelope,
} from '@/lib/domain-types'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
} from '@/components/ui'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { OffsetFooter } from '@/components/list'
import { FilterInput, FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError } from '@/components/toast'

/**
 * The domain assistant.
 *
 * Answers are grounded in a snapshot of the caller's own data, filtered by their
 * permissions before retrieval, so the assistant cannot surface a record they
 * could not open themselves. The `degraded_reason` is surfaced rather than
 * hidden: if the platform answered without a live model, the user is told.
 */
export function AssistantScreen() {
  return (
    <React.Suspense fallback={<PageShell><LoadingBlock rows={6} /></PageShell>}>
      <Assistant />
    </React.Suspense>
  )
}

function Assistant() {
  const searchParams = useSearchParams()
  const { activeCompanyPublicId, can } = useCompany()
  const [question, setQuestion] = React.useState(searchParams.get('q') ?? '')
  const [answer, setAnswer] = React.useState<AssistantAnswer | null>(null)
  const [history, setHistory] = React.useState<{ question: string; at: string }[]>([])

  const providers = useCompanyQuery<{
    chat: Record<string, { configured: boolean; display_name: string }>
  }>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['ai', 'providers'],
    path: '/ai/providers',
    enabled: can('ai.read'),
  })

  const ask = useCompanyMutation<AssistantAnswer, string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (value) =>
      api.post<AssistantAnswer>(
        '/ai/assistant/domain',
        { question: value },
        { companyPublicId: activeCompanyPublicId },
      ),
    onSuccess: (result) => {
      setAnswer(result)
      setHistory((previous) => [
        { question: result.question, at: new Date().toISOString() },
        ...previous,
      ])
    },
  })

  const configured = Object.values(providers.data?.chat ?? {}).filter(
    (provider) => provider.configured,
  ).length

  const suggested = [
    'What is outstanding and how overdue is it?',
    'Which projects are at risk and why?',
    'Who has unbilled approved time?',
    'Which contracts expire in the next 60 days?',
    'How much did we invoice last month?',
  ]

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'AI Assistant' }]}
          title="Ask about your business"
          description="A question over your own records. The assistant reads only what you are already permitted to read, and cites what it used."
        />

        {!can('ai.assistant') ? (
          <Card>
            <EmptyState
              icon={<Sparkles aria-hidden />}
              title="The assistant is not available to you"
              description="Asking questions requires the ai.assistant permission in this company, and your consent to use the assistant on your account."
            />
          </Card>
        ) : (
          <>
            <Card>
              <CardHeader>
                <CardTitle>Your question</CardTitle>
                <CardDescription>
                  {providers.isPending
                    ? 'Checking which model providers are configured…'
                    : configured > 0
                      ? `${configured} model ${configured === 1 ? 'provider is' : 'providers are'} configured.`
                      : 'No model provider is configured in this environment, so answers will come from the platform fallback and say so.'}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <form
                  onSubmit={async (event) => {
                    event.preventDefault()
                    if (question.trim().length < 3) return
                    try {
                      await ask.mutateAsync(question.trim())
                    } catch (cause) {
                      notifyError(cause, 'The assistant could not answer that.')
                    }
                  }}
                  className="flex flex-col gap-3 sm:flex-row sm:items-end"
                >
                  <div className="min-w-0 flex-1">
                    <label htmlFor="assistant-question" className="mb-1 block text-xs font-medium text-muted-foreground">
                      Question
                    </label>
                    <Input
                      id="assistant-question"
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      placeholder="What is outstanding and how overdue is it?"
                    />
                  </div>
                  <Button type="submit" loading={ask.isPending} disabled={question.trim().length < 3}>
                    <Send aria-hidden />
                    Ask
                  </Button>
                </form>

                <div className="flex flex-wrap gap-1.5">
                  {suggested.map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setQuestion(item)}
                      className="rounded-full border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                    >
                      {item}
                    </button>
                  ))}
                </div>

                {ask.isError ? (
                  <ErrorState error={ask.error} onRetry={() => void ask.reset()} />
                ) : null}

                {answer ? (
                  <div className="space-y-3 rounded-lg border border-border bg-surface-sunken p-4">
                    <p className="text-xs font-medium uppercase tracking-wide text-subtle-foreground">
                      {answer.question}
                    </p>

                    {answer.degraded_reason ? (
                      <div className="flex items-start gap-2 rounded-md bg-warning-soft p-2.5 text-xs text-warning">
                        <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0" />
                        <span>
                          Answered without a live model ({answer.degraded_reason}). Treat it as
                          indicative rather than authoritative.
                        </span>
                      </div>
                    ) : null}

                    <p className="whitespace-pre-wrap text-sm">{answer.answer}</p>

                    {answer.provider ? (
                      <p className="text-2xs text-subtle-foreground">
                        {answer.provider}
                        {answer.context_summary ? ` · ${answer.context_summary}` : ''}
                      </p>
                    ) : null}

                    {answer.citations.length > 0 ? (
                      <div>
                        <p className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
                          Sources
                        </p>
                        <ul className="mt-1 space-y-1">
                          {answer.citations.map((citation, index) => (
                            <li key={index} className="flex items-center gap-2 text-xs">
                              <PublicId value={citation.document_public_id} />
                              <span className="truncate">{citation.title}</span>
                              {citation.page_number ? (
                                <span className="text-subtle-foreground">p{citation.page_number}</span>
                              ) : null}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </CardContent>
            </Card>

            {history.length > 0 ? (
              <Card>
                <CardHeader>
                  <CardTitle>Your questions this session</CardTitle>
                  <CardDescription>
                    Held in this browser only. Nothing is stored on the server by this
                    screen.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <ul className="space-y-1.5">
                    {history.map((item, index) => (
                      <li key={index} className="flex items-center justify-between gap-3 text-sm">
                        <button
                          type="button"
                          onClick={() => setQuestion(item.question)}
                          className="truncate text-left hover:text-primary-strong"
                        >
                          {item.question}
                        </button>
                        <span className="shrink-0 text-2xs text-subtle-foreground">
                          {formatDateTime(item.at)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <AiLink href="/ai/insights" icon={<Lightbulb aria-hidden />} label="Insights" description="Stored findings" permission="ai.insights.read" />
              <AiLink href="/ai/actions" icon={<Zap aria-hidden />} label="Actions" description="Proposals to approve" permission="ai.read" />
              <AiLink href="/ai/automations" icon={<Zap aria-hidden />} label="Automations" description="Scheduled AI runs" permission="ai.read" />
              <AiLink href="/ai/knowledge" icon={<BookOpen aria-hidden />} label="Knowledge" description="Indexed documents" permission="ai.read" />
            </div>
          </>
        )}
      </div>
    </PageShell>
  )
}

function AiLink({
  href,
  icon,
  label,
  description,
  permission,
}: {
  href: string
  icon: React.ReactNode
  label: string
  description: string
  permission: string
}) {
  const { can } = useCompany()
  if (!can(permission)) return null
  return (
    <a
      href={href}
      className="flex items-center gap-3 rounded-lg border border-border bg-surface p-4 transition-colors hover:border-primary/40 hover:bg-primary-soft/40"
    >
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary-soft text-primary-strong [&_svg]:size-4">
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        <span className="block truncate text-xs text-muted-foreground">{description}</span>
      </span>
    </a>
  )
}

/* -------------------------------------------------------------------------- */
/* Insights                                                                   */
/* -------------------------------------------------------------------------- */

/** Offset-paginated wrapper shared by the AI list screens. */
function useOffsetList<T>({
  companyPublicId,
  path,
  queryKey,
  limit = 25,
  extra = {},
}: {
  companyPublicId: string | null
  path: string
  queryKey: readonly unknown[]
  limit?: number
  extra?: Record<string, string | number | boolean | null | undefined>
}) {
  const [offset, setOffset] = React.useState(0)

  const params = React.useMemo(() => {
    const query = new URLSearchParams()
    query.set('limit', String(limit))
    query.set('offset', String(offset))
    for (const [key, value] of Object.entries(extra)) {
      if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
    }
    return `?${query.toString()}`
  }, [extra, limit, offset])

  const query = useCompanyQuery<PageEnvelope<T>>({
    companyPublicId,
    queryKey: [...queryKey, offset, JSON.stringify(extra)],
    path,
    queryParams: params,
  })

  return { query, offset, setOffset, limit }
}

export function InsightsScreen() {
  const { activeCompanyPublicId, can } = useCompany()
  const [entityType, setEntityType] = React.useState('')
  const [severity, setSeverity] = React.useState('')
  const [search, setSearch] = React.useState('')

  const extra = React.useMemo(
    () => ({
      entity_type: entityType || null,
      severity: severity || null,
    }),
    [entityType, severity],
  )

  const { query, offset, setOffset, limit } = useOffsetList<AiInsight>({
    companyPublicId: activeCompanyPublicId,
    path: '/ai/insights',
    queryKey: ['ai', 'insights'],
    extra,
  })

  React.useEffect(() => {
    setOffset(0)
  }, [extra, setOffset])

  if (!can('ai.insights.read')) {
    return (
      <PageShell>
        <Card>
          <EmptyState
            icon={<Lightbulb aria-hidden />}
            title="Insights are not available to you"
            description="Reading stored insights requires ai.insights.read in this company."
          />
        </Card>
      </PageShell>
    )
  }

  const rows = query.data?.data ?? []
  const needle = search.trim().toLowerCase()

  const columns: Column<AiInsight>[] = [
    {
      key: 'title',
      header: 'Insight',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.title}</p>
          <p className="truncate text-xs text-muted-foreground">{row.summary}</p>
        </div>
      ),
    },
    {
      key: 'severity',
      header: 'Severity',
      cell: (row) => <StatusBadge status={row.severity} />,
    },
    { key: 'type', header: 'Type', hideBelow: 'sm', cell: (row) => row.insight_type },
    {
      key: 'entity',
      header: 'Entity',
      hideBelow: 'lg',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate text-muted-foreground">{row.entity_type}</p>
          {row.entity_public_id ? (
            <PublicId value={row.entity_public_id} />
          ) : null}
        </div>
      ),
    },
    {
      key: 'confidence',
      header: 'Confidence',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => (row.confidence ? `${Math.round(Number(row.confidence) * 100)}%` : '—'),
    },
    {
      key: 'created',
      header: 'Created',
      hideBelow: 'sm',
      cell: (row) => formatDateTime(row.created_at),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'AI Insights' }]}
          title="Insights"
          description="Findings the platform has generated from your own records. Each one names the entity it is about so you can check it."
        />

        <div className="flex flex-wrap items-end gap-3">
          <FilterInput
            id="insight-search"
            label="Filter this page"
            value={search}
            onChange={setSearch}
            placeholder="Title or summary"
            className="min-w-56 flex-1"
          />
          <FilterSelect
            id="insight-entity"
            label="Entity type"
            value={entityType}
            onChange={setEntityType}
            options={['PROJECT', 'CONTRACT', 'INVOICE', 'COMPANY', 'MEMBER']}
            className="w-48"
          />
          <FilterSelect
            id="insight-severity"
            label="Severity"
            value={severity}
            onChange={setSeverity}
            options={['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']}
            className="w-44"
          />
        </div>

        {query.isPending ? (
          <LoadingBlock rows={8} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={
                needle
                  ? rows.filter(
                      (row) =>
                        row.title.toLowerCase().includes(needle) ||
                        row.summary.toLowerCase().includes(needle),
                    )
                  : rows
              }
              rowKey={(row) => row.public_id}
              caption="Stored AI insights"
              exportName="ai-insights"
              csv={[
                { header: 'Insight ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Summary', value: (row) => row.summary },
                { header: 'Type', value: (row) => row.insight_type },
                { header: 'Severity', value: (row) => row.severity },
                { header: 'Entity type', value: (row) => row.entity_type },
                { header: 'Entity id', value: (row) => row.entity_public_id },
                { header: 'Confidence', value: (row) => row.confidence },
                { header: 'Created', value: (row) => row.created_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<Lightbulb aria-hidden />}
                  title="No insights yet"
                  description="Insights appear once there is enough activity for the platform to analyse. None are available with these filters."
                />
              }
            />

            <OffsetFooter
              limit={limit}
              offset={offset}
              meta={query.data?.meta}
              onOffsetChange={setOffset}
              busy={query.isFetching}
            />
          </>
        )}
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Actions                                                                    */
/* -------------------------------------------------------------------------- */

/**
 * Proposed AI actions.
 *
 * Nothing here is executable from this screen. An action is a proposal the
 * platform made; approving one is a human decision recorded against the
 * approver, and the server refuses it if the proposer was the approver.
 */
export function ActionsScreen() {
  const { activeCompanyPublicId, can } = useCompany()
  const [status, setStatus] = React.useState('')

  const extra = React.useMemo(() => ({ status: status || null }), [status])

  const { query, offset, setOffset, limit } = useOffsetList<AiAction>({
    companyPublicId: activeCompanyPublicId,
    path: '/ai/actions',
    queryKey: ['ai', 'actions'],
    extra,
  })

  React.useEffect(() => {
    setOffset(0)
  }, [extra, setOffset])

  if (!can('ai.read')) {
    return (
      <PageShell>
        <Card>
          <EmptyState
            icon={<Zap aria-hidden />}
            title="AI actions are not available to you"
            description="Reading proposed actions requires ai.read in this company."
          />
        </Card>
      </PageShell>
    )
  }

  const rows = query.data?.data ?? []

  const columns: Column<AiAction>[] = [
    {
      key: 'action',
      header: 'Proposed action',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.action_type}</p>
          <p className="truncate text-xs text-muted-foreground">{row.rationale ?? 'No rationale given'}</p>
        </div>
      ),
    },
    { key: 'agent', header: 'Agent', hideBelow: 'sm', cell: (row) => row.agent_key },
    {
      key: 'target',
      header: 'Target',
      hideBelow: 'lg',
      cell: (row) => <span className="text-muted-foreground">{row.target_type ?? '—'}</span>,
    },
    { key: 'status', header: 'Status', cell: (row) => <StatusBadge status={row.status} /> },
    {
      key: 'risk',
      header: 'Risk',
      hideBelow: 'md',
      cell: (row) => (row.risk_level ? <Badge tone="outline">{row.risk_level}</Badge> : '—'),
    },
    {
      key: 'permission',
      header: 'Requires',
      hideBelow: 'lg',
      cell: (row) => (
        <span className="font-mono text-2xs">{row.required_permission ?? '—'}</span>
      ),
    },
    {
      key: 'created',
      header: 'Proposed',
      hideBelow: 'sm',
      cell: (row) => formatDateTime(row.created_at),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'AI Actions' }]}
          title="Proposed AI actions"
          description="Actions the platform has proposed. None of them run automatically: each needs a named human to approve it, and the approver cannot be the proposer."
        />

        <div className="w-52">
          <FilterSelect
            id="action-status"
            label="Status"
            value={status}
            onChange={setStatus}
            options={['PROPOSED', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'EXECUTED', 'EXPIRED']}
            className="w-full"
          />
        </div>

        {query.isPending ? (
          <LoadingBlock rows={8} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Proposed AI actions"
              exportName="ai-actions"
              csv={[
                { header: 'Action ID', value: (row) => row.public_id },
                { header: 'Agent', value: (row) => row.agent_key },
                { header: 'Action type', value: (row) => row.action_type },
                { header: 'Target type', value: (row) => row.target_type },
                { header: 'Status', value: (row) => row.status },
                { header: 'Risk level', value: (row) => row.risk_level },
                { header: 'Required permission', value: (row) => row.required_permission },
                { header: 'Rationale', value: (row) => row.rationale },
                { header: 'Created', value: (row) => row.created_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<Zap aria-hidden />}
                  title="No proposed actions"
                  description="Agents propose actions from the assistant or from an automation run. Nothing has been proposed yet."
                />
              }
            />

            <OffsetFooter
              limit={limit}
              offset={offset}
              meta={query.data?.meta}
              onOffsetChange={setOffset}
              busy={query.isFetching}
            />
          </>
        )}
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Automations                                                                */
/* -------------------------------------------------------------------------- */

export function AutomationsScreen() {
  const { activeCompanyPublicId, can } = useCompany()

  const { query, offset, setOffset, limit } = useOffsetList<AiAutomation>({
    companyPublicId: activeCompanyPublicId,
    path: '/ai/automations',
    queryKey: ['ai', 'automations'],
  })

  if (!can('ai.read')) {
    return (
      <PageShell>
        <Card>
          <EmptyState
            icon={<Zap aria-hidden />}
            title="Automations are not available to you"
            description="Reading automations requires ai.read in this company."
          />
        </Card>
      </PageShell>
    )
  }

  const rows = query.data?.data ?? []

  const columns: Column<AiAutomation>[] = [
    {
      key: 'name',
      header: 'Automation',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.name}</p>
          <p className="truncate text-xs text-muted-foreground">{row.description ?? '—'}</p>
        </div>
      ),
    },
    { key: 'trigger', header: 'Trigger', hideBelow: 'sm', cell: (row) => row.trigger_type },
    {
      key: 'schedule',
      header: 'Schedule',
      hideBelow: 'md',
      cell: (row) => (
        <span className="font-mono text-2xs">{row.schedule_cron ?? '—'}</span>
      ),
    },
    {
      key: 'active',
      header: 'Active',
      cell: (row) => (
        <Badge tone={row.is_active ? 'success' : 'neutral'}>{row.is_active ? 'Yes' : 'No'}</Badge>
      ),
    },
    {
      key: 'risk',
      header: 'Risk',
      hideBelow: 'md',
      cell: (row) => (row.risk_level ? <StatusBadge status={row.risk_level} /> : '—'),
    },
    {
      key: 'runs',
      header: 'Runs',
      numeric: true,
      hideBelow: 'sm',
      cell: (row) => row.run_count,
    },
    {
      key: 'last',
      header: 'Last run',
      hideBelow: 'sm',
      cell: (row) => (row.last_run_at ? formatDateTime(row.last_run_at) : 'Never'),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Automations' }]}
          title="AI automations"
          description="Scheduled analysis that runs against your records. Automations propose; they never write without an approval."
        />

        {query.isPending ? (
          <LoadingBlock rows={8} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="AI automations"
              exportName="ai-automations"
              csv={[
                { header: 'Automation ID', value: (row) => row.public_id },
                { header: 'Name', value: (row) => row.name },
                { header: 'Description', value: (row) => row.description },
                { header: 'Trigger', value: (row) => row.trigger_type },
                { header: 'Schedule', value: (row) => row.schedule_cron },
                { header: 'Active', value: (row) => (row.is_active ? 'yes' : 'no') },
                { header: 'Risk level', value: (row) => row.risk_level },
                { header: 'Runs', value: (row) => row.run_count },
                { header: 'Last run', value: (row) => row.last_run_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<Zap aria-hidden />}
                  title="No automations configured"
                  description="An automation runs analysis on a schedule. None has been set up for this company."
                />
              }
            />

            <OffsetFooter
              limit={limit}
              offset={offset}
              meta={query.data?.meta}
              onOffsetChange={setOffset}
              busy={query.isFetching}
            />
          </>
        )}
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Knowledge                                                                  */
/* -------------------------------------------------------------------------- */

export function KnowledgeScreen() {
  const { activeCompanyPublicId, can } = useCompany()
  const [search, setSearch] = React.useState('')

  const extra = React.useMemo(() => ({ q: search || null }), [search])

  const { query, offset, setOffset, limit } = useOffsetList<AiKnowledge>({
    companyPublicId: activeCompanyPublicId,
    path: '/ai/knowledge',
    queryKey: ['ai', 'knowledge'],
    extra,
  })

  React.useEffect(() => {
    setOffset(0)
  }, [extra, setOffset])

  if (!can('ai.read')) {
    return (
      <PageShell>
        <Card>
          <EmptyState
            icon={<BookOpen aria-hidden />}
            title="The knowledge base is not available to you"
            description="Reading indexed documents requires ai.read in this company."
          />
        </Card>
      </PageShell>
    )
  }

  const rows = query.data?.data ?? []

  const columns: Column<AiKnowledge>[] = [
    {
      key: 'title',
      header: 'Document',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.title}</p>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    { key: 'source', header: 'Source', hideBelow: 'sm', cell: (row) => row.source_type },
    { key: 'status', header: 'Status', cell: (row) => <StatusBadge status={row.status} /> },
    {
      key: 'chunks',
      header: 'Chunks',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => row.chunk_count,
    },
    {
      key: 'tokens',
      header: 'Tokens',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => row.token_count.toLocaleString(),
    },
    {
      key: 'updated',
      header: 'Updated',
      hideBelow: 'sm',
      cell: (row) => formatDateTime(row.updated_at),
    },
  ]

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Knowledge base' }]}
          title="Knowledge base"
          description="Documents the assistant can retrieve from. Only documents you are permitted to read are ever retrieved, whatever is indexed here."
        />

        <div className="max-w-md">
          <FilterInput
            id="knowledge-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Title or source type"
            className="w-full"
          />
        </div>

        {query.isPending ? (
          <LoadingBlock rows={8} />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => void query.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Indexed knowledge documents"
              exportName="ai-knowledge"
              csv={[
                { header: 'Document ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Source type', value: (row) => row.source_type },
                { header: 'Status', value: (row) => row.status },
                { header: 'Chunks', value: (row) => row.chunk_count },
                { header: 'Tokens', value: (row) => row.token_count },
                { header: 'Language', value: (row) => row.language },
                { header: 'Updated', value: (row) => row.updated_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<BookOpen aria-hidden />}
                  title="Nothing indexed"
                  description={
                    search
                      ? 'No indexed document matches that search.'
                      : 'Documents are indexed for retrieval once they have been uploaded and processed.'
                  }
                  action={
                    search ? (
                      <Button variant="outline" onClick={() => setSearch('')}>
                        Clear search
                      </Button>
                    ) : undefined
                  }
                />
              }
            />

            <OffsetFooter
              limit={limit}
              offset={offset}
              meta={query.data?.meta}
              onOffsetChange={setOffset}
              busy={query.isFetching}
            />
          </>
        )}
      </div>
    </PageShell>
  )
}