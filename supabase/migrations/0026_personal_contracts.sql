-- =============================================================================
-- MyTrakin :: 0026_personal_contracts.sql
--
-- CODE Phase 3: binding engagements for individual (company-less) work.
--
-- Shape: contracts.company_id becomes nullable (NULL = personal, owned by
-- created_by) plus a responded_by capture column so acceptance records who
-- decided, not just when.
--
-- RLS: every contract-family policy gains an owner branch for personal rows.
-- Reads for child tables already funnel through app.can_view_contract, so one
-- branch there covers them; writes get an explicit parent-personal branch.
-- Company rows evaluate exactly as before.
-- =============================================================================

ALTER TABLE public.contracts ALTER COLUMN company_id DROP NOT NULL;

ALTER TABLE public.contracts
  ADD COLUMN IF NOT EXISTS responded_by uuid NULL REFERENCES public.users(id);

CREATE OR REPLACE FUNCTION app.can_view_contract(p_contract_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO 'public', 'app', 'pg_temp'
AS $function$
  SELECT EXISTS (
    SELECT 1 FROM public.contracts c
     WHERE c.id = p_contract_id AND c.deleted_at IS NULL
       AND (
         app.row_company_read(c.company_id, 'contracts.read')
         OR (c.counterparty_company_id IS NOT NULL AND app.is_member(c.counterparty_company_id)
             AND app.has_permission(c.counterparty_company_id, 'contracts.read'))
         OR c.counterparty_user_id = app.current_user_id()
         OR (c.company_id IS NULL AND c.created_by = app.current_user_id())
         OR EXISTS (SELECT 1 FROM public.contract_parties cp
                     WHERE cp.contract_id = c.id
                       AND ((cp.party_company_id IS NOT NULL AND app.is_member(cp.party_company_id))
                            OR cp.party_user_id = app.current_user_id()))
       )
  );
$function$;

DROP POLICY IF EXISTS contracts_write ON public.contracts;
CREATE POLICY contracts_write ON public.contracts
  FOR ALL TO public
  USING (
    app.row_company_read(company_id, 'contracts.update')
    OR (counterparty_user_id = app.current_user_id()
        AND status IN ('SENT', 'PENDING_ACCEPTANCE'))
    OR (company_id IS NULL AND created_by = app.current_user_id())
  )
  WITH CHECK (
    app.row_company_read(company_id, 'contracts.create')
    OR (company_id IS NULL AND created_by = app.current_user_id())
  );

DROP POLICY IF EXISTS contract_roles_write ON public.contract_roles;
CREATE POLICY contract_roles_write ON public.contract_roles
  FOR ALL TO public
  USING (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_roles.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_roles.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  )
  WITH CHECK (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_roles.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_roles.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  );

DROP POLICY IF EXISTS contract_line_items_write ON public.contract_line_items;
CREATE POLICY contract_line_items_write ON public.contract_line_items
  FOR ALL TO public
  USING (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_line_items.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_line_items.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  )
  WITH CHECK (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_line_items.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_line_items.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  );

DROP POLICY IF EXISTS contract_parties_write ON public.contract_parties;
CREATE POLICY contract_parties_write ON public.contract_parties
  FOR ALL TO public
  USING (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_parties.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_parties.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  )
  WITH CHECK (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_parties.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_parties.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  );

DROP POLICY IF EXISTS contract_steps_write ON public.contract_approval_steps;
CREATE POLICY contract_steps_write ON public.contract_approval_steps
  FOR ALL TO public
  USING (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_approval_steps.contract_id
               AND app.row_company_read(c.company_id, 'contracts.approve'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_approval_steps.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  )
  WITH CHECK (
    EXISTS (SELECT 1 FROM public.contracts c
             WHERE c.id = contract_approval_steps.contract_id
               AND app.row_company_read(c.company_id, 'contracts.update'))
    OR EXISTS (SELECT 1 FROM public.contracts c
                WHERE c.id = contract_approval_steps.contract_id
                  AND c.company_id IS NULL AND c.created_by = app.current_user_id())
  );
