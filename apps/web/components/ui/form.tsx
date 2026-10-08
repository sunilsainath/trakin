'use client'

import * as React from 'react'
import { AlertCircle } from 'lucide-react'
import { useFormContext, type FieldPath, type FieldValues } from 'react-hook-form'

import { cn } from '@/lib/utils'
import { Input, Label } from '@/components/ui'

/**
 * Form primitives bound to React Hook Form.
 *
 * The error message is wired with aria-describedby and aria-invalid so the
 * field is announced correctly, rather than relying on colour alone.
 */

interface FormFieldContextValue<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>,
> {
  name: TName
  children?: React.ReactNode
}

const FormFieldContext = React.createContext<FormFieldContextValue | null>(null)
const FormItemContext = React.createContext<{ id: string } | null>(null)

export function FormField<
  TFieldValues extends FieldValues = FieldValues,
  TName extends FieldPath<TFieldValues> = FieldPath<TFieldValues>,
>({ ...props }: FormFieldContextValue<TFieldValues, TName>) {
  return <FormFieldContext.Provider value={props}>{props.children}</FormFieldContext.Provider>
}

export function useFormField() {
  const fieldContext = React.useContext(FormFieldContext)
  const itemContext = React.useContext(FormItemContext)
  const { getFieldState } = useFormContext()

  if (!fieldContext || !itemContext) {
    throw new Error('useFormField must be used inside <FormField> and <FormItem>')
  }

  const { name } = fieldContext
  const fieldState = getFieldState(name)

  return {
    name,
    formItemId: `${itemContext.id}-item`,
    formDescriptionId: `${itemContext.id}-description`,
    formMessageId: `${itemContext.id}-message`,
    ...fieldState,
  }
}

export function FormItem({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const id = React.useId()
  return (
    <FormItemContext.Provider value={{ id }}>
      <div className={cn('space-y-1.5', className)} {...props} />
    </FormItemContext.Provider>
  )
}

export function FormLabel({ className, ...props }: React.ComponentProps<typeof Label>) {
  const { error, formItemId } = useFormField()
  return (
    <Label
      htmlFor={formItemId}
      className={cn(error && 'text-danger', className)}
      {...props}
    />
  )
}

export function FormControl({ children }: { children: React.ReactNode }) {
  const { error, formItemId, formDescriptionId, formMessageId } = useFormField()
  const describedBy = error ? `${formDescriptionId} ${formMessageId}` : formDescriptionId

  if (!React.isValidElement(children)) return <>{children}</>

  return React.cloneElement(children as React.ReactElement<Record<string, unknown>>, {
    id: formItemId,
    'aria-invalid': error ? true : undefined,
    'aria-describedby': describedBy || undefined,
  })
}

export function FormDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  const { formDescriptionId } = useFormField()
  return (
    <p id={formDescriptionId} className={cn('text-xs text-muted-foreground', className)} {...props} />
  )
}

/**
 * Server errors and schema errors both surface here. `aria-live="polite"` means
 * a validation failure is announced without stealing focus.
 */
export function FormMessage({ className, children, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  const { error, formMessageId } = useFormField()
  const body = error ? String(error.message ?? '') : children

  if (!body) return null

  return (
    <p
      id={formMessageId}
      role="alert"
      aria-live="polite"
      className={cn('flex items-center gap-1 text-xs font-medium text-danger', className)}
      {...props}
    >
      <AlertCircle aria-hidden className="size-3.5 shrink-0" />
      {body}
    </p>
  )
}

/** Accessible text field, wired to form state. */
export function FormInput({
  label,
  hint,
  className,
  ...props
}: React.ComponentProps<typeof Input> & {
  label: string
  hint?: string
}) {
  return (
    <FormItem>
      <FormLabel>{label}</FormLabel>
      <FormControl>
        <Input className={className} {...props} />
      </FormControl>
      {hint ? <FormDescription>{hint}</FormDescription> : null}
      <FormMessage />
    </FormItem>
  )
}