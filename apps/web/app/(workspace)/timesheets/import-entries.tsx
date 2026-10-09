'use client'

import * as React from 'react'
import { Upload } from 'lucide-react'

import { getAccessToken } from '@/lib/api'
import { Button, Dialog, EmptyState } from '@/components/ui'
import { notifyError, notifySuccess } from '@/components/toast'

interface PreviewEntry {
  entry_date: string
  hours: string
  work_description: string
  confidence: number
}

/**
 * Import time from a file.
 *
 * The file is sent to the API, which returns the rows it read; nothing is
 * written yet. The user reviews the preview, unchecks anything wrong, and only
 * the confirmed rows become entries. CSV/TSV/text is read deterministically;
 * scans need document extraction and answer with a typed error otherwise.
 */
export function ImportEntries({ timesheetId, onImported }: { timesheetId: string; onImported: () => void }) {
  const [open, setOpen] = React.useState(false)
  const [rows, setRows] = React.useState<PreviewEntry[] | null>(null)
  const [included, setIncluded] = React.useState<boolean[]>([])
  const [notice, setNotice] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)

  const reset = () => {
    setRows(null)
    setIncluded([])
    setNotice(null)
  }

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setBusy(true)
    setNotice(null)
    try {
      const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
      const token = await getAccessToken()
      const form = new FormData()
      form.append('file', file)
      const response = await fetch(`${base}/api/v1/timesheets/${timesheetId}/import`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
        cache: 'no-store',
      })
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as {
          error?: { message?: string }
        }
        throw new Error(payload.error?.message ?? 'That file could not be read.')
      }
      const body = (await response.json()) as { entries: PreviewEntry[] }
      setRows(body.entries)
      setIncluded(body.entries.map(() => true))
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'That file could not be read.')
    } finally {
      setBusy(false)
    }
  }

  const confirm = async () => {
    if (!rows) return
    const entries = rows
      .filter((_, index) => included[index])
      .map((row) => ({
        entry_date: row.entry_date,
        hours: row.hours,
        work_description: row.work_description,
        is_billable: true,
        source: 'AI_IMPORT',
      }))
    if (entries.length === 0) {
      setNotice('Select at least one row to import.')
      return
    }
    setBusy(true)
    try {
      const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
      const token = await getAccessToken()
      const response = await fetch(`${base}/api/v1/timesheets/${timesheetId}/import/confirm`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ entries }),
        cache: 'no-store',
      })
      if (!response.ok) throw new Error('The import could not be saved.')
      notifySuccess(`${entries.length} entr${entries.length === 1 ? 'y' : 'ies'} imported.`)
      setOpen(false)
      reset()
      onImported()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>
        <Upload aria-hidden />
        Import from file
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) reset()
        }}
        title="Import time from a file"
        description="Upload a CSV, TSV or text file. Review what was read before anything is written — the model never writes entries on its own."
      >
        <div className="space-y-4">
          {notice ? (
            <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-foreground">
              {notice}
            </p>
          ) : null}

          <input
            type="file"
            accept=".csv,.tsv,.txt,text/csv,text/plain"
            disabled={busy}
            onChange={(event) => void upload(event)}
            className="text-sm"
            aria-label="Timesheet file"
          />

          {rows === null ? (
            <p className="text-xs text-muted-foreground">
              Expected columns: a date and hours (optionally a description). Example header:
              <code className="ml-1 rounded bg-muted px-1">date,hours,description</code>.
            </p>
          ) : rows.length === 0 ? (
            <EmptyState title="No rows found" description="The file did not contain dated rows with hours." />
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-muted-foreground">
                {rows.length} row{rows.length === 1 ? '' : 's'} read. Uncheck anything that should not be imported.
              </p>
              <ul className="space-y-1.5">
                {rows.map((row, index) => (
                  <li
                    key={`${row.entry_date}-${index}`}
                    className="flex items-center gap-3 rounded-md border border-border px-3 py-2 text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={included[index] ?? false}
                      onChange={(event) =>
                        setIncluded((previous) => {
                          const next = [...previous]
                          next[index] = event.target.checked
                          return next
                        })
                      }
                      className="size-4 accent-primary"
                      aria-label={`Include ${row.entry_date}`}
                    />
                    <span className="w-24 shrink-0 font-mono text-xs">{row.entry_date}</span>
                    <span className="w-16 shrink-0">{row.hours}h</span>
                    <span className="min-w-0 flex-1 truncate text-muted-foreground">
                      {row.work_description || '—'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void confirm()} loading={busy} disabled={!rows || rows.length === 0}>
              Import selected
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  )
}
