-- =============================================================================
-- MyTrakin :: 0028_company_tax_classification.sql
--
-- Company founding collects the W-9 identity fields the API validates and the
-- review screen displays: the Line 3a federal tax classification (including
-- the Line 3b LLC letter as distinct values), the TIN type, and the TIN
-- last-4. The full TIN is never stored as structured data -- it lives only
-- inside the W-9 scan in private storage -- so last-4 plus type is the
-- complete structured record.
-- =============================================================================

ALTER TABLE public.companies
  ADD COLUMN IF NOT EXISTS tax_classification text;

ALTER TABLE public.companies
  DROP CONSTRAINT IF EXISTS companies_tax_classification_check;

ALTER TABLE public.companies
  ADD CONSTRAINT companies_tax_classification_check
  CHECK (
    tax_classification IS NULL OR tax_classification IN (
      'INDIVIDUAL_SOLE_PROPRIETOR',
      'C_CORPORATION',
      'S_CORPORATION',
      'PARTNERSHIP',
      'TRUST_ESTATE',
      'LLC_SOLE_PROPRIETORSHIP',
      'LLC_C_CORP',
      'LLC_S_CORP',
      'LLC_PARTNERSHIP',
      'OTHER'
    )
  );
