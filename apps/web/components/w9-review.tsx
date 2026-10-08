'use client'

import { Badge } from '@/components/ui'
import {
  w9ReviewFields,
  type W9FieldKey,
  type W9FormValues,
} from '@/lib/w9'

export interface W9IntakeState {
  title: string
  status: string
  scan_status: string | null
  extraction_state: string | null
}

/**
 * W-9 review: the document state plus every identity field in W-9 order.
 *
 * Each field shows its value, where the value came from, and any validation
 * error. Sources are honest: without an extraction backend every field is
 * user-entered, and a pending pipeline stage is labelled pending — the
 * screen never presents unextracted data as extracted.
 */
export function W9Review({
  values,
  errors,
  fileName,
  intake,
}: {
  values: W9FormValues
  errors: Partial<Record<W9FieldKey, string>>
  fileName: string | null
  intake: W9IntakeState | null
}) {
  const fields = w9ReviewFields(values)

  return (
    <div className="space-y-4">
      <div className="rounded-md border border-border p-3">
        <p className="text-sm font-medium">W-9 document</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {fileName ?? 'No file attached'}
        </p>
        {intake ? (
          <dl className="mt-2 grid grid-cols-3 gap-2 text-xs">
            <div>
              <dt className="text-subtle-foreground">Storage</dt>
              <dd className="font-medium">{intake.status.toLowerCase()}</dd>
            </div>
            <div>
              <dt className="text-subtle-foreground">Malware scan</dt>
              <dd className="font-medium">{(intake.scan_status ?? 'PENDING').toLowerCase()}</dd>
            </div>
            <div>
              <dt className="text-subtle-foreground">Text extraction</dt>
              <dd className="font-medium">{(intake.extraction_state ?? 'PENDING').toLowerCase()}</dd>
            </div>
          </dl>
        ) : null}
        <p className="mt-2 text-2xs text-subtle-foreground">
          No automated extraction is available for this upload, so every field
          below was entered by you and will be validated — not auto-filled.
        </p>
      </div>

      <dl className="divide-y divide-border/60 rounded-md border border-border">
        {fields.map((field) => {
          const problem = errors[field.key]
          return (
            <div key={field.key} className="space-y-0.5 px-3 py-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <dt className="text-xs text-muted-foreground">
                  {field.line} · {field.label}
                </dt>
                <Badge tone={problem ? 'danger' : 'outline'}>
                  {problem ? 'Needs attention' : 'You entered'}
                </Badge>
              </div>
              <dd className="truncate text-sm font-medium">{field.value || '—'}</dd>
              {problem ? (
                <dd role="alert" className="text-xs font-medium text-danger">
                  {problem}
                </dd>
              ) : null}
            </div>
          )
        })}
      </dl>
    </div>
  )
}
