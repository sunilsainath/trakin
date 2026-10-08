'use client'

import * as React from 'react'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm, type FieldValues, type Resolver } from 'react-hook-form'

import { FormDescription, FormField, FormItem, FormLabel, FormMessage } from '@/components/ui/form'
import { Button, Input, Label, Select, Textarea } from '@/components/ui'

/* -------------------------------------------------------------------------- */
/* Field specification                                                        */
/* -------------------------------------------------------------------------- */

export interface FieldSpec<TFieldValues extends FieldValues> {
  name: keyof TFieldValues & string
  label: string
  required?: boolean
  placeholder?: string
  hint?: string
  disabled?: boolean
  type?: 'text' | 'textarea' | 'select' | 'date' | 'checkbox'
  inputType?: React.HTMLInputTypeAttribute
  step?: string | number
  min?: string | number
  max?: string | number
  rows?: number
  options?: readonly string[]
}

/* -------------------------------------------------------------------------- */
/* Dialog form                                                                */
/* -------------------------------------------------------------------------- */

/**
 * A validated form that fills a dialog, the shape most create and edit dialogs
 * take.
 *
 * Zod owns validation and runs on submit rather than per keystroke, so nobody is
 * told their date is invalid while they are still choosing it. The submit button
 * carries its own in-flight state, which is what stops a double click creating
 * two records.
 *
 * The form hook is held at `FieldValues` and narrowed at the boundary: the
 * resolver's schema type cannot be proven to match the caller's generic, and
 * asserting it would hide a real mismatch.
 */
export function DialogForm<TFieldValues extends FieldValues>({
  id,
  schema,
  defaultValues,
  fields,
  onSubmit,
  submitLabel,
  onCancel,
  busy,
  error,
  children,
  className,
  submitVariant = 'primary',
}: {
  id: string
  schema: z.ZodType<TFieldValues>
  defaultValues: Partial<TFieldValues>
  fields: FieldSpec<TFieldValues>[]
  onSubmit: (values: TFieldValues) => Promise<void> | void
  submitLabel: string
  onCancel: () => void
  busy?: boolean
  error?: unknown
  children?: React.ReactNode
  className?: string
  submitVariant?: 'primary' | 'danger' | 'success'
}) {
  const form = useForm<FieldValues>({
    resolver: zodResolver(schema) as Resolver<FieldValues>,
    defaultValues: defaultValues as FieldValues,
    mode: 'onSubmit',
  })

  // Either the mutation's own flag or the form's submit flag disables the button,
  // so a caller driving the mutation from outside still gets a disabled button.
  const submitting = Boolean(busy) || form.formState.isSubmitting

  return (
    <form
      id={id}
      noValidate
      className={className}
      onSubmit={form.handleSubmit(async (values) => {
        await onSubmit(values as TFieldValues)
      })}
    >
      <div className="space-y-4">
        {fields.map((field) => (
          <FormField key={field.name} name={field.name as never}>
            <FormItem>
              <FormLabel>
                {field.label}
                {field.required ? <span className="ml-0.5 text-danger">*</span> : null}
              </FormLabel>

              {field.type === 'textarea' ? (
                <Textarea
                  id={field.name}
                  rows={field.rows ?? 3}
                  placeholder={field.placeholder}
                  disabled={submitting || field.disabled}
                  aria-invalid={Boolean(form.formState.errors[field.name]) || undefined}
                  {...form.register(field.name as never)}
                />
              ) : field.type === 'select' ? (
                <Select
                  id={field.name}
                  disabled={submitting || field.disabled}
                  aria-invalid={Boolean(form.formState.errors[field.name]) || undefined}
                  {...form.register(field.name as never)}
                >
                  {(field.options ?? []).map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </Select>
              ) : (
                <Input
                  id={field.name}
                  type={field.type === 'date' ? 'date' : (field.inputType ?? 'text')}
                  step={field.step}
                  min={field.min}
                  max={field.max}
                  placeholder={field.placeholder}
                  disabled={submitting || field.disabled}
                  aria-invalid={Boolean(form.formState.errors[field.name]) || undefined}
                  {...form.register(field.name as never)}
                />
              )}

              {field.hint ? <FormDescription>{field.hint}</FormDescription> : null}
              <FormMessage />
            </FormItem>
          </FormField>
        ))}

        {children}

        {error ? (
          <p role="alert" className="text-xs font-medium text-danger">
            {error instanceof Error ? error.message : 'That did not work.'}
          </p>
        ) : null}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
          Cancel
        </Button>
        <Button type="submit" variant={submitVariant} loading={submitting}>
          {submitting ? 'Working…' : submitLabel}
        </Button>
      </div>
    </form>
  )
}

/* -------------------------------------------------------------------------- */
/* Simple field wrapper                                                       */
/* -------------------------------------------------------------------------- */

/**
 * A labelled field with its error and hint, for hand-rolled dialogs that need
 * more layout control than `FieldSpec` allows.
 */
export function Field({
  label,
  error,
  hint,
  required,
  children,
  className,
  htmlFor,
}: {
  label: string
  error?: string | undefined
  hint?: string
  required?: boolean
  children: React.ReactNode
  className?: string
  htmlFor?: string
}) {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor} className="mb-1.5 block">
        {label}
        {required ? <span className="ml-0.5 text-danger">*</span> : null}
      </Label>
      {children}
      {error ? (
        <p role="alert" className="mt-1 text-xs font-medium text-danger">
          {error}
        </p>
      ) : hint ? (
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

/** A two-column grid of fields inside a dialog. */
export function FieldGrid({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={className ?? 'grid gap-4 sm:grid-cols-2'}>{children}</div>
}

/** A date input with the design-system border and focus ring. */
export function DateInput({
  id,
  value,
  onChange,
  disabled,
  required,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  required?: boolean
}) {
  return (
    <Input
      id={id}
      type="date"
      value={value}
      required={required}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

/**
 * A currency select.
 *
 * The company's default currency is passed as `value`, never assumed: every
 * amount in this product is formatted from the record's own `currency` field, so
 * a EUR contract is never shown in dollars.
 */
export function CurrencySelect({
  id,
  value,
  onChange,
  disabled,
  currencies = [
    'USD',
    'EUR',
    'GBP',
    'CAD',
    'AUD',
    'NZD',
    'CHF',
    'SEK',
    'NOK',
    'DKK',
    'JPY',
    'SGD',
    'AED',
    'ZAR',
    'INR',
    'BRL',
    'MXN',
  ],
}: {
  id: string
  value: string
  onChange: (currency: string) => void
  disabled?: boolean
  currencies?: readonly string[]
}) {
  return (
    <Select
      id={id}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value.toUpperCase())}
    >
      {currencies.includes(value) ? null : <option value={value}>{value}</option>}
      {currencies.map((code) => (
        <option key={code} value={code}>
          {code}
        </option>
      ))}
    </Select>
  )
}

/**
 * A file input styled as a button, for the multipart document upload.
 *
 * The native control is kept in the DOM and visually hidden rather than replaced,
 * so keyboard focus, the file dialog and the announced file name all behave as
 * the platform intends.
 */
export function FilePicker({
  id,
  label,
  accept,
  disabled,
  onSelect,
}: {
  id: string
  label: string
  accept?: string
  disabled?: boolean
  onSelect: (file: File) => void
}) {
  const inputId = `${id}-input`

  return (
    <>
      <input
        id={inputId}
        type="file"
        accept={accept}
        disabled={disabled}
        className="sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) onSelect(file)
          // Reset so picking the same file twice fires a change event again.
          event.target.value = ''
        }}
      />
      <Button
        type="button"
        variant="outline"
        disabled={disabled}
        onClick={() => {
          const input = document.getElementById(inputId) as HTMLInputElement | null
          input?.click()
        }}
      >
        {label}
      </Button>
    </>
  )
}