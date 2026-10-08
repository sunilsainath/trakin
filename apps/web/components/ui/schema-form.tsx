'use client'

import * as React from 'react'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  useForm,
  type DefaultValues,
  type FieldValues,
  type Resolver,
  type UseFormRegisterReturn,
} from 'react-hook-form'
import type { ZodTypeAny } from 'zod'

import { cn } from '@/lib/utils'
import { FormField, FormItem } from '@/components/ui/form'

/**
 * Field names are plain strings rather than `FieldPath<T>`: the field list is
 * assembled at runtime from a schema, so the compiler cannot check the names
 * against a single value type. Validation authority stays with Zod.
 */
export interface FieldConfig {
  name: string
  label: string
  type?: React.HTMLInputTypeAttribute
  placeholder?: string
  hint?: string
  autoComplete?: string
  inputMode?: React.HTMLAttributes<HTMLInputElement>['inputMode']
  disabled?: boolean
  readOnly?: boolean
  maxLength?: number
  min?: number
  max?: number
  step?: number
  options?: { value: string; label: string; description?: string }[]
  rows?: number
}

interface FormSchemaProps<TFieldValues extends FieldValues> {
schema: ZodTypeAny
fields: FieldConfig[]
  defaultValues: DefaultValues<TFieldValues>
  submitLabel?: string
  onSubmit: (values: TFieldValues) => Promise<void> | void
  children?: React.ReactNode
  footer?: React.ReactNode
  className?: string
  /** Renders above the fields, e.g. a server error banner. */
  banner?: React.ReactNode
  description?: string
}

/**
 * Schema-driven form.
 *
 * Zod owns validation and runs on submit rather than per keystroke, so users
 * are not corrected while they are still typing an email address. The submit
 * button carries its own loading state to prevent a double submission.
 *
 * The form hook is held at `FieldValues` and narrowed back to TFieldValues at
 * the boundary, because the resolver's schema type cannot be proven to match
 * the caller's generic at compile time.
 */
export function SchemaForm<TFieldValues extends FieldValues>({
  schema,
  fields,
  defaultValues,
  submitLabel = 'Save',
  onSubmit,
  children,
  footer,
  className,
  banner,
  description,
}: FormSchemaProps<TFieldValues>) {
  const form = useForm<FieldValues>({
    resolver: zodResolver(schema) as Resolver<FieldValues>,
    defaultValues,
    mode: 'onSubmit',
  })

  const submitting = form.formState.isSubmitting

  return (
    <form
      onSubmit={form.handleSubmit(async (values) => {
        await onSubmit(values as TFieldValues)
      })}
      className={cn('space-y-5', className)}
      noValidate
    >
      {banner}

      {fields.map((field) => (
        <FormField key={field.name} name={field.name as never}>
          <FormItem>
            <SchemaField
              field={field as FieldConfig}
              register={form.register(field.name)}
              error={form.formState.errors[field.name]?.message as string | undefined}
              disabled={submitting || field.disabled}
            />
          </FormItem>
        </FormField>
      ))}

      {children}

      <div className="flex items-center justify-end gap-2 pt-1">
        {footer}
        <button
          type="submit"
          disabled={submitting}
          aria-busy={submitting}
          className={cn(
            'inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm',
            'font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary-strong',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
            'disabled:pointer-events-none disabled:opacity-50',
          )}
        >
          {submitting ? (
            <span
              aria-hidden
              className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent"
            />
          ) : null}
          {submitting ? 'Working…' : submitLabel}
        </button>
      </div>

      {description ? (
        <p className="text-right text-xs text-muted-foreground">{description}</p>
      ) : null}
    </form>
  )
}

type RegisterResult = UseFormRegisterReturn

function SchemaField({
  field,
  register,
  error,
  disabled,
}: {
  field: FieldConfig
  register: RegisterResult
  error?: string
  disabled?: boolean
}) {
  const classes =
    'flex w-full rounded-md border bg-surface px-3 text-sm text-foreground h-10 ' +
    'placeholder:text-muted-foreground/80 transition-colors ' +
    'focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring/40 ' +
    'disabled:cursor-not-allowed disabled:opacity-60 disabled:bg-muted/50 ' +
    (error ? 'border-danger ring-danger/20' : 'border-input')

  return (
    <>
      <label htmlFor={field.name} className="block text-sm font-medium text-foreground">
        {field.label}
      </label>

      {field.options ? (
        <select
          id={field.name}
          disabled={disabled}
          className={classes}
          {...register}
        >
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : field.rows ? (
        <textarea
          id={field.name}
          rows={field.rows}
          disabled={disabled}
          placeholder={field.placeholder}
          className={`${classes} resize-y py-2`}
          {...register}
        />
      ) : (
        <input
          id={field.name}
          type={field.type ?? 'text'}
          placeholder={field.placeholder}
          autoComplete={field.autoComplete}
          inputMode={field.inputMode}
          disabled={disabled}
          readOnly={field.readOnly}
          maxLength={field.maxLength}
          min={field.min}
          max={field.max}
          step={field.step}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${field.name}-message` : field.hint ? `${field.name}-hint` : undefined}
          className={classes}
          {...register}
        />
      )}

      {field.hint && !error ? (
        <p id={`${field.name}-hint`} className="text-xs text-muted-foreground">
          {field.hint}
        </p>
      ) : null}

      {error ? (
        <p
          id={`${field.name}-message`}
          role="alert"
          className="text-xs font-medium text-danger"
        >
          {error}
        </p>
      ) : null}
    </>
  )
}

