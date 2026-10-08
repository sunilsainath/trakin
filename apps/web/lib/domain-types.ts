/**
 * API response types for the CORE, WORK, BILLING and PLATFORM modules.
 *
 * These mirror the API's Pydantic schemas (see `apps/api/app/schemas`). Amounts
 * are decimal strings, not numbers: `0.1 + 0.2` in binary floating point is how a
 * finance screen ends up showing a cent that does not exist. They are formatted
 * with `Intl` and never summed in JavaScript.
 *
 * Anything the API returns as a free-form object is typed as `Record<string,
 * unknown>` and read through a narrowing helper rather than being asserted into a
 * shape the server never promised.
 */

import type { Page, SearchHit } from '@/lib/types'

export type { Page }

/** A monetary amount exactly as the API sends it. */
export type Money = string

/* -------------------------------------------------------------------------- */
/* CORE: projects                                                              */
/* -------------------------------------------------------------------------- */

export const PROJECT_STATUSES = [
  'DRAFT',
  'PLANNING',
  'ACTIVE',
  'ON_HOLD',
  'COMPLETED',
  'CANCELLED',
  'CLOSED',
] as const
export type ProjectStatus = (typeof PROJECT_STATUSES)[number]

export const BILLING_BASES = ['TIMESHEET', 'FIXED', 'RECURRING', 'USAGE', 'MILESTONE'] as const
export type BillingBasis = (typeof BILLING_BASES)[number]

export const BILLING_FREQUENCIES = ['WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'CUSTOM'] as const
export type BillingFrequency = (typeof BILLING_FREQUENCIES)[number]

export const RATE_TYPES = ['HOURLY', 'DAILY', 'FIXED', 'PER_UNIT', 'PERCENT'] as const
export type RateType = (typeof RATE_TYPES)[number]

export interface Project {
  public_id: string
  company_id: string
  name: string
  description: string | null
  project_type: string
  category: string
  status: string
  start_date: string | null
  estimated_end_date: string | null
  estimated_hours: string | null
  estimated_budget: string | null
  currency: string
  billing_basis: string
  billing_frequency: string
  payment_terms_days: number
  health_score: number | null
  owner_user_id: string | null
  owner_name: string | null
  client_name: string | null
  client_company_id: string | null
  contract_value: string | null
  invoiced_total: string
  outstanding_total: string
  billing_status: string
  team_size: number
  open_role_count: number
  role_count: number
  sow_count: number
  contract_count: number
  active_contract_count: number
  timesheet_count: number
  allowed_transitions?: string[]
  last_activity_at: string | null
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ProjectRole {
  public_id: string
  project_id: string
  project_name: string | null
  title: string
  description: string | null
  required_count: number
  allocated_count: number
  required_skills: string[]
  seniority: string | null
  min_hourly_rate: string | null
  max_hourly_rate: string | null
  cost_rate: string | null
  currency: string
  billing_basis: string
  allocation_pct: string
  start_date: string | null
  end_date: string | null
  status: string
  contracted_count: number
  active_assignments: number
  approved_hours_to_date: string
  utilisation_pct: string
  created_at: string
  updated_at: string
}

/** The aggregate `GET /projects/{id}` returns in one round trip. */
export interface ProjectDashboard {
  project: Project
  roles: ProjectRole[]
  team: Assignment[]
  sows: Sow[]
  contracts: Contract[]
  timesheets: Record<string, unknown>[]
  invoices: Record<string, unknown>[]
  documents: Record<string, unknown>[]
  activity: ProjectActivityEntry[]
  insights: ProjectInsight[]
  billing: ProjectBilling
  permissions: Record<string, boolean>
}

export interface ProjectActivityEntry {
  action: string
  resource_type: string
  resource_public_id: string | null
  actor_public_id: string | null
  actor_name: string | null
  at: string
}

export interface ProjectInsight {
  kind: string
  public_id?: string
  severity?: string
  title?: string | null
  score?: number
  summary?: string | null
  signals?: string[]
  data?: Record<string, unknown>
  created_at?: string
}

export interface ProjectBilling {
  invoices_issued?: number
  invoiced?: string
  outstanding?: string
  overdue_invoices?: number
  unbilled_timesheets?: number
  unbilled_hours?: string
}

/* -------------------------------------------------------------------------- */
/* CORE: statements of work and contracts                                     */
/* -------------------------------------------------------------------------- */

export const SOW_STATUSES = [
  'DRAFT',
  'PENDING_APPROVAL',
  'SENT',
  'PENDING_ACCEPTANCE',
  'ACTIVE',
  'REJECTED',
  'EXPIRED',
  'TERMINATED',
  'CLOSED',
] as const
export type SowStatus = (typeof SOW_STATUSES)[number]

export interface SowRole {
  project_role_id: string
  role_title: string | null
  quantity: number
  rate: string | null
  rate_type: string
  currency: string
  notes: string | null
}

export interface Sow {
  public_id: string
  project_id: string
  project_name: string | null
  sow_type: string
  counterparty_company_id: string | null
  counterparty_company_name: string | null
  counterparty_user_id: string | null
  counterparty_user_name: string | null
  title: string
  description: string | null
  scope: string | null
  deliverables: Record<string, unknown>[]
  milestones: Record<string, unknown>[]
  status: string
  start_date: string | null
  end_date: string | null
  currency: string
  default_rate: string | null
  billing_basis: string
  billing_frequency: string
  invoice_frequency: string
  payment_terms_days: number
  payment_method: string | null
  special_conditions: string | null
  max_total_amount: string | null
  auto_generate_contracts: boolean
  approved_by: string | null
  approved_at: string | null
  document_id: string | null
  roles: SowRole[]
  contract_count: number
  contract_ids: string[]
  history: Record<string, unknown>[]
  created_at: string
  updated_at: string
}

export const CONTRACT_STATUSES = [
  'DRAFT',
  'SENT',
  'PENDING_ACCEPTANCE',
  'ACCEPTED',
  'ACTIVE',
  'DECLINED',
  'EXPIRED',
  'TERMINATED',
  'CLOSED',
] as const
export type ContractStatus = (typeof CONTRACT_STATUSES)[number]

export interface ContractRole {
  project_role_id: string
  role_title: string | null
  quantity: number
  rate: string | null
  rate_type: string
  currency: string
  billing_basis: string
  billing_frequency: string
  payment_terms_days: number
  max_units: string | null
  start_date: string | null
  end_date: string | null
  overtime_rule: string
  overtime_rate_multiplier: string
  tax_rule: string
  tax_rate: string
  notes: string | null
  billed_units: string
}

export interface ContractApprovalStep {
  step_no: number
  name: string
  status: string
  required_permission: string | null
  approver_user_id: string | null
  approver_company_id: string | null
  decided_at: string | null
  notes: string | null
}

export interface Contract {
  public_id: string
  project_id: string
  project_name: string | null
  sow_id: string
  sow_title: string | null
  title: string
  contract_type: string
  status: string
  currency: string
  billing_basis: string
  billing_frequency: string
  payment_terms_days: number
  start_date: string | null
  end_date: string | null
  contract_value: string | null
  auto_renew: boolean
  renewal_notice_days: number | null
  termination_notice_days: number | null
  notice_period_end: string | null
  governing_law: string | null
  confidentiality_level: string
  requires_timesheets: boolean
  locked: boolean
  version: number
  risk_score: number | null
  counterparty_company_id: string | null
  counterparty_company_name: string | null
  counterparty_user_id: string | null
  counterparty_user_name: string | null
  document_id: string | null
  roles: ContractRole[]
  parties: Record<string, unknown>[]
  line_items: Record<string, unknown>[]
  approval_steps: ContractApprovalStep[]
  invoiced_total: string
  outstanding_total: string
  invoice_count: number
  assignment_count: number
  timesheet_count: number
  sent_at: string | null
  responded_at: string | null
  activated_at: string | null
  terminated_at: string | null
  response_notes: string | null
  /** Target statuses the server will accept. Drives which action buttons render. */
  allowed_transitions: string[]
  created_at: string
  updated_at: string
}

export interface VersionRow {
  version: number
  status: string
  changed_at: string
  changed_by: string | null
  changed_by_name: string | null
  reason: string | null
  snapshot: Record<string, unknown>
}

/* -------------------------------------------------------------------------- */
/* WORK: assignments, timesheets, leave                                       */
/* -------------------------------------------------------------------------- */

export interface Assignment {
  /** A UUID, not a public id: the assignment endpoints are keyed by uuid. */
  id: string
  contract_id: string
  contract_title: string | null
  contract_status: string | null
  project_id: string
  project_name: string | null
  role_id: string | null
  role_title: string | null
  user_id: string
  user_name: string | null
  role_title_override: string | null
  hourly_rate: string | null
  currency: string
  start_date: string
  end_date: string | null
  status: string
  allocation_pct: string
  hours_to_date: string
  source: string
  created_at: string
  updated_at: string
}

export const TIMESHEET_STATUSES = [
  'DRAFT',
  'SUBMITTED',
  'UNDER_REVIEW',
  'APPROVED',
  'LOCKED',
  'REJECTED',
] as const
export type TimesheetStatus = (typeof TIMESHEET_STATUSES)[number]

export interface TimesheetEntry {
  id: string
  entry_date: string
  start_time: string | null
  end_time: string | null
  break_minutes: number
  hours: string
  is_billable: boolean
  work_description: string
  project_task: string | null
  rate_applied: string | null
  amount: string
  currency: string
  source: string
}

export interface Timesheet {
  public_id: string
  user_id: string
  user_name: string | null
  project_id: string
  project_name: string | null
  contract_id: string
  contract_title: string | null
  role_id: string | null
  role_title: string | null
  assignment_id: string
  period_start: string
  period_end: string
  billing_frequency: string
  status: string
  total_hours: string
  billable_hours: string
  total_amount: string
  currency: string
  entry_count: number
  current_step: number
  /** Whether the server will still accept entry edits. */
  editable: boolean
  submitted_at: string | null
  approved_at: string | null
  locked_at: string | null
  rejection_reason: string | null
  entries: TimesheetEntry[]
  approvals: Record<string, unknown>[]
  revisions: Record<string, unknown>[]
  created_at: string
  updated_at: string
}

export const LEAVE_STATUSES = ['DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'CANCELLED'] as const
export type LeaveStatus = (typeof LEAVE_STATUSES)[number]

export const LEAVE_TYPES = [
  'ANNUAL',
  'SICK',
  'CASUAL',
  'PARENTAL',
  'UNPAID',
  'BEREAVEMENT',
  'COMP_OFF',
  'OTHER',
] as const
export type LeaveType = (typeof LEAVE_TYPES)[number]

export interface LeaveRequest {
  public_id: string
  user_id: string
  user_name: string | null
  policy_public_id: string | null
  policy_name: string | null
  leave_type: string | null
  start_date: string
  end_date: string
  total_days: string
  reason: string | null
  status: string
  approver_user_id: string | null
  decided_at: string | null
  decision_notes: string | null
  created_at: string | null
}

export interface LeavePolicy {
  public_id: string
  name: string
  leave_type: string
  accrual_method: string
  accrual_rate: string
  max_balance: string | null
  carry_forward_limit: string | null
  requires_approval: boolean
  min_notice_days: number
  max_consecutive_days: number | null
  allow_negative_balance: boolean
  effective_from: string
  effective_to: string | null
  is_active: boolean
}

export interface LeaveBalance {
  policy_public_id: string
  policy_name: string
  leave_type: string
  year: number
  entitled: string
  accrued: string
  taken: string
  pending: string
  carried_over: string
  available: string | null
  expires_on: string | null
}

/* -------------------------------------------------------------------------- */
/* BILLING: invoices and runs                                                 */
/* -------------------------------------------------------------------------- */

export const INVOICE_STATUSES = [
  'DRAFT',
  'PENDING',
  'SUBMITTED',
  'APPROVED',
  'PARTIALLY_PAID',
  'PAID',
  'OVERDUE',
  'DISPUTED',
  'REJECTED',
  'CANCELLED',
  'REFUNDED',
] as const
export type InvoiceStatus = (typeof INVOICE_STATUSES)[number]

export interface InvoiceItem {
  id: string
  description: string
  line_type: string
  quantity: string
  unit: string
  unit_rate: string
  subtotal: string
  tax_rate: string
  tax_total: string
  total: string
  currency: string
  service_period_start: string | null
  service_period_end: string | null
  source_timesheet_id: string | null
  project_role_id: string | null
  project_role_title: string | null
}

export interface Invoice {
  public_id: string
  invoice_number: string | null
  direction: string
  status: string
  company_id: string
  contract_id: string
  contract_title: string | null
  project_id: string | null
  project_name: string | null
  sow_id: string | null
  sow_title: string | null
  counterparty_company_id: string | null
  counterparty_company_name: string | null
  counterparty_user_id: string | null
  counterparty_user_name: string | null
  role_ids: string[]
  period_start: string
  period_end: string
  issue_date: string | null
  due_date: string
  currency: string
  subtotal: string
  tax_total: string
  total_amount: string
  amount_paid: string
  amount_disputed: string
  balance_due: string
  allocated_total: string
  payment_terms_days: number
  msa_required: boolean
  msa_block_reason: string | null
  disputed_reason: string | null
  rejected_reason: string | null
  notes: string | null
  terms_snapshot: Record<string, unknown>
  locked: boolean
  version: number
  items: InvoiceItem[]
  allocations: Record<string, unknown>[]
  approvals: Record<string, unknown>[]
  history: Record<string, unknown>[]
  allowed_transitions: string[]
  item_count: number
  submitted_at: string | null
  approved_at: string | null
  paid_at: string | null
  created_at: string
  updated_at: string
}

export interface InvoicePreview {
  contract_id: string
  contract_status: string
  period_start: string
  period_end: string
  currency: string
  subtotal: string
  tax_total: string
  total: string
  items: Record<string, unknown>[]
  warnings: string[]
  msa_required: boolean
}

export interface BillingRun {
  public_id: string
  run_type: string
  status: string
  period_start: string | null
  period_end: string | null
  contracts_scanned: number
  invoices_created: number
  invoices_skipped: number
  total_amount: string
  currency: string
  started_at: string | null
  finished_at: string | null
  error_details: unknown
  created_at: string
}

export interface AgingBucket {
  bucket: string
  invoice_count: number
  amount: string
}

export interface ReceivablesSummary {
  open_count: number
  outstanding: string
  overdue: string
  not_yet_due: string
  invoiced_this_month: string
  collected_this_month: string
  aging: AgingBucket[]
  late_payers: { counterparty: string; invoice_count: number; amount: string; days_late: number }[]
  revenue_by_role: Record<string, unknown>[]
  revenue_by_project: Record<string, unknown>[]
}

/* -------------------------------------------------------------------------- */
/* PAYMENTS, bank accounts and reconciliation                                 */
/* -------------------------------------------------------------------------- */

export const PAYMENT_STATUSES = [
  'SCHEDULED',
  'INITIATED',
  'PROCESSING',
  'COMPLETED',
  'FAILED',
  'PARTIALLY_REFUNDED',
  'REFUNDED',
  'CANCELLED',
] as const
export type PaymentStatus = (typeof PAYMENT_STATUSES)[number]

export interface PaymentAllocation {
  id: string
  invoice_id: string | null
  invoice_public_id: string | null
  invoice_number: string | null
  amount: string
  currency: string
  allocation_type: string
  confidence: string | null
  matched_by: string
  confirmed_at: string | null
}

export interface Payment {
  public_id: string
  direction: string
  status: string
  amount: string
  currency: string
  fee_amount: string
  net_amount: string | null
  payment_method: string
  counterparty_company_id: string | null
  counterparty_company_name: string | null
  counterparty_name: string | null
  bank_account_id: string | null
  account_number_masked: string | null
  payment_account_id: string | null
  scheduled_for: string | null
  initiated_at: string | null
  completed_at: string | null
  failed_at: string | null
  failure_reason: string | null
  authorization_type: string
  processor: string
  processor_payment_ref: string | null
  reconciliation_status: string
  bank_transaction_id: string | null
  allocated_total: string
  allocation_count: number
  allocations: PaymentAllocation[]
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface PaymentRequest {
  public_id: string
  requester_company_id: string
  counterparty_company_id: string | null
  invoice_id: string | null
  invoice_public_id: string | null
  invoice_number: string | null
  amount: string
  currency: string
  status: string
  requested_by: string | null
  requested_due_date: string | null
  notes: string | null
  created_at: string
}

export interface BankConnection {
  public_id: string
  provider: string
  institution_name: string | null
  institution_id: string | null
  status: string
  verification_state: string
  ownership_verified: boolean
  consent_expires_at: string | null
  last_synced_at: string | null
  transaction_count: number
  account_count: number
  owner_user_id: string | null
  created_at: string
  error_detail: Record<string, unknown> | null
}

export interface BankAccount {
  public_id: string
  connection_public_id: string
  institution_name: string
  name: string | null
  account_number_masked: string
  account_type: string
  currency: string
  status: string
  verification_state: string
  is_primary: boolean
  available_balance: string | null
  current_balance: string | null
  connected_at: string | null
  verified_at: string | null
  last_synced_at: string | null
  transaction_count: number
  unmatched_count: number
}

export interface BankTransaction {
  /** A UUID: the reconciliation endpoints are keyed by transaction uuid. */
  id: string
  account_id: string
  account_public_id: string
  account_number_masked: string | null
  institution_name: string | null
  posted_at: string
  authorized_at: string | null
  amount: string
  currency: string
  direction: string
  description_raw: string | null
  merchant_name: string | null
  normalized_description: string | null
  category: string | null
  is_pending: boolean
  is_reconciled: boolean
  match_status: string
  match_confidence: string | null
  invoice_public_id: string | null
  invoice_number: string | null
  payment_public_id: string | null
  imported_at: string | null
}

export interface MatchCandidate {
  invoice_id: string
  invoice_number: string | null
  invoice_total: string
  invoice_balance_due: string
  invoice_currency: string
  invoice_status: string
  counterparty: string | null
  project_name: string | null
  due_date: string | null
  confidence: number
  suggestion: 'MATCH' | 'REVIEW' | 'IGNORE'
  reason: string
  score_breakdown: Record<string, number>
}

export interface MatchSuggestion {
  id: string
  confidence: string
  score_breakdown: Record<string, unknown>
  suggestion: string
  status: string
  decided_at: string | null
  decision_notes: string | null
  transaction_id: string
  posted_at: string
  txn_amount: string
  txn_currency: string
  description_raw: string | null
  merchant_name: string | null
  invoice_public_id: string
  invoice_number: string | null
  balance_due: string
  invoice_currency: string
  due_date: string | null
  created_at: string
}

export interface ReconciliationSummary {
  open_txns: number
  unmatched_inbound: string
  unmatched_total: string
  reconciled_count: number
  pending_suggestions: number
}

/* -------------------------------------------------------------------------- */
/* Documents and master service agreements                                    */
/* -------------------------------------------------------------------------- */

export interface DocumentSummary {
  public_id: string
  company_id: string
  owner_user_id: string | null
  doc_type: string
  title: string
  description: string | null
  visibility: string
  current_version_id: string | null
  version_count: number
  status: string
  is_legal_hold: boolean
  retention_until: string | null
  retention_policy: string | null
  related_type: string | null
  related_id: string | null
  checksum_sha256: string | null
  ai_processing_state: string | null
  version_no: number | null
  file_name: string | null
  content_type: string | null
  byte_size: number | null
  created_at: string
  updated_at: string
}

export interface DocumentDownload {
  document_id: string
  url: string
  expires_in_seconds: number
}

export const DOC_TYPES = [
  'CONTRACT',
  'SOW',
  'INVOICE',
  'MSA',
  'W9',
  'W2',
  'TAX',
  'IDENTITY',
  'TIMESHEET',
  'OTHER',
] as const

export interface MsaVersion {
  version_no: number
  status: string
  submitted_at: string | null
  reviewed_at: string | null
  review_notes: string | null
  effective_date: string | null
  expiration_date: string | null
  document_version_id: string | null
  created_at: string
  submitted_by: string | null
  reviewed_by: string | null
}

export interface MsaRequest {
  status: string
  message: string | null
  template_key: string | null
  expires_at: string | null
  response_notes: string | null
  created_at: string
  requester_company_id: string
  requester_company_name: string
  target_company_id: string
  target_company_name: string
}

export interface Msa {
  public_id: string
  company_a_id: string
  company_a_name: string
  company_b_id: string
  company_b_name: string
  status: string
  effective_date: string | null
  expiration_date: string | null
  auto_renew: boolean
  renewal_notice_days: number | null
  governing_law: string | null
  payment_terms_days: number
  current_version_id: string | null
  current_version_no: number
  current_version_status: string | null
  version_effective_date: string | null
  version_expiration_date: string | null
  requested_by: string | null
  activated_at: string | null
  terminated_at: string | null
  notes: string | null
  metadata: Record<string, unknown>
  versions: MsaVersion[]
  requests: MsaRequest[]
  allowed_transitions: string[]
  created_at: string
  updated_at: string
}

/* -------------------------------------------------------------------------- */
/* Platform: dashboard, notifications, AI                                     */
/* -------------------------------------------------------------------------- */

export interface ReceivablesPanel {
  open_count: number
  outstanding: string
  overdue: string
  not_yet_due: string
  invoiced_this_month: string
  collected_this_month: string
  aging: AgingBucket[]
  late_payers: ReceivablesSummary['late_payers']
  revenue_by_role: Record<string, unknown>[]
  revenue_by_project: Record<string, unknown>[]
}

export interface DashboardResponse {
  company: { public_id: string | null; name: string | null; currency: string }
  my_role_keys: string[]
  domains: string[]
  panels: {
    finance?: {
      receivables: ReceivablesPanel
      bank?: { accounts: number; cash_balance: string }
      reconciliation?: ReconciliationSummary
    }
    projects?: {
      total: number
      active: number
      pipeline: number
      on_hold: number
      completed: number
      avg_health: number
      at_risk: { public_id: string; name: string; status: string; health_score: number }[]
    }
    contracts?: {
      by_status: Record<string, number>
      total: number
      expiring_within_60_days: {
        public_id: string
        title: string
        end_date: string
        status: string
      }[]
    }
    sows?: Record<string, number>
    timesheets?: {
      drafts: number
      rejected: number
      awaiting: number
      approved_recent: number
      billable_hours_this_month: string
      mine_awaiting: number
    }
    leave?: Record<string, number>
    people?: { members: number; on_active_work: number }
    me?: {
      assignments: Record<string, unknown>[]
      timesheets: Record<string, unknown>[]
      leave: Record<string, unknown>[]
      pending_approvals: number
    }
    ai_alerts: {
      public_id: string
      type: string
      severity: string
      title: string
      summary: string
      created_at: string
    }[]
  }
}

/** `GET /notifications/unread-count` — six independent counters. */
export interface UnreadCountResponse {
  notifications: number
  approvals: number
  timesheets: number
  invoices: number
  contracts: number
  leave: number
}

export interface AssistantAnswer {
  question: string
  answer: string
  provider: string
  citations: {
    document_public_id: string
    title: string
    page_number: number | null
    section_path?: string | null
    similarity?: number
  }[]
  /** Set when the platform answered from a fallback rather than a live model. */
  degraded_reason: string | null
  context_summary: string | null
}

export interface AiInsight {
  public_id: string
  insight_type: string
  severity: string
  title: string
  summary: string
  entity_type: string
  entity_public_id: string | null
  data: Record<string, unknown>
  confidence: string | null
  created_at: string
  expires_at: string | null
}

export interface AiKnowledge {
  public_id: string
  title: string
  source_type: string
  status: string
  chunk_count: number
  token_count: number
  language: string | null
  created_at: string
  updated_at: string
}

export interface AiAutomation {
  public_id: string
  name: string
  description: string | null
  trigger_type: string
  is_active: boolean
  schedule_cron: string | null
  risk_level: string | null
  last_run_at: string | null
  run_count: number
  created_at: string
}

export interface AiAction {
  public_id: string
  agent_key: string
  action_type: string
  target_type: string | null
  status: string
  risk_level: string | null
  required_permission: string | null
  rationale: string | null
  parameters: Record<string, unknown>
  created_at: string
  expires_at: string | null
  proposed_by: string | null
}

export type { SearchHit }