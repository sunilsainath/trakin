'use client'

import * as React from 'react'

import { Button, Dialog, Input, Label, Textarea } from '@/components/ui'
import { errorMessage } from '@/components/query'

/**
 * Confirmation for an irreversible action that the API requires a reason for.
 *
 * Archive, cancel, terminate, close and deactivate all write a `reason` into the
 * audit log, and several of them take it as a required query parameter. The
 * dialog states the consequence in the description rather than asking a
 * content-free "are you sure?", and the field is validated against the same
 * minimum length the API enforces so the request is not refused after the user
 * has already confirmed the consequence.
 */
export function ReasonDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  label = 'Reason',
  hint,
  minLength = 3,
  dateLabel,
  dateHint,
  busy,
  error,
  tone = 'danger',
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  label?: string
  hint?: string
  minLength?: number
  /** When set, an optional date field is shown (e.g. a termination effective date). */
  dateLabel?: string
  dateHint?: string
  busy?: boolean
  error?: unknown
  tone?: 'danger' | 'primary'
  onConfirm: (reason: string, date: string | null) => void
}) {
  const [reason, setReason] = React.useState('')
  const [touched, setTouched] = React.useState(false)
  const [date, setDate] = React.useState('')

  // Reset between invocations so a previous reason is never resubmitted.
  React.useEffect(() => {
    if (open) {
      setReason('')
      setTouched(false)
      setDate('')
    }
  }, [open, title])

  const invalid = touched && reason.trim().length < minLength

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            loading={busy}
            onClick={() => {
              setTouched(true)
              if (reason.trim().length < minLength) return
              onConfirm(reason.trim(), date || null)
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-1.5">
        <Label htmlFor="reason-dialog">
          {label}
          <span className="ml-0.5 text-danger">*</span>
        </Label>
        <Textarea
          id="reason-dialog"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          onBlur={() => setTouched(true)}
          rows={3}
          disabled={busy}
          placeholder={hint}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? 'reason-dialog-error' : undefined}
        />
        {invalid ? (
          <p
            id="reason-dialog-error"
            role="alert"
            className="text-xs font-medium text-danger"
          >
            Enter at least {minLength} characters. This reason is recorded in the
            audit log and cannot be edited later.
          </p>
        ) : null}
        {dateLabel ? (
          <div className="space-y-1.5 pt-1">
            <Label htmlFor="reason-dialog-date">{dateLabel}</Label>
            <Input
              id="reason-dialog-date"
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
              disabled={busy}
            />
            {dateHint ? (
              <p className="text-xs text-muted-foreground">{dateHint}</p>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}

/**
 * Confirmation for an action that takes an optional note rather than a reason.
 *
 * Used for submit, send, approve and the other forward transitions: they change
 * state irreversibly, so they are still confirmed, but the API has no required
 * reason field for them.
 */
export function NoteDialog({
  open,
  onOpenChange,
  title,
  description,
  label = 'Note',
  placeholder,
  confirmLabel = 'Confirm',
  busy,
  error,
  tone = 'primary',
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  label?: string
  placeholder?: string
  confirmLabel?: string
  busy?: boolean
  error?: unknown
  tone?: 'primary' | 'danger'
  onConfirm: (notes: string | null) => void
}) {
  const [notes, setNotes] = React.useState('')

  React.useEffect(() => {
    if (open) setNotes('')
  }, [open, title])

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={tone}
            loading={busy}
            onClick={() => onConfirm(notes.trim() || null)}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-1.5">
        <Label htmlFor="note-dialog">{label}</Label>
        <Textarea
          id="note-dialog"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={3}
          disabled={busy}
          placeholder={placeholder}
        />
        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}

/**
 * Confirmation for an action with no fields at all.
 *
 * Used where the consequence needs stating but no input is required, such as
 * "send this contract to the counterparty".
 */
export function ConfirmOnlyDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = 'Confirm',
  busy,
  error,
  tone = 'primary',
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  busy?: boolean
  error?: unknown
  tone?: 'primary' | 'danger'
  onConfirm: () => void
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant={tone} loading={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {error ? (
        <p role="alert" className="text-xs font-medium text-danger">
          {errorMessage(error)}
        </p>
      ) : null}
    </Dialog>
  )
}

/**
 * Confirmation for an approval decision.
 *
 * Approving is not destructive, so the dialog is neutral in tone, but it still
 * confirms: an approval here can lock a timesheet or release an invoice, and it
 * is recorded against the approver's identity with segregation-of-duties rules
 * applied server-side.
 */
export function DecisionDialog({
  open,
  onOpenChange,
  decision,
  title,
  description,
  confirmLabel,
  notesLabel = 'Notes',
  busy,
  error,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  decision: 'APPROVED' | 'REJECTED'
  title: string
  description: string
  confirmLabel: string
  notesLabel?: string
  busy?: boolean
  error?: unknown
  onConfirm: (notes: string | null) => void
}) {
  const [notes, setNotes] = React.useState('')

  React.useEffect(() => {
    if (open) setNotes('')
  }, [open, title])

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return
        onOpenChange(next)
      }}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={decision === 'APPROVED' ? 'success' : 'danger'}
            loading={busy}
            onClick={() => onConfirm(notes.trim() || null)}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-1.5">
        <Label htmlFor="decision-notes">{notesLabel}</Label>
        <Textarea
          id="decision-notes"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
          rows={3}
          disabled={busy}
        />
        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {errorMessage(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  )
}

/**
 * A labelled single field inside a dialog, for prompts that take exactly one
 * input such as a new end date or a renewal term.
 */
export function DialogField({
  id,
  label,
  hint,
  error,
  required,
  children,
}: {
  id: string
  label: string
  hint?: string
  error?: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        {label}
        {required ? <span className="ml-0.5 text-danger">*</span> : null}
      </Label>
      {children}
      {error ? (
        <p role="alert" className="text-xs font-medium text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}