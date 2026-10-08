-- =============================================================================
-- MyTrakin :: 0013_seed_reference.sql
-- Development/reference data only. Contains NO user, company or transactional
-- data — permissions, role templates, reference taxonomies and feature flags.
--
-- Loaded by `supabase/seed.sql` in development and by `make db-reset`.
-- Never run against production: production permissions are managed through
-- migrations, and platform roles cannot edit permission keys.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Permission catalogue
-- Module: identity, social, business, code, work, billing, payments, documents,
--         ai, platform
-- -----------------------------------------------------------------------------
INSERT INTO public.permissions (key, module, action, description, sensitivity, requires_approval) VALUES
-- ---- identity & account
('profile.read',            'identity', 'read',   'View own profile and settings',                     'STANDARD',  false),
('profile.update',          'identity', 'update', 'Update own profile',                                'STANDARD',  false),
('profile.read_sensitive',  'identity', 'read',   'View own masked sensitive identifiers',             'SENSITIVE', true),
('sessions.read',           'identity', 'read',   'View own active sessions and devices',              'STANDARD',  false),
('sessions.revoke',         'identity', 'update', 'Revoke own sessions',                               'STANDARD',  false),

-- ---- social
('connections.read',        'social',   'read',   'List own connections',                              'STANDARD',  false),
('connections.create',      'social',   'create', 'Send connection requests',                          'STANDARD',  false),
('connections.respond',     'social',   'update', 'Accept or decline connection requests',              'STANDARD',  false),
('connections.remove',      'social',   'update', 'Remove an existing connection',                     'STANDARD',  false),
('posts.read',              'social',   'read',   'View the feed',                                     'STANDARD',  false),
('posts.create',            'social',   'create', 'Create posts',                                      'STANDARD',  false),
('posts.update',            'social',   'update', 'Edit own posts',                                    'STANDARD',  false),
('posts.delete',            'social',   'delete', 'Delete own posts',                                  'STANDARD',  false),
('posts.manage',            'social',   'update', 'Moderate company posts',                            'SENSITIVE', false),
('messages.read',           'social',   'read',   'Read conversations',                                'STANDARD',  false),
('messages.send',           'social',   'create', 'Send messages',                                     'STANDARD',  false),
('reports.create',          'social',   'create', 'Report users or content',                           'STANDARD',  false),

-- ---- business / company
('companies.create',        'business', 'create', 'Create a company',                                  'STANDARD',  false),
('companies.read',          'business', 'read',   'View company details',                              'STANDARD',  false),
('companies.update',        'business', 'update', 'Update company details',                            'STANDARD',  false),
('companies.delete',        'business', 'delete', 'Delete/close a company',                            'DESTRUCTIVE', true),
('members.read',            'business', 'read',   'List company members',                              'STANDARD',  false),
('members.invite',          'business', 'create', 'Invite users to the company',                        'STANDARD',  false),
('members.manage',          'business', 'update', 'Change roles, deactivate or remove members',        'SENSITIVE', true),
('roles.read',              'business', 'read',   'List company roles and their permissions',          'STANDARD',  false),
('roles.manage',            'business', 'update', 'Create and edit company roles',                     'SENSITIVE', true),
('settings.read',           'business', 'read',   'View company settings',                             'STANDARD',  false),
('settings.update',         'business', 'update', 'Update company settings',                           'STANDARD',  false),
('dashboard.read',          'business', 'read',   'View the company dashboard',                        'STANDARD',  false),

-- ---- documents & MSA
('documents.read',          'documents','read',   'View document metadata',                            'STANDARD',  false),
('documents.read_audit',    'documents','read',   'View document access logs',                         'SENSITIVE', false),
('documents.upload',        'documents','create', 'Upload documents and new versions',                 'STANDARD',  false),
('documents.download',      'documents','read',   'Download document contents',                        'STANDARD',  false),
('documents.delete',        'documents','delete', 'Delete documents (blocked by retention/hold)',       'DESTRUCTIVE', true),
('msas.read',               'documents','read',   'View MSAs',                                         'STANDARD',  false),
('msas.request',            'documents','create', 'Request an MSA from a counterparty',                 'STANDARD',  false),
('msas.submit',             'documents','create', 'Submit an MSA for review',                          'STANDARD',  false),
('msas.review',             'documents','update', 'Accept or reject an MSA',                           'SENSITIVE', true),

-- ---- CODE: projects
('projects.read',           'code',     'read',   'View projects',                                     'STANDARD',  false),
('projects.create',         'code',     'create', 'Create projects',                                   'STANDARD',  false),
('projects.update',         'code',     'update', 'Edit projects',                                     'STANDARD',  false),
('projects.delete',         'code',     'delete', 'Delete projects',                                   'DESTRUCTIVE', true),
('projects.manage_roles',   'code',     'update', 'Define project roles and capacity',                 'STANDARD',  false),

-- ---- CODE: SOW
('sows.read',               'code',     'read',   'View SOWs',                                         'STANDARD',  false),
('sows.create',             'code',     'create', 'Create SOWs',                                       'STANDARD',  false),
('sows.update',             'code',     'update', 'Edit SOWs',                                         'STANDARD',  false),
('sows.approve',            'code',     'approve','Approve and activate SOWs',                         'FINANCIAL', true),
('sows.delete',             'code',     'delete', 'Delete SOWs',                                       'DESTRUCTIVE', true),

-- ---- CODE: contracts
('contracts.read',          'code',     'read',   'View contracts',                                    'STANDARD',  false),
('contracts.read_rates',    'code',     'read',   'View commercial rates on contracts',                'FINANCIAL', false),
('contracts.create',        'code',     'create', 'Create contracts from SOW allocations',             'STANDARD',  false),
('contracts.update',        'code',     'update', 'Edit contract terms',                               'FINANCIAL', true),
('contracts.send',          'code',     'create', 'Send a contract for acceptance',                    'STANDARD',  false),
('contracts.accept',        'code',     'update', 'Accept or decline a contract',                     'STANDARD',  false),
('contracts.approve',       'code',     'approve','Approve a contract for activation',                 'FINANCIAL', true),
('contracts.delete',        'code',     'delete', 'Delete or close contracts',                         'DESTRUCTIVE', true),

-- ---- WORK
('workforce.read',          'work',     'read',   'View assignments',                                  'STANDARD',  false),
('workforce.manage',        'work',     'update', 'Create and manage assignments',                     'STANDARD',  false),
('timesheets.read_any',     'work',     'read',   'View any timesheet in the company',                 'SENSITIVE', false),
('timesheets.read_rate',    'work',     'read',   'View billable rates on timesheets',                 'FINANCIAL', false),
('timesheets.create',       'work',     'create', 'Create own timesheets',                             'STANDARD',  false),
('timesheets.create_any',   'work',     'create', 'Create timesheets on behalf of others',             'SENSITIVE', true),
('timesheets.submit',       'work',     'update', 'Submit timesheets for approval',                    'STANDARD',  false),
('timesheets.approve',      'work',     'approve','Approve or reject timesheets',                      'SENSITIVE', true),
('timesheets.lock',         'work',     'update', 'Lock approved timesheets',                          'STANDARD',  false),
('leave.request',           'work',     'create', 'Submit leave requests',                             'STANDARD',  false),
('leave.read_any',          'work',     'read',   'View any leave request in the company',             'STANDARD',  false),
('leave.approve',           'work',     'approve','Approve or reject leave',                           'STANDARD',  false),
('leave.manage',            'work',     'update', 'Manage leave policies and balances',                'STANDARD',  false),

-- ---- billing
('invoices.read',           'billing',  'read',   'View invoices',                                     'STANDARD',  false),
('invoices.create',         'billing',  'create', 'Generate invoices',                                 'FINANCIAL', true),
('invoices.update',         'billing',  'update', 'Edit draft invoices',                               'FINANCIAL', true),
('invoices.submit',         'billing',  'update', 'Submit invoices to the counterparty',               'FINANCIAL', true),
('invoices.approve',        'billing',  'approve','Approve invoices',                                  'FINANCIAL', true),
('invoices.cancel',         'billing',  'update', 'Cancel invoices',                                   'FINANCIAL', true),
('billing_runs.read',       'billing',  'read',   'View billing runs',                                 'STANDARD',  false),
('billing_runs.execute',    'billing',  'create', 'Execute the invoice engine',                        'FINANCIAL', true),

-- ---- payments
('payments.read',           'payments', 'read',   'View payments and accounts',                        'FINANCIAL', false),
('payments.create',         'payments', 'create', 'Record payments',                                   'FINANCIAL', true),
('payments.update',         'payments', 'update', 'Edit payment details',                              'FINANCIAL', true),
('payments.initiate',       'payments', 'update', 'Initiate money movement',                           'FINANCIAL', true),
('payments.complete',       'payments', 'update', 'Mark a payment as completed',                       'FINANCIAL', true),
('payments.manage',         'payments', 'update', 'Manage processor accounts and schedules',           'FINANCIAL', true),
('payments.connect_bank',   'payments', 'create', 'Connect and verify bank accounts',                  'SENSITIVE', true),
('transactions.read',       'payments', 'read',   'View bank transactions',                            'FINANCIAL', false),
('reconciliation.read',     'payments', 'read',   'View reconciliation suggestions',                   'STANDARD',  false),
('reconciliation.manage',   'payments', 'update', 'Accept or reject reconciliation matches',           'FINANCIAL', true),
('payment_requests.create', 'payments', 'create', 'Request payment',                                   'STANDARD',  false),

-- ---- AI
('ai.assistant',            'ai',       'create', 'Use the AI assistant',                              'STANDARD',  false),
('ai.insights.read',        'ai',       'read',   'View AI insights',                                  'STANDARD',  false),
('ai.contract_intelligence','ai',       'create', 'Run contract intelligence',                         'STANDARD',  false),
('ai.document_intelligence','ai',       'create', 'Run document intelligence (OCR/extraction)',        'STANDARD',  false),
('ai.project_intelligence', 'ai',       'create', 'Run project intelligence',                          'STANDARD',  false),
('ai.workforce_intelligence','ai',      'create', 'Run workforce intelligence',                         'STANDARD',  false),
('ai.financial_intelligence','ai',      'create', 'Run financial intelligence',                         'FINANCIAL', true),
('ai.extractions.read',     'ai',       'read',   'Review AI extraction results',                      'SENSITIVE', false),
('ai.extractions.create',   'ai',       'create', 'Trigger document extraction',                       'STANDARD',  false),
('ai.actions.approve',      'ai',       'approve','Approve AI-proposed actions',                       'FINANCIAL', true),
('ai.automations.manage',   'ai',       'update', 'Create and enable AI automations',                  'FINANCIAL', true),
('ai.usage.read',           'ai',       'read',   'View AI usage and cost',                            'STANDARD',  false),
('ai.knowledge.write',      'ai',       'create', 'Add or remove company knowledge',                   'STANDARD',  false),

-- ---- platform
('audit.read',              'platform', 'read',   'Read audit history',                                'SENSITIVE', false),
('tasks.read',              'platform', 'read',   'View background job history',                       'STANDARD',  false),
('search.use',              'platform', 'read',   'Use global search',                                 'STANDARD',  false),
('notifications.read',      'platform', 'read',   'Read own notifications',                            'STANDARD',  false),
('files.upload',            'platform', 'create', 'Upload files',                                      'STANDARD',  false)
ON CONFLICT (key) DO UPDATE
  SET description  = EXCLUDED.description,
      module       = EXCLUDED.module,
      action       = EXCLUDED.action,
      sensitivity  = EXCLUDED.sensitivity;

-- -----------------------------------------------------------------------------
-- Role templates
-- Copied into every new company by app.bootstrap_company_roles(). Business rules
-- live here as data: an administrator can compose a new role from these keys
-- without a code change or a deploy.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.role_templates (
  id            uuid PRIMARY KEY DEFAULT app.uuid7(),
  key           text NOT NULL UNIQUE,
  name          text NOT NULL,
  description   text NOT NULL DEFAULT '',
  category      text NOT NULL DEFAULT 'STANDARD',
  -- Ordered grant list applied to the copied role.
  permission_keys text[] NOT NULL DEFAULT '{}',
  is_default    boolean NOT NULL DEFAULT false,   -- granted to the founding SUPER_ADMIN only
  is_assignable boolean NOT NULL DEFAULT true,
  sort_order    int NOT NULL DEFAULT 100
);

CREATE INDEX IF NOT EXISTS ix_role_templates_category ON public.role_templates (category, sort_order);

INSERT INTO public.role_templates (key, name, description, category, permission_keys, is_default, sort_order) VALUES
('SUPER_ADMIN', 'Super Admin', 'Unrestricted authority within the company. Still bound by segregation-of-duties policy where the company requires it.', 'GOVERNANCE',
 ARRAY(SELECT key FROM public.permissions), true, 10),

('COMPANY_ADMIN', 'Company Admin', 'Runs day-to-day company administration without destructive or financial authority.',
 'GOVERNANCE',
 ARRAY['companies.read','companies.update','settings.read','settings.update','dashboard.read',
       'members.read','members.invite','members.manage','roles.read',
       'projects.read','sows.read','contracts.read','documents.read','documents.upload',
       'posts.read','posts.create','posts.update','posts.manage','audit.read'], false, 20),

('CONTRACT_MANAGER', 'Contract Manager', 'Owns the contract lifecycle: drafting, negotiation, approval and activation.',
 'DELIVERY',
 ARRAY['projects.read','projects.create','projects.update','projects.manage_roles',
       'sows.read','sows.create','sows.update','sows.approve',
       'contracts.read','contracts.create','contracts.update','contracts.send','contracts.approve',
       'documents.read','documents.upload','msas.read','msas.submit','members.read','dashboard.read'], false, 30),

('PROJECT_MANAGER', 'Project Manager', 'Owns delivery: project setup, staffing, timeline and progress reporting.',
 'DELIVERY',
 ARRAY['projects.read','projects.create','projects.update','projects.manage_roles',
       'sows.read','sows.create','sows.update',
       'contracts.read','documents.read','documents.upload','workforce.read','workforce.manage',
       'timesheets.read_any','leave.read_any','members.read','dashboard.read'], false, 40),

('FINANCE_MANAGER', 'Finance Manager', 'Owns billing, receivables, payables and financial reporting.',
 'FINANCE',
 ARRAY['invoices.read','invoices.create','invoices.update','invoices.submit','invoices.approve','invoices.cancel',
       'billing_runs.read','billing_runs.execute',
       'payments.read','payments.create','payments.update','payments.manage',
       'transactions.read','reconciliation.read','reconciliation.manage','payment_requests.create',
       'contracts.read','contracts.read_rates','projects.read','sows.read',
       'documents.read','documents.upload','dashboard.read','audit.read'], false, 50),

('TIMESHEET_MANAGER', 'Timesheet Manager', 'Reviews and approves timesheets for assigned staff.',
 'PEOPLE',
 ARRAY['timesheets.read_any','timesheets.create_any','timesheets.submit','timesheets.approve','timesheets.lock',
       'timesheets.read_rate','workforce.read','leave.read_any','leave.approve',
       'projects.read','contracts.read','members.read','dashboard.read'], false, 60),

('HR_MANAGER', 'HR Manager', 'Manages membership, roles, leave policy and workforce records.',
 'PEOPLE',
 ARRAY['members.read','members.invite','members.manage','roles.read',
       'workforce.read','workforce.manage',
       'leave.request','leave.read_any','leave.approve','leave.manage',
       'documents.read','documents.upload','dashboard.read'], false, 70),

('POST_MANAGER', 'Post Manager', 'Publishes and moderates the company presence on the professional network.',
 'PEOPLE',
 ARRAY['posts.read','posts.create','posts.update','posts.delete','posts.manage',
       'documents.read','documents.upload','members.read'], false, 80),

('ACCOUNTANT', 'Accountant', 'Day-to-day bookkeeping: records payments and reconciles transactions.',
 'FINANCE',
 ARRAY['invoices.read','invoices.create','invoices.update',
       'payments.read','payments.create','transactions.read',
       'reconciliation.read','reconciliation.manage',
       'contracts.read','contracts.read_rates','projects.read','documents.read','dashboard.read'], false, 90),

('EMPLOYEE', 'Employee', 'Baseline access for an individual contributor working under a contract.',
 'PEOPLE',
 ARRAY['profile.read','profile.update','sessions.read',
       'connections.read','connections.create','connections.respond',
       'posts.read','posts.create','posts.update','posts.delete',
       'messages.read','messages.send','reports.create',
       'contracts.read','projects.read','sows.read',
       'timesheets.create','timesheets.submit','leave.request',
       'documents.read','notifications.read','search.use','dashboard.read'], false, 100),

('CLIENT', 'Client', 'Counterparty-facing view: reviews contracts, invoices and documents shared with them.',
 'EXTERNAL',
 ARRAY['profile.read','profile.update',
       'contracts.read','sows.read','projects.read',
       'invoices.read','payments.read',
       'documents.read','messages.read','messages.send',
       'notifications.read','search.use'], false, 110),

('VIEWER', 'Viewer', 'Read-only access to dashboards, projects and reports.',
 'GOVERNANCE',
 ARRAY['profile.read','dashboard.read','projects.read','contracts.read','invoices.read',
       'documents.read','members.read','notifications.read','search.use'], false, 120)
ON CONFLICT (key) DO UPDATE
  SET name          = EXCLUDED.name,
      description   = EXCLUDED.description,
      permission_keys = EXCLUDED.permission_keys;

-- -----------------------------------------------------------------------------
-- Company bootstrap: copy role templates into a new company and grant the founder
-- SUPER_ADMIN. Idempotent, and safe to call once from the API in the same
-- transaction that created the company.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.bootstrap_company_roles(
  p_company_id uuid,
  p_founder_user_id uuid
) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  r record;
  v_role_id uuid;
BEGIN

  FOR r IN SELECT * FROM public.role_templates ORDER BY sort_order
  LOOP
    INSERT INTO public.company_roles (company_id, key, name, description, is_system, is_assignable, created_by)
    VALUES (p_company_id, r.key, r.name, r.description, true, r.is_assignable, p_founder_user_id)
    ON CONFLICT (company_id, key) DO UPDATE SET name = EXCLUDED.name
    RETURNING id INTO v_role_id;

    INSERT INTO public.role_permissions (role_id, permission_key, granted_by)
    SELECT v_role_id, k, p_founder_user_id
      FROM unnest(r.permission_keys) AS k
      JOIN public.permissions p ON p.key = k
    ON CONFLICT DO NOTHING;
  END LOOP;

  SELECT id INTO v_role_id
    FROM public.company_roles WHERE company_id = p_company_id AND key = 'SUPER_ADMIN';

  INSERT INTO public.company_memberships
    (company_id, user_id, role_id, status, joined_at)
  VALUES (p_company_id, p_founder_user_id, v_role_id, 'ACTIVE', now())
  ON CONFLICT (company_id, user_id) DO UPDATE
    SET role_id = v_role_id, status = 'ACTIVE', joined_at = now();
END
$$;

COMMENT ON FUNCTION app.bootstrap_company_roles(uuid, uuid) IS
  'Copies role templates into a new company and makes the founder its SUPER_ADMIN.';

-- -----------------------------------------------------------------------------
-- Feature flags (platform defaults; per-company overrides created in the UI)
-- -----------------------------------------------------------------------------
INSERT INTO platform.feature_flags (key, description, enabled, rollout_pct, config) VALUES
('ai.assistant',            'AI assistant and natural-language business queries',        true,  100, '{}'::jsonb),
('ai.contract_intelligence','Contract summarisation, risk scoring and comparison',        true,  100, '{}'::jsonb),
('ai.document_intelligence','OCR and structured extraction for uploaded documents',        true,  100, '{"max_file_mb":25}'::jsonb),
('ai.project_intelligence', 'Project health, schedule and budget risk analysis',          true,  100, '{}'::jsonb),
('ai.workforce_intelligence','Skill matching and staffing recommendations',              true,  100, '{}'::jsonb),
('ai.financial_intelligence','Cash-flow forecasting and payment prediction',             true,   50, '{}'::jsonb),
('ai.automations',          'Natural-language automation builder',                        true,   25, '{}'::jsonb),
('ai.agents',               'Agentic workflows with approval gates',                     false,   0, '{}'::jsonb),
('ai.timesheet_import',     'Import timesheets from PDF/Excel/image uploads',             true,  100, '{}'::jsonb),
('ai.w9_intelligence',      'W-9 extraction and validation',                            true,  100, '{}'::jsonb),
('payments.plaid',          'Bank connection and transaction synchronisation',           false,   0, '{}'::jsonb),
('payments.reconciliation', 'AI-assisted payment reconciliation',                        true,   50, '{"auto_accept_confidence":0.97}'::jsonb),
('payments.initiate',       'Initiating money movement through a processor',             false,   0, '{}'::jsonb),
('messaging.groups',        'Group conversations',                                       false,   0, '{}'::jsonb),
('mfa.enforcement',         'Mandatory multi-factor authentication',                    false,   0, '{}'::jsonb),
('multi_hop_contracts',     'Multi-hop subcontracting chains',                           true,  100, '{}'::jsonb),
('search.semantic',         'Vector-assisted global search (pgvector)',                  false,   0, '{}'::jsonb),
('platform.admin_console',  'Platform administration surface',                          false,   0, '{}'::jsonb)
ON CONFLICT (key) DO UPDATE
  SET description = EXCLUDED.description,
      config      = EXCLUDED.config;

-- -----------------------------------------------------------------------------
-- Reference skills (seed a small taxonomy; extended by admins at runtime)
-- -----------------------------------------------------------------------------
INSERT INTO public.skills (name, category) VALUES
('Java',            'Programming'), ('Python',        'Programming'), ('TypeScript',   'Programming'),
('C#',              'Programming'), ('Go',            'Programming'), ('Rust',         'Programming'),
('SQL',             'Data'),        ('PostgreSQL',    'Data'),        ('Data Engineering','Data'),
('Machine Learning','AI'),         ('NLP',           'AI'),          ('Computer Vision','AI'),
('React',           'Frontend'),   ('Next.js',       'Frontend'),    ('TypeScript Frontend','Frontend'),
('UI/UX Design',    'Design'),     ('Product Management','Business'),('Project Management','Business'),
('Financial Analysis','Finance'),  ('Accounting',    'Finance'),     ('Compliance',    'Finance'),
('Recruitment',     'People'),     ('Technical Writing','Content'), ('Sales',         'Business'),
('DevOps',          'Infrastructure'), ('AWS',       'Infrastructure'), ('Azure',      'Infrastructure'),
('Kubernetes',      'Infrastructure'), ('Security',   'Infrastructure'), ('QA Engineering','Engineering'),
('Business Analysis','Business'), ('Vendor Management','Business'),  ('Contract Management','Business')
ON CONFLICT (name) DO NOTHING;

-- -----------------------------------------------------------------------------
-- AI provider registry (no credentials — activation requires a secrets entry)
-- -----------------------------------------------------------------------------
INSERT INTO public.ai_providers
  (key, display_name, provider_type, base_url, model_name, context_window, max_output_tokens,
   cost_per_1k_input_cents, cost_per_1k_output_cents, supports_functions, supports_vision,
   is_enabled, priority)
VALUES
('openai',    'OpenAI',    'CHAT',     'https://api.openai.com/v1',
 'gpt-4o-mini', 128000, 16384, 0.000150, 0.000600, true,  true,  true,  10),
('anthropic', 'Anthropic', 'CHAT',     'https://api.anthropic.com/v1',
 'claude-3-5-sonnet-latest', 200000, 8192, 0.003000, 0.015000, true, true, true,  20),
('gemini',    'Google Gemini', 'CHAT', 'https://generativelanguage.googleapis.com/v1beta',
 'gemini-1.5-pro', 1000000, 8192, 0.001250, 0.005000, true, true, true, 30),
('local',     'Self-hosted', 'CHAT',    'http://localhost:8080/v1',
 'llama-3.1-70b', 32768, 4096, 0.000000, 0.000000, true, false, false, 50),
('openai_embedding',   'OpenAI Embeddings',   'EMBEDDING', 'https://api.openai.com/v1',
 'text-embedding-3-small', 8191, 0, 0.000020, 0.0, false, false, true, 10),
('local_embedding',    'Local Embeddings',    'EMBEDDING', 'http://localhost:8080/v1',
 'nomic-embed-text', 8192, 0, 0.0, 0.0, false, false, false, 50)
ON CONFLICT (key) DO UPDATE
  SET display_name = EXCLUDED.display_name,
      model_name   = EXCLUDED.model_name,
      context_window = EXCLUDED.context_window;

-- -----------------------------------------------------------------------------
-- Column-level protection for the reference tables that the API role reads
-- -----------------------------------------------------------------------------
ALTER TABLE public.role_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS role_templates_read ON public.role_templates;
CREATE POLICY role_templates_read ON public.role_templates FOR SELECT
  USING (app.current_user_id() IS NOT NULL);

GRANT SELECT ON public.role_templates TO mytrakin_api, mytrakin_readonly;
GRANT SELECT ON public.role_templates TO mytrakin_worker;
