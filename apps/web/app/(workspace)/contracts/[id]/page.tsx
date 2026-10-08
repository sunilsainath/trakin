'use client'

import * as React from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  Archive,
  Ban,
  Check,
  CheckCircle2,
  FileCheck2,
  History,
  RefreshCw,
  Send,
  ShieldAlert,
  Users,
  X,
} from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate, formatDateTime } from '@/lib/utils'
import type { Contract, VersionRow } from '@/lib/domain-types'
import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  EmptyState,
  Input,
  Skeleton,
  Tabs,
} from '@/components/ui'
import { DateInput } from '@/components/forms'
import { Metric, StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import {
  DecisionDialog,
  DialogField,
  NoteDialog,
  ReasonDialog,
} from '@/components/destructive'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, errorMessage, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

type LifecycleAction =
  | 'submit'
  | 'send'
  | 'accept'
  | 'decline'
  | 'activate'
  | 'terminate'
  | 'close'
  | 'renew'
  | 'approve_step'
  | 'reject_step'

/**
 * One contract.
 *
 * Which lifecycle buttons appear is decided by `allowed_transitions`, the list of
 * target statuses the server says it will accept from here. Deriving that from
 * the status enum on the client would drift from the server's transition table
 * the first time a rule is added, and the result would be a button that always
 * fails.
 */
export default function ContractDetailPage() {
  const params = useParams<{ id: string }>()
  const contractId = params.id
  const { activeCompanyPublicId, can } = useCompany()
  const [tab, setTab] = React.useState('summary')
  const [action, setAction] = React.useState<LifecycleAction | null>(null)
  const [stepNo, setStepNo] = React.useState<number | null>(null)

  const contract = useCompanyQuery<Contract>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['contracts', 'detail', contractId],
    path: `/contracts/${contractId}`,
  })

  const versions = useCompanyQuery<VersionRow[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['contracts', 'versions', contractId],
    path: `/contracts/${contractId}/versions`,
    enabled: tab === 'trail',
  })

  const lifecycle = useCompanyMutation<Contract, { path: string; body: Record<string, unknown> }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ path, body }) =>
      api.post<Contract>(path, body, { companyPublicId: activeCompanyPublicId }),
    invalidate: [
      ['contracts', 'detail', contractId],
      ['contracts', 'list'],
    ],
    onSuccess: () => {
      setAction(null)
      setStepNo(null)
      notifySuccess('Contract updated.')
    },
  })

  const approveStep = useCompanyMutation<Contract, { step: number; decision: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ step, decision }) =>
      api.post<Contract>(
        `/contracts/${contractId}/approvals/${step}?decision=${decision}`,
        {},
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['contracts', 'detail', contractId]],
    onSuccess: () => {
      setStepNo(null)
      notifySuccess('Approval recorded.')
    },
  })

  if (contract.isPending) {
    return (
      <PageShell width="wide">
        <div className="space-y-4">
          <Skeleton className="h-4 w-64" />
          <Skeleton className="h-7 w-96" />
          <LoadingBlock rows={8} />
        </div>
      </PageShell>
    )
  }

  if (contract.isError) {
    return (
      <PageShell>
        <div className="space-y-4">
          <ErrorState error={contract.error} onRetry={() => void contract.refetch()} />
          <Link href="/contracts" className="text-sm font-medium text-primary hover:underline">
            Back to contracts
          </Link>
        </div>
      </PageShell>
    )
  }

  const data = contract.data
  const allowed = new Set(data.allowed_transitions.map((value) => value.toUpperCase()))
  const pendingSteps = data.approval_steps.filter((step) => step.status === 'PENDING')

  const call = async (path: string, body: Record<string, unknown> = {}) => {
    try {
      await lifecycle.mutateAsync({ path, body })
    } catch (cause) {
      notifyError(cause, 'That change could not be applied.')
    }
  }

  const decideStep = async (decision: 'APPROVED' | 'REJECTED') => {
    if (stepNo === null) return
    try {
      await approveStep.mutateAsync({ step: stepNo, decision })
    } catch (cause) {
      notifyError(cause, 'The approval could not be recorded.')
    }
  }

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[
            { label: 'Contracts', href: '/contracts' },
            { label: data.public_id, mono: true },
          ]}
          title={data.title}
          description={data.sow_title ? `Generated from ${data.sow_title}` : undefined}
          meta={<StatusBadge status={data.status} />}
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <PublicId value={data.public_id} kind="contract" size="lg" />

              {allowed.has('SENT') && can('contracts.send') ? (
                <Button size="sm" variant="outline" onClick={() => setAction('send')}>
                  <Send aria-hidden />
                  Send
                </Button>
              ) : null}

              {allowed.has('ACCEPTED') && can('contracts.accept') ? (
                <Button size="sm" variant="success" onClick={() => setAction('accept')}>
                  <Check aria-hidden />
                  Accept
                </Button>
              ) : null}

              {allowed.has('DECLINED') && can('contracts.accept') ? (
                <Button size="sm" variant="outline" onClick={() => setAction('decline')}>
                  <X aria-hidden />
                  Decline
                </Button>
              ) : null}

              {allowed.has('ACTIVE') && can('contracts.approve') ? (
                <Button size="sm" variant="success" onClick={() => setAction('activate')}>
                  <CheckCircle2 aria-hidden />
                  Activate
                </Button>
              ) : null}

              {allowed.has('TERMINATED') && can('contracts.terminate') ? (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-danger hover:bg-danger-soft"
                  onClick={() => setAction('terminate')}
                >
                  <Ban aria-hidden />
                  Terminate
                </Button>
              ) : null}

              {allowed.has('CLOSED') && can('contracts.terminate') ? (
                <Button size="sm" variant="outline" onClick={() => setAction('close')}>
                  <Archive aria-hidden />
                  Close
                </Button>
              ) : null}

              {can('contracts.update') && !data.locked ? (
                <Button size="sm" variant="outline" onClick={() => setAction('renew')}>
                  <RefreshCw aria-hidden />
                  Renew
                </Button>
              ) : null}
            </div>
          }
        />

        {data.locked ? (
          <div className="flex items-start gap-2.5 rounded-lg border border-warning/30 bg-warning-soft p-3 text-sm">
            <ShieldAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
            <p>
              This contract is locked. Its terms are frozen because it has been
              sent or activated, so edits are refused by the server.
            </p>
          </div>
        ) : null}

        {pendingSteps.length > 0 && can('contracts.approve') ? (
          <Card className="border-primary/30">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FileCheck2 aria-hidden className="size-4 text-primary" />
                Waiting on an approval
              </CardTitle>
              <CardDescription>
                You cannot approve a step you initiated. The server refuses it and
                says so rather than silently allowing it.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {pendingSteps.map((step) => (
                  <li
                    key={step.step_no}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium">
                        Step {step.step_no}: {step.name}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {step.required_permission
                          ? `Requires ${step.required_permission}`
                          : 'No specific permission required'}
                      </p>
                    </div>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="success"
                        onClick={() => {
                          setStepNo(step.step_no)
                          setAction('approve_step')
                        }}
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setStepNo(step.step_no)
                          setAction('reject_step')
                        }}
                      >
                        Reject
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ) : null}

        <Tabs
          tabs={[
            { key: 'summary', label: 'Summary' },
            { key: 'roles', label: 'Roles', badge: data.roles.length },
            { key: 'parties', label: 'Parties', badge: data.parties.length },
            { key: 'lines', label: 'Line items', badge: data.line_items.length },
            { key: 'approvals', label: 'Approvals', badge: data.approval_steps.length },
            { key: 'trail', label: 'Versions & trail' },
          ]}
          active={tab}
          onChange={setTab}
          className="overflow-x-auto scrollbar-thin"
        />

        {tab === 'summary' ? <SummaryTab contract={data} /> : null}
        {tab === 'roles' ? <RolesTab contract={data} /> : null}
        {tab === 'parties' ? <PartiesTab contract={data} /> : null}
        {tab === 'lines' ? <LineItemsTab contract={data} /> : null}
        {tab === 'approvals' ? <ApprovalsTab contract={data} /> : null}
        {tab === 'trail' ? <TrailTab contract={data} versions={versions} /> : null}

        {/* ------------------------------------------------------------- */}
        {/* Lifecycle dialogs                                                */}
        {/* ------------------------------------------------------------- */}

<LifecycleDialog
          kind={action}
          onClose={() => setAction(null)}
          busy={lifecycle.isPending}
          error={lifecycle.isError ? lifecycle.error : null}
          contract={data}
          stepNo={stepNo}
          stepBusy={approveStep.isPending}
          onConfirm={call}
          onStep={decideStep}
        />
      </div>
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Summary                                                                    */
/* -------------------------------------------------------------------------- */

function SummaryTab({ contract }: { contract: Contract }) {
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Contract value"
          value={
            contract.contract_value
              ? formatCurrency(contract.contract_value, contract.currency, { compact: true })
              : '—'
          }
        />
        <Metric
          label="Invoiced"
          value={formatCurrency(contract.invoiced_total, contract.currency, { compact: true })}
        />
        <Metric
          label="Outstanding"
          value={formatCurrency(contract.outstanding_total, contract.currency, { compact: true })}
          tone={Number(contract.outstanding_total) > 0 ? 'warning' : undefined}
        />
        <Metric
          label="Risk score"
          value={contract.risk_score ?? '—'}
          hint={contract.risk_score ? 'out of 100' : 'Not scored'}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Commercial terms</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              <Detail label="Status" value={contract.status} />
              <Detail label="Type" value={contract.contract_type} />
              <Detail label="Currency" value={contract.currency} />
              <Detail label="Billing basis" value={contract.billing_basis} />
              <Detail label="Billing frequency" value={contract.billing_frequency} />
              <Detail label="Payment terms" value={`${contract.payment_terms_days} days`} />
              <Detail
                label="Start"
                value={contract.start_date ? formatDate(contract.start_date) : '—'}
              />
              <Detail label="End" value={contract.end_date ? formatDate(contract.end_date) : '—'} />
              <Detail
                label="Auto renew"
                value={
                  contract.auto_renew
                    ? `Yes, ${contract.renewal_notice_days ?? 0} days notice`
                    : 'No'
                }
              />
              <Detail
                label="Termination notice"
                value={
                  contract.termination_notice_days
                    ? `${contract.termination_notice_days} days`
                    : 'None recorded'
                }
              />
              <Detail
                label="Notice period ends"
                value={
                  contract.notice_period_end ? formatDate(contract.notice_period_end) : '—'
                }
              />
              <Detail label="Governing law" value={contract.governing_law ?? '—'} />
              <Detail label="Confidentiality" value={contract.confidentiality_level} />
              <Detail
                label="Timesheets"
                value={contract.requires_timesheets ? 'Required' : 'Not required'}
              />
              <Detail label="Version" value={String(contract.version)} />
            </dl>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Linked records</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <LinkedRecord
                label="Project"
                id={contract.project_id}
                name={contract.project_name}
                href={`/projects/${contract.project_id}`}
                kind="project"
              />
              <LinkedRecord
                label="Statement of work"
                id={contract.sow_id}
                name={contract.sow_title}
                href={`/sows/${contract.sow_id}`}
                kind="sow"
              />
              <LinkedRecord
                label="Counterparty"
                id={contract.counterparty_company_id ?? contract.counterparty_user_id}
                name={
                  contract.counterparty_company_name ?? contract.counterparty_user_name
                }
              />
              <LinkedRecord
                label="Document"
                id={contract.document_id}
                name={null}
                href={contract.document_id ? `/documents/${contract.document_id}` : undefined}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Activity on this contract</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-1.5 text-sm">
                <Row label="Timesheets" value={contract.timesheet_count} />
                <Row label="Invoices" value={contract.invoice_count} />
                <Row label="Assignments" value={contract.assignment_count} />
              </ul>
              <dl className="mt-4 space-y-1.5 text-sm">
                <Detail label="Sent" value={contract.sent_at ? formatDateTime(contract.sent_at) : 'Not sent'} />
                <Detail
                  label="Responded"
                  value={contract.responded_at ? formatDateTime(contract.responded_at) : '—'}
                />
                <Detail
                  label="Activated"
                  value={contract.activated_at ? formatDateTime(contract.activated_at) : '—'}
                />
                <Detail
                  label="Terminated"
                  value={contract.terminated_at ? formatDateTime(contract.terminated_at) : '—'}
                />
              </dl>
              {contract.response_notes ? (
                <p className="mt-3 rounded-md bg-surface-sunken p-2.5 text-sm text-muted-foreground">
                  {contract.response_notes}
                </p>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function RolesTab({ contract }: { contract: Contract }) {
  const columns: Column<Contract['roles'][number]>[] = [
    {
      key: 'role',
      header: 'Project role',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.role_title ?? '—'}</p>
          <PublicId value={row.project_role_id} kind="role" />
        </div>
      ),
    },
    { key: 'quantity', header: 'Quantity', numeric: true, cell: (row) => row.quantity },
    {
      key: 'rate',
      header: 'Rate',
      numeric: true,
      cell: (row) =>
        row.rate ? `${formatCurrency(row.rate, row.currency)} / ${row.rate_type}` : '—',
    },
    {
      key: 'billing',
      header: 'Billing',
      hideBelow: 'md',
      cell: (row) => `${row.billing_basis} · ${row.billing_frequency}`,
    },
    { key: 'terms', header: 'Terms', numeric: true, hideBelow: 'lg', cell: (row) => `${row.payment_terms_days}d` },
    {
      key: 'billed',
      header: 'Billed units',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => row.billed_units,
    },
    {
      key: 'overtime',
      header: 'Overtime',
      hideBelow: 'lg',
      cell: (row) =>
        row.overtime_rule === 'NONE'
          ? 'None'
          : `${row.overtime_rule} (x${row.overtime_rate_multiplier})`,
    },
    {
      key: 'tax',
      header: 'Tax',
      hideBelow: 'lg',
      cell: (row) => `${row.tax_rule}${Number(row.tax_rate) > 0 ? ` ${row.tax_rate}` : ''}`,
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Users aria-hidden className="size-4 text-primary" />
          Contracted roles
        </CardTitle>
        <CardDescription>
          These roles and rates are what the billing engine prices. They come from the
          approved SOW and cannot be overridden by an invoice.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <DataTable
          columns={columns}
          rows={contract.roles}
          rowKey={(row) => row.project_role_id}
          caption="Roles on this contract"
          exportName={`contract-${contract.public_id}-roles`}
          csv={[
            { header: 'Role ID', value: (row) => row.project_role_id },
            { header: 'Role', value: (row) => row.role_title },
            { header: 'Quantity', value: (row) => row.quantity },
            { header: 'Rate', value: (row) => row.rate },
            { header: 'Rate type', value: (row) => row.rate_type },
            { header: 'Currency', value: (row) => row.currency },
            { header: 'Billed units', value: (row) => row.billed_units },
            { header: 'Billing basis', value: (row) => row.billing_basis },
            { header: 'Billing frequency', value: (row) => row.billing_frequency },
            { header: 'Payment terms', value: (row) => row.payment_terms_days },
            { header: 'Overtime rule', value: (row) => row.overtime_rule },
            { header: 'Tax rule', value: (row) => row.tax_rule },
            { header: 'Notes', value: (row) => row.notes },
          ]}
          emptyState={
            <EmptyState
              title="No roles on this contract"
              description="A contract with no priced roles cannot produce a billable invoice."
            />
          }
        />
      </CardContent>
    </Card>
  )
}

/**
 * Parties and line items arrive as free-form projections, so they are rendered
 * from the keys the server sent rather than from an assumed schema.
 */
function PartiesTab({ contract }: { contract: Contract }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Parties</CardTitle>
        <CardDescription>Every company and person bound by this contract.</CardDescription>
      </CardHeader>
      <CardContent>
        {contract.parties.length === 0 ? (
          <EmptyState
            title="No parties recorded"
            description="Parties are added when the contract is created or generated from a SOW."
          />
        ) : (
          <ul className="divide-y divide-border/60">
            {contract.parties.map((party, index) => (
              <li key={index} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {String(party.signatory_name ?? party.name ?? party.party_company_id ?? 'Party')}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {String(party.party_role ?? 'PARTIMARY')}
                    {party.signatory_email ? ` · ${String(party.signatory_email)}` : ''}
                  </p>
                </div>
                <PublicId
                  value={String(party.party_company_id ?? party.party_user_id ?? '')}
                  kind={
                    typeof party.party_company_id === 'string' ? 'company' : 'user'
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function LineItemsTab({ contract }: { contract: Contract }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Line items</CardTitle>
        <CardDescription>
          Fixed, recurring and one-off charges that sit outside the role-based billing.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {contract.line_items.length === 0 ? (
          <EmptyState
            title="No line items"
            description="This contract bills purely on time or usage through its roles."
          />
        ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="data-table min-w-full">
              <caption className="sr-only">Line items on this contract</caption>
              <thead>
                <tr>
                  <th scope="col">Label</th>
                  <th scope="col">Type</th>
                  <th scope="col" className="text-right">
                    Quantity
                  </th>
                  <th scope="col">Unit</th>
                  <th scope="col" className="text-right">
                    Rate
                  </th>
                  <th scope="col" className="text-right">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody>
                {contract.line_items.map((item, index) => {
                  const quantity = Number(item.quantity ?? '0')
                  const unitRate = Number(item.unit_rate ?? '0')
                  const currency = String(item.currency ?? contract.currency)
                  return (
                    <tr key={index}>
                      <td>
                        <span className="font-medium">{String(item.label ?? '—')}</span>
                        {item.description ? (
                          <span className="block text-xs text-muted-foreground">
                            {String(item.description)}
                          </span>
                        ) : null}
                      </td>
                      <td>
                        <span className="text-muted-foreground">{String(item.line_type ?? '—')}</span>
                      </td>
                      <td className="tabular">{quantity}</td>
                      <td>
                        <span className="text-muted-foreground">{String(item.unit ?? '')}</span>
                      </td>
                      <td className="tabular">{formatCurrency(unitRate, currency)}</td>
                      <td className="tabular">
                        {formatCurrency(String(item.total ?? quantity * unitRate), currency)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ApprovalsTab({ contract }: { contract: Contract }) {
  return (
    <div className="space-y-6">
      <TimesheetChainCard contract={contract} />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileCheck2 aria-hidden className="size-4 text-primary" />
            Approval steps
          </CardTitle>
          <CardDescription>
            Each step names the permission it needs. The server refuses a step you
            proposed yourself.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {contract.approval_steps.length === 0 ? (
            <EmptyState
              title="No approval steps"
              description="This contract did not go through a multi-step approval flow."
            />
          ) : (
            <ol className="space-y-3">
              {contract.approval_steps.map((step) => (
                <li
                  key={step.step_no}
                  className="rounded-md border border-border p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium">
                      Step {step.step_no}: {step.name}
                    </p>
                    <StatusBadge status={step.status} />
                  </div>
                  <dl className="mt-2 grid gap-x-6 gap-y-1 text-xs sm:grid-cols-3">
                    <div>
                      <dt className="text-muted-foreground">Permission</dt>
                      <dd className="font-mono">{step.required_permission ?? 'none'}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Approver</dt>
                      <dd className="font-mono">{step.approver_user_id ?? 'unassigned'}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">Decided</dt>
                      <dd>{step.decided_at ? formatDateTime(step.decided_at) : 'pending'}</dd>
                    </div>
                  </dl>
                  {step.notes ? (
                    <p className="mt-2 text-sm text-muted-foreground">{step.notes}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

/**
 * The timesheet approval chain: who reviews submitted sheets, in which
 * order. Editable while the contract is a draft; afterwards the chain is
 * frozen because submitted sheets already materialised their steps from it.
 */
function TimesheetChainCard({ contract }: { contract: Contract }) {
  const { activeCompanyPublicId, can } = useCompany()
  const [userId, setUserId] = React.useState('')
  const [permission, setPermission] = React.useState('timesheets.approve')
  const [dueDays, setDueDays] = React.useState('3')

  const members = useCompanyQuery<
    { user: { public_id: string; display_name: string }; role_name: string }[]
  >({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['company', 'members'],
    path: '/companies/current/members',
    enabled: can('contracts.update') && contract.status === 'DRAFT',
  })

  const steps = contract.timesheet_approval_chain?.steps ?? []
  const editable = can('contracts.update') && contract.status === 'DRAFT'

  const save = useCompanyMutation<unknown, { steps: unknown[] }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.patch(`/contracts/${contract.public_id}`, body, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [['contracts', 'detail', contract.public_id]],
    onSuccess: () => {
      notifySuccess('Approval chain saved.', 'Submitted timesheets will follow these steps in order.')
    },
  })

  const replace = (next: typeof steps) => {
    save
      .mutateAsync({
        steps: next.map((step) => ({
          user_public_id: step.user_public_id,
          required_permission: step.required_permission,
          due_within_days: step.due_within_days,
        })),
      })
      .catch((cause) => notifyError(cause, 'The approval chain could not be saved.'))
  }

  const add = () => {
    if (!userId) return
    const due = Math.min(90, Math.max(1, Number(dueDays) || 3))
    void replace([
      ...steps,
      { user_id: null, user_public_id: userId, company_id: '', required_permission: permission, due_within_days: due },
    ])
    setUserId('')
  }

  const nameFor = (step: (typeof steps)[number]) => {
    const member = (members.data ?? []).find((m) => m.user.public_id === step.user_public_id)
    return member ? `${member.user.display_name} (${step.user_public_id})` : (step.user_public_id ?? 'Anyone with the permission')
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Users aria-hidden className="size-4 text-primary" />
          Timesheet approval chain
        </CardTitle>
        <CardDescription>
          {steps.length === 0
            ? 'No chain configured: submitted sheets fall back to a single approval step.'
            : 'Submitted sheets create one approval per step, in order. A rejection at any step returns the sheet.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {steps.length > 0 ? (
          <ol className="space-y-2">
            {steps.map((step, index) => (
              <li
                key={`${step.user_public_id ?? 'any'}-${index}`}
                className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2 text-sm"
              >
                <span className="min-w-0">
                  <span className="font-medium">Step {index + 1}</span>
                  <span className="text-muted-foreground"> · {nameFor(step)}</span>
                  <span className="block font-mono text-2xs text-subtle-foreground">
                    {step.required_permission} · due in {step.due_within_days}d
                  </span>
                </span>
                {editable ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={save.isPending}
                    onClick={() => replace(steps.filter((_, position) => position !== index))}
                  >
                    Remove
                  </Button>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}

        {editable ? (
          <div className="flex flex-wrap items-end gap-2 border-t border-border/60 pt-3">
            <div className="min-w-40 flex-1">
              <label htmlFor="chain-user" className="mb-1 block text-xs font-medium text-muted-foreground">
                Approver
              </label>
              <select
                id="chain-user"
                value={userId}
                onChange={(event) => setUserId(event.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
              >
                <option value="">Anyone with the permission…</option>
                {(members.data ?? []).map((member) => (
                  <option key={member.user.public_id} value={member.user.public_id}>
                    {member.user.display_name} · {member.role_name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="chain-permission" className="mb-1 block text-xs font-medium text-muted-foreground">
                Permission
              </label>
              <select
                id="chain-permission"
                value={permission}
                onChange={(event) => setPermission(event.target.value)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm"
              >
                <option value="timesheets.approve">timesheets.approve</option>
                <option value="timesheets.lock">timesheets.lock</option>
              </select>
            </div>
            <div>
              <label htmlFor="chain-due" className="mb-1 block text-xs font-medium text-muted-foreground">
                Due (days)
              </label>
              <input
                id="chain-due"
                type="number"
                min={1}
                max={90}
                value={dueDays}
                onChange={(event) => setDueDays(event.target.value)}
                className="h-9 w-20 rounded-md border border-input bg-background px-2 text-sm"
              />
            </div>
            <Button size="sm" disabled={save.isPending} onClick={add}>
              {save.isPending ? 'Saving…' : 'Add step'}
            </Button>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">
            The chain can only be changed while the contract is a draft
            {can('contracts.update') ? '' : ' and by someone holding contracts.update'}.
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function TrailTab({
  contract,
  versions,
}: {
  contract: Contract
  versions: ReturnType<typeof useCompanyQuery<VersionRow[]>>
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History aria-hidden className="size-4 text-primary" />
          Versions and trail
        </CardTitle>
        <CardDescription>
          Every version of this contract, with who changed it and why.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {versions.isPending ? (
          <LoadingBlock rows={4} />
        ) : versions.isError ? (
          <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
        ) : (versions.data ?? []).length === 0 ? (
          <EmptyState title="No versions recorded" description="This contract has not changed since it was created." />
        ) : (
          <ol className="space-y-4">
            {(versions.data ?? []).map((version) => (
              <li key={version.version} className="border-l border-border pl-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs">v{version.version}</span>
                  <StatusBadge status={version.status} />
                  <span className="text-xs text-muted-foreground">
                    {formatDateTime(version.changed_at)}
                  </span>
                </div>
                {version.changed_by_name || version.changed_by ? (
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    by {version.changed_by_name ?? version.changed_by}
                  </p>
                ) : null}
                {version.reason ? <p className="mt-1 text-sm">{version.reason}</p> : null}
              </li>
            ))}
          </ol>
        )}

        <div className="mt-6 border-t border-border pt-4">
          <h3 className="text-sm font-semibold">Contract record</h3>
          <dl className="mt-2 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <Detail label="Created" value={formatDateTime(contract.created_at)} />
            <Detail label="Last updated" value={formatDateTime(contract.updated_at)} />
          </dl>
        </div>
      </CardContent>
    </Card>
  )
}

/* -------------------------------------------------------------------------- */
/* Lifecycle dialogs                                                          */
/* -------------------------------------------------------------------------- */

/**
 * Routes one lifecycle action to the right dialog.
 *
 * Terminate, decline and close are irreversible, so each asks for a reason the
 * audit log keeps. The rest take an optional note.
 */
/**
 * Routes one lifecycle action to the right dialog.
 *
 * Terminate, decline and close are irreversible and recorded in the audit log, so
 * each asks for a reason. The rest take an optional note. Renewal has its own
 * fields because it sets a new term.
 */
function LifecycleDialog({
  kind,
  onClose,
  busy,
  error,
  contract,
  stepNo,
  stepBusy,
  onConfirm,
  onStep,
}: {
  kind: LifecycleAction | null
  onClose: () => void
  busy: boolean
  error: unknown
  contract: Contract
  stepNo: number | null
  stepBusy: boolean
  onConfirm: (path: string, body: Record<string, unknown>) => Promise<void>
  onStep: (decision: 'APPROVED' | 'REJECTED') => Promise<void>
}) {
  const [start, setStart] = React.useState('')
  const [end, setEnd] = React.useState('')
  const [value, setValue] = React.useState('')
  const [renewError, setRenewError] = React.useState<string | null>(null)

  React.useEffect(() => {
    setStart('')
    setEnd('')
    setValue('')
    setRenewError(null)
  }, [kind])

  const close = (open: boolean) => {
    if (!open) onClose()
  }

  if (kind === 'renew') {
    return (
      <RenewDialog
        open
        onOpenChange={close}
        currency={contract.currency}
        busy={busy}
        error={error}
        start={start}
        end={end}
        value={value}
        onStart={setStart}
        onEnd={setEnd}
        onValue={setValue}
        validationError={renewError}
        onConfirm={async () => {
          if (!start || !end) {
            setRenewError('Both a new start date and end date are required.')
            return
          }
          if (end < start) {
            setRenewError('The new end date cannot be before the new start date.')
            return
          }
          await onConfirm(`/contracts/${contract.public_id}/renew`, {
            new_start_date: start,
            new_end_date: end,
            ...(value ? { contract_value: value } : {}),
          })
        }}
      />
    )
  }

  if (kind === 'approve_step' || kind === 'reject_step') {
    return (
      <DecisionDialog
        open
        onOpenChange={close}
        decision={kind === 'approve_step' ? 'APPROVED' : 'REJECTED'}
        title={`${kind === 'approve_step' ? 'Approve' : 'Reject'} approval step ${stepNo ?? ''}`}
        description={
          kind === 'approve_step'
            ? 'Approving moves the contract to its next approval step. It is recorded against your identity and cannot be undone from here.'
            : 'Rejecting stops the approval flow and returns the contract for changes.'
        }
        confirmLabel={kind === 'approve_step' ? 'Approve step' : 'Reject step'}
        busy={stepBusy}
        error={error}
        onConfirm={async () => {
          await onStep(kind === 'approve_step' ? 'APPROVED' : 'REJECTED')
        }}
      />
    )
  }

  const destructive: Partial<
    Record<LifecycleAction, { title: string; description: string; path: string }>
  > = {
    terminate: {
      title: 'Terminate this contract',
      description:
        'Terminating ends the engagement under this contract. The server refuses it while a receivable invoice is still open, so settle or cancel those first.',
      path: `/contracts/${contract.public_id}/terminate`,
    },
    close: {
      title: 'Close this contract',
      description:
        'Closing is final. The contract stays readable for audit but cannot be reactivated, and no further time or invoices can be recorded against it.',
      path: `/contracts/${contract.public_id}/close`,
    },
    decline: {
      title: 'Decline this contract',
      description:
        'Declining records that these terms were refused. It cannot be undone from this screen.',
      path: `/contracts/${contract.public_id}/decline`,
    },
  }

  const reasonConfig = kind ? destructive[kind] : undefined
  if (reasonConfig) {
    return (
      <ReasonDialog
        open
        onOpenChange={close}
        title={reasonConfig.title}
        description={reasonConfig.description}
        confirmLabel={reasonConfig.title.split(' ')[0] ?? 'Confirm'}
        label="Reason"
        busy={busy}
        error={error}
        onConfirm={(reason) => onConfirm(reasonConfig.path, { reason })}
      />
    )
  }

  const simple: Partial<
    Record<LifecycleAction, { title: string; description: string; path: string; confirm: string }>
  > = {
    send: {
      title: 'Send this contract',
      description:
        'Sending records the contract as sent to the counterparty and locks its terms. Any open approval step must be decided first.',
      path: `/contracts/${contract.public_id}/send`,
      confirm: 'Send contract',
    },
    accept: {
      title: 'Accept this contract',
      description:
        'Accepting records agreement. The server requires at least one priced role or a contract value before it will accept.',
      path: `/contracts/${contract.public_id}/accept`,
      confirm: 'Accept contract',
    },
    activate: {
      title: 'Activate this contract',
      description:
        'Activating starts the engagement: people can be assigned, timesheets recorded and billing run.',
      path: `/contracts/${contract.public_id}/activate`,
      confirm: 'Activate contract',
    },
    submit: {
      title: 'Submit for approval',
      description: 'Submitting starts the approval flow for this contract.',
      path: `/contracts/${contract.public_id}/submit`,
      confirm: 'Submit',
    },
  }

  const config = kind ? simple[kind] : undefined
  if (!config) return null

  return (
    <NoteDialog
      open
      onOpenChange={close}
      title={config.title}
      description={config.description}
      label="Note"
      placeholder="Optional"
      confirmLabel={config.confirm}
      busy={busy}
      error={error}
      onConfirm={async (value) => {
        await onConfirm(config.path, value ? { notes: value } : {})
      }}
    />
  )
}

/** The renewal term form: two dates and an optional new value. */
function RenewDialog({
  open,
  onOpenChange,
  currency,
  busy,
  error,
  start,
  end,
  value,
  onStart,
  onEnd,
  onValue,
  validationError,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currency: string
  busy: boolean
  error: unknown
  start: string
  end: string
  value: string
  onStart: (value: string) => void
  onEnd: (value: string) => void
  onValue: (value: string) => void
  validationError: string | null
  onConfirm: () => Promise<void>
}) {
  return (
    <RenewBody
      open={open}
      onOpenChange={onOpenChange}
      currency={currency}
      busy={busy}
      error={error}
      start={start}
      end={end}
      value={value}
      onStart={onStart}
      onEnd={onEnd}
      onValue={onValue}
      validationError={validationError}
      onConfirm={onConfirm}
    />
  )
}

/* -------------------------------------------------------------------------- */
/* Small presentational helpers                                               */
/* -------------------------------------------------------------------------- */

/** The renewal term form. Two dates and an optional new contract value. */
function RenewBody({
  open,
  onOpenChange,
  currency,
  busy,
  error,
  start,
  end,
  value,
  onStart,
  onEnd,
  onValue,
  validationError,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currency: string
  busy: boolean
  error: unknown
  start: string
  end: string
  value: string
  onStart: (value: string) => void
  onEnd: (value: string) => void
  onValue: (value: string) => void
  validationError: string | null
  onConfirm: () => Promise<void>
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title="Renew this contract"
      description="Renewal sets a new term on this contract. The previous term stays in the version history so the old terms remain auditable."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button loading={busy} onClick={() => void onConfirm()}>
            Renew contract
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <DialogField
            id="renew-start"
            label="New start date"
            error={validationError && !start ? validationError : undefined}
          >
            <DateInput id="renew-start" value={start} onChange={onStart} disabled={busy} />
          </DialogField>
          <DialogField
            id="renew-end"
            label="New end date"
            error={validationError && start && !end ? validationError : undefined}
          >
            <DateInput id="renew-end" value={end} onChange={onEnd} disabled={busy} />
          </DialogField>
        </div>

        {validationError && start && end ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {validationError}
          </p>
        ) : null}

        <DialogField
          id="renew-value"
          label="New contract value"
          hint={`Optional, in ${currency}. Leave blank to keep the current value.`}
        >
          <Input
            id="renew-value"
            type="number"
            min="0"
            step="0.01"
            value={value}
            onChange={(event) => onValue(event.target.value)}
            disabled={busy}
          />
        </DialogField>

        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        {label}
      </dt>
      <dd className="truncate">{value}</dd>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <li className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular">{value}</span>
    </li>
  )
}

function LinkedRecord({
  label,
  id,
  name,
  href,
  kind,
}: {
  label: string
  id: string | null
  name: string | null
  href?: string
  kind?: 'project' | 'sow' | 'company' | 'user'
}) {
  if (!id) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="text-sm text-muted-foreground">Not set</span>
      </div>
    )
  }

  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      {name ? (
        <Link href={href ?? '#'} className="truncate text-sm font-medium hover:text-primary-strong">
          {name}
        </Link>
      ) : (
        <PublicId value={id} kind={kind} href={href} />
      )}
    </div>
  )
}