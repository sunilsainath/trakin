/**
 * Typed response shapes.
 *
 * These mirror the API's Pydantic schemas. The frontend never invents a field
 * the server did not send, and never assumes a field exists: anything optional
 * here is `| null` rather than optional-but-assumed.
 */

export type Visibility = 'PUBLIC' | 'CONNECTIONS' | 'PRIVATE'
export type MembershipStatus = 'PENDING' | 'ACTIVE' | 'SUSPENDED' | 'DEACTIVATED'
export type Sensitivity = 'STANDARD' | 'SENSITIVE' | 'FINANCIAL' | 'DESTRUCTIVE'

export interface PageMeta {
  total: number | null
  limit: number
  next_cursor: string | null
  has_more: boolean
}

export interface Page<T> {
  data: T[]
  meta: PageMeta
  request_id: string | null
}

export interface AckResponse {
  ok: true
  message: string | null
  request_id: string | null
}

/* ------------------------------------------------------------------ identity */

export interface AiSettings {
  assistant_enabled: boolean
  document_ai_enabled: boolean
  automations_enabled: boolean
  allow_training_use: boolean
}

export interface SecuritySettings {
  mfa_required: boolean
  login_alerts: boolean
  session_timeout_minutes: number
}

export interface Me {
  public_id: string
  email: string
  email_verified: boolean
  first_name: string
  last_name: string
  phone_e164: string | null
  country_code: string | null
  avatar_url: string | null
  headline: string | null
  bio: string | null
  location_city: string | null
  location_country: string | null
  timezone: string
  status: string
  onboarding_completed: boolean
  created_at: string
  settings: {
    notifications: Record<string, { in_app: boolean; email: boolean; push: boolean }>
    ai: AiSettings
    privacy: Record<string, Visibility>
    security: SecuritySettings
  }
}

export interface UserRef {
  public_id: string
  display_name: string
  headline: string | null
  avatar_url: string | null
}

export interface SensitiveFieldRef {
  field: string
  masked: string
  verification_state: string
}

export type ConnectionState = 'NONE' | 'PENDING_SENT' | 'PENDING_RECEIVED' | 'CONNECTED'

export interface UserProfile {
  public_id: string
  display_name: string
  headline: string | null
  bio: string | null
  avatar_url: string | null
  location_city: string | null
  location_country: string | null
  timezone: string
  profile_visibility: Visibility
  skills: string[]
  years_experience: number | null
  availability_status: string | null
  connection_state: ConnectionState
  mutual_connections: number
  sensitive: SensitiveFieldRef[]
  email?: string | null
  phone_e164?: string | null
}

export interface Session {
  public_id: string
  device: string | null
  ip_address: string | null
  current: boolean
  created_at: string
  last_seen_at: string
}

/* ------------------------------------------------------------------ business */

export interface Company {
  public_id: string
  display_name: string
  legal_name: string | null
  country_code: string | null
  status: string
  default_currency: string
  verification_state: string
  /** Role keys held by the caller here, e.g. SUPER_ADMIN. */
  my_role_keys: string[]
  /** Fully resolved permissions for the caller in this company. */
  my_permissions: string[]
  created_at: string
}

export interface Membership {
  public_id: string
  user: UserRef
  role_key: string
  role_name: string
  status: MembershipStatus
  job_title: string | null
  department: string | null
  joined_at: string | null
  hourly_rate: string | null
  currency: string | null
}

export interface Role {
  public_id: string
  key: string
  name: string
  description: string
  is_system: boolean
  is_assignable: boolean
  member_count: number
  permissions: string[]
}

export interface Permission {
  key: string
  module: string
  action: string
  description: string
  sensitivity: Sensitivity
  requires_approval: boolean
}

export interface Invitation {
  public_id: string
  invitation_token: string
  expires_at: string
}

/* --------------------------------------------------------------------- search */

export interface SearchHit {
  entity_type: 'PERSON' | 'COMPANY' | 'PROJECT' | 'SOW' | 'CONTRACT' | 'INVOICE' | 'DOCUMENT' | 'POST'
  public_id: string
  title: string
  subtitle: string | null
  rank?: number
}

/* ------------------------------------------------------------------------ AI */

export interface AiCitation {
  document_public_id: string
  title: string
  page_number: number | null
  section_path?: string | null
  similarity?: number
}

export interface AiAnswer {
  answered: boolean
  reason?: string
  message?: string
  answer?: string
  citations: AiCitation[]
  sources_summary?: string
  provider?: string
  model?: string
  tokens_used?: number
  cost_cents?: number
  conversation_id?: string | null
}

export interface AiFeatureState {
  enabled: boolean
  config: Record<string, unknown>
}

export interface AiCapabilities {
  gateway: {
    chat: Record<string, { configured: boolean; display_name: string }>
    embeddings: Record<string, { configured: boolean; dimensions: number }>
  }
  features: Record<string, AiFeatureState>
  consent: {
    assistant_enabled: boolean
    document_ai_enabled: boolean
    automations_enabled: boolean
  }
}

export interface AgentSummary {
  key: string
  display_name: string
  purpose: string
}

export interface AgentPlan {
  agent: string
  status: 'PROPOSED' | 'PENDING_APPROVAL' | 'ANSWERED' | 'AWAITING_APPROVAL'
  action_public_id: string | null
  requires_approval: boolean
  proposal: Record<string, unknown>
  rationale: string
  citations: AiCitation[]
}

/* -------------------------------------------------------------- notifications */

export interface NotificationItem {
  public_id: string
  type: string
  title: string
  body: string | null
  resource_type: string | null
  resource_public_id: string | null
  action_url: string | null
  severity: 'INFO' | 'SUCCESS' | 'WARNING' | 'CRITICAL'
  read: boolean
  created_at: string
}

export interface UnreadCounts {
  notifications: number
  messages: number
  approvals: number
}

/* --------------------------------------------------------------------- audit */

export interface AuditEntry {
  occurred_at: string
  action: string
  resource_type: string
  resource_public_id: string | null
  actor_type: string
  actor_label: string | null
  changed_fields: string[]
  reason: string | null
  request_id: string | null
  metadata: Record<string, unknown>
}

/* ------------------------------------------------------------------ payments */

export interface IntegrationCapabilities {
  bank_connections: Record<string, { configured: boolean }>
  payment_processors: Record<string, { configured: boolean; moves_money: boolean }>
  token_encryption_available: boolean
}
