/**
 * W-9 identity: shared field definitions, validation and masking.
 *
 * The messages here mirror the server validator (`validate_w9_identity` in
 * app/services/companies.py) so the review screen can show field-level errors
 * before submitting — but the server always has the final word, and a 409
 * response carries the same `details.fields` shape this module produces.
 *
 * Sources are honest: without an OCR backend wired, every field is
 * user-entered. When extraction exists, callers pass `source: 'extracted'`
 * with a confidence, and the review screen renders it as such.
 */

export type W9FieldKey =
  | 'legal_name'
  | 'tax_classification'
  | 'tin_type'
  | 'tin_last4'
  | 'address_line1'
  | 'city'
  | 'region'
  | 'postal_code'

export interface W9FormValues {
  legal_name: string
  tax_classification: string
  tin_type: string
  tin_last4: string
  address_line1: string
  city: string
  region: string
  postal_code: string
}

export const TAX_CLASSIFICATIONS: { value: string; label: string }[] = [
  { value: 'INDIVIDUAL_SOLE_PROPRIETOR', label: 'Individual / sole proprietor' },
  { value: 'C_CORPORATION', label: 'C corporation' },
  { value: 'S_CORPORATION', label: 'S corporation' },
  { value: 'PARTNERSHIP', label: 'Partnership' },
  { value: 'TRUST_ESTATE', label: 'Trust / estate' },
  { value: 'LLC_SOLE_PROPRIETORSHIP', label: 'LLC — sole proprietorship (disregarded)' },
  { value: 'LLC_C_CORP', label: 'LLC — C corporation' },
  { value: 'LLC_S_CORP', label: 'LLC — S corporation' },
  { value: 'LLC_PARTNERSHIP', label: 'LLC — partnership' },
  { value: 'OTHER', label: 'Other (see W-9 instructions)' },
]

export const TIN_TYPES: { value: string; label: string }[] = [
  { value: 'EIN', label: 'Employer ID (EIN)' },
  { value: 'SSN', label: 'Social Security (SSN)' },
  { value: 'ITIN', label: 'Individual Taxpayer ID (ITIN)' },
]

export function classificationLabel(value: string): string {
  return TAX_CLASSIFICATIONS.find((option) => option.value === value)?.label ?? value
}

export function tinTypeLabel(value: string): string {
  return TIN_TYPES.find((option) => option.value === value)?.label ?? value
}

/** Masked display: only the last four ever leave the server. */
export function maskTin(last4: string): string {
  return `••••${last4}`
}

export function validateW9(
  values: W9FormValues,
  countryCode = 'US',
): Partial<Record<W9FieldKey, string>> {
  const errors: Partial<Record<W9FieldKey, string>> = {}
  if (values.legal_name.trim().length < 2) {
    errors.legal_name = 'Line 1 — Name is required.'
  }
  if (!values.tax_classification) {
    errors.tax_classification = 'Line 3a — Federal tax classification is required.'
  } else if (!TAX_CLASSIFICATIONS.some((option) => option.value === values.tax_classification)) {
    errors.tax_classification = 'Line 3a — Select a valid federal tax classification.'
  }
  if (!TIN_TYPES.some((option) => option.value === values.tin_type)) {
    errors.tin_type = 'Part I — Select the TIN type (EIN, SSN or ITIN).'
  }
  if (!/^\d{4}$/.test(values.tin_last4.trim())) {
    errors.tin_last4 = 'Part I — Enter the last 4 digits of the TIN.'
  }
  if (!values.address_line1.trim()) {
    errors.address_line1 = 'Line 5 — Street address is required.'
  }
  if (!values.city.trim()) {
    errors.city = 'Line 6 — City is required.'
  }
  if (!values.region.trim()) {
    errors.region = 'Line 6 — State is required.'
  }
  if (!values.postal_code.trim()) {
    errors.postal_code = 'Line 6 — ZIP code is required.'
  } else if (countryCode.trim().toUpperCase() === 'US' && !/^\d{5}(-\d{4})?$/.test(values.postal_code.trim())) {
    errors.postal_code = 'Line 6 — ZIP code format is not valid (e.g. 94107).'
  }
  return errors
}

export interface W9ReviewField {
  key: W9FieldKey
  line: string
  label: string
  value: string
  /** Where the value came from. Manual until an extraction backend exists. */
  source: 'entered'
  /** Present only for extracted values. */
  confidence?: number
}

/** Rows for the review screen, in W-9 order, with the TIN masked. */
export function w9ReviewFields(values: W9FormValues): W9ReviewField[] {
  return [
    { key: 'legal_name', line: 'Line 1', label: 'Name', value: values.legal_name.trim(), source: 'entered' },
    {
      key: 'tax_classification',
      line: 'Line 3a',
      label: 'Federal tax classification',
      value: classificationLabel(values.tax_classification),
      source: 'entered',
    },
    {
      key: 'tin_type',
      line: 'Part I',
      label: 'TIN type',
      value: tinTypeLabel(values.tin_type),
      source: 'entered',
    },
    {
      key: 'tin_last4',
      line: 'Part I',
      label: 'TIN',
      value: maskTin(values.tin_last4.trim()),
      source: 'entered',
    },
    { key: 'address_line1', line: 'Line 5', label: 'Street address', value: values.address_line1.trim(), source: 'entered' },
    { key: 'city', line: 'Line 6', label: 'City', value: values.city.trim(), source: 'entered' },
    { key: 'region', line: 'Line 6', label: 'State', value: values.region.trim(), source: 'entered' },
    { key: 'postal_code', line: 'Line 6', label: 'ZIP', value: values.postal_code.trim(), source: 'entered' },
  ]
}
