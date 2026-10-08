import { describe, expect, it } from 'vitest'

import { csvCell, csvFilename, toCsv, type CsvColumn } from '@/lib/csv'

interface Row {
  public_id: string
  name: string
  amount: string | null
  count: number
}

const columns: CsvColumn<Row>[] = [
  { header: 'Project ID', value: (row) => row.public_id },
  { header: 'Name', value: (row) => row.name },
  { header: 'Amount', value: (row) => row.amount },
  { header: 'Open roles', value: (row) => row.count },
]

const rows: Row[] = [
  { public_id: 'P01H8KM2Q', name: 'Acme rollout', amount: '1200.00', count: 2 },
  { public_id: 'P01H8KM3R', name: 'Acme, Inc "West"', amount: null, count: 0 },
]

describe('csvCell', () => {
  it('leaves a plain value alone', () => {
    expect(csvCell('P01H8KM2Q')).toBe('P01H8KM2Q')
    expect(csvCell(0)).toBe('0')
  })

  it('renders a missing value as an empty cell, not the text null', () => {
    expect(csvCell(null)).toBe('')
    expect(csvCell(undefined)).toBe('')
  })

  it('quotes a value containing a delimiter', () => {
    expect(csvCell('Acme, Inc')).toBe('"Acme, Inc"')
  })

  it('quotes and doubles embedded quotes', () => {
    // RFC 4180: a quote inside a quoted field is doubled.
    expect(csvCell('Acme, Inc "West"')).toBe('"Acme, Inc ""West"""')
  })

  it('quotes a value containing a newline', () => {
    expect(csvCell('line one\nline two')).toBe('"line one\nline two"')
  })
})

describe('toCsv', () => {
  it('emits a header row and one line per record', () => {
    const csv = toCsv(rows, columns)
    expect(csv.split('\r\n')).toHaveLength(3)
    expect(csv.split('\r\n')[0]).toBe('Project ID,Name,Amount,Open roles')
  })

  it('preserves a comma inside a value without breaking the row', () => {
    const [, , second] = toCsv(rows, columns).split('\r\n')
    expect(second).toBe('P01H8KM3R,"Acme, Inc ""West""",,0')
  })

  it('emits an empty cell for a missing value', () => {
    expect(toCsv([rows[1]!], columns)).toContain('P01H8KM3R,"Acme, Inc ""West""",,0')
  })

  it('still produces a header when there are no rows', () => {
    expect(toCsv([], columns)).toBe('Project ID,Name,Amount,Open roles')
  })

  it('uses CRLF line endings so a spreadsheet opens it correctly', () => {
    expect(toCsv(rows, columns)).toContain('\r\n')
  })
})

describe('csvFilename', () => {
  it('stamps the file with a sortable date', () => {
    expect(csvFilename('projects', new Date(2026, 1, 14))).toBe('projects-2026-02-14.csv')
  })

  it('defaults to today', () => {
    expect(csvFilename('invoices')).toMatch(/^invoices-\d{4}-\d{2}-\d{2}\.csv$/)
  })
})