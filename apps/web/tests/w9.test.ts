import { describe, expect, it } from 'vitest'

import {
  maskTin,
  validateW9,
  w9ReviewFields,
  type W9FormValues,
} from '@/lib/w9'

const VALID: W9FormValues = {
  legal_name: 'ABC Technologies LLC',
  tax_classification: 'LLC_S_CORP',
  tin_type: 'EIN',
  tin_last4: '4821',
  address_line1: '548 Market Street',
  city: 'San Francisco',
  region: 'CA',
  postal_code: '94107',
}

describe('validateW9', () => {
  it('accepts a complete return', () => {
    expect(validateW9(VALID)).toEqual({})
  })

  it('names each failing line instead of a single rejection', () => {
    const errors = validateW9({
      ...VALID,
      legal_name: '',
      tax_classification: 'KINGDOM',
      tin_type: 'PASSPORT',
      tin_last4: '12',
      address_line1: '',
      city: '',
      region: '',
      postal_code: '9410',
    })
    expect(Object.keys(errors).sort()).toEqual(
      [
        'address_line1',
        'city',
        'legal_name',
        'postal_code',
        'region',
        'tax_classification',
        'tin_last4',
        'tin_type',
      ].sort(),
    )
    expect(errors.legal_name).toMatch(/^Line 1/)
    expect(errors.tin_last4).toMatch(/^Part I/)
  })

  it('accepts non-US postal codes', () => {
    expect(
      validateW9({ ...VALID, region: 'Karnataka', postal_code: '560001' }, 'IN'),
    ).toEqual({})
  })
})

describe('maskTin', () => {
  it('never returns more than the last four', () => {
    expect(maskTin('4821')).toBe('••••4821')
  })
})

describe('w9ReviewFields', () => {
  it('lists W-9 lines in order with the TIN masked', () => {
    const fields = w9ReviewFields(VALID)
    expect(fields.map((field) => field.key)).toEqual([
      'legal_name',
      'tax_classification',
      'tin_type',
      'tin_last4',
      'address_line1',
      'city',
      'region',
      'postal_code',
    ])
    expect(fields.find((field) => field.key === 'tin_last4')?.value).toBe('••••4821')
    expect(fields.every((field) => field.source === 'entered')).toBe(true)
  })
})
