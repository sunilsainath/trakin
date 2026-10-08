/**
 * CSV export for the current page of a list.
 *
 * Exports exactly what the user is looking at, and says so in the file name, so
 * an exported page is never mistaken for the whole table. Cell escaping follows
 * RFC 4180 because the values are user-authored: a project named
 * `Acme, Inc "West"` must not break the row.
 */

/** One column: a header and how to read the value from a row. */
export interface CsvColumn<T> {
  header: string
  value: (row: T) => string | number | null | undefined
}

/**
 * Quote a single CSV field.
 *
 * A field is quoted when it contains a delimiter, a quote or a newline. Quotes
 * inside the value are doubled. A null or undefined becomes an empty cell rather
 * than the text "null".
 */
export function csvCell(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return ''

  const text = String(value)
  const needsQuoting = /[",\r\n]/.test(text)
  if (!needsQuoting) return text

  return `"${text.replace(/"/g, '""')}"`
}

/**
 * Build a complete CSV document from rows and columns.
 *
 * Line endings are CRLF as the RFC specifies, which is also what Excel expects,
 * so a double click opens a well-formed sheet rather than one long line.
 */
export function toCsv<T>(rows: T[], columns: CsvColumn<T>[]): string {
  const header = columns.map((column) => csvCell(column.header)).join(',')
  const body = rows.map((row) => columns.map((column) => csvCell(column.value(row))).join(','))

  return [header, ...body].join('\r\n')
}

/**
 * Trigger a browser download of a CSV string.
 *
 * Deliberately uses a Blob and an object URL rather than a data URI: a page of
 * financial rows can exceed the data-URI length limit, and the CSP in
 * `next.config.mjs` allows `blob:` for images only. No DOM node is left behind.
 */
export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)

  const link = document.createElement('a')
  link.href = url
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)

  URL.revokeObjectURL(url)
}

/** `projects-2026-02-14.csv` — sortable and self-describing. */
export function csvFilename(base: string, when = new Date()): string {
  const stamp = [
    when.getFullYear(),
    String(when.getMonth() + 1).padStart(2, '0'),
    String(when.getDate()).padStart(2, '0'),
  ].join('-')

  return `${base}-${stamp}.csv`
}