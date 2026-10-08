'use client'

import * as React from 'react'
import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency } from '@/lib/utils'
import type { Invoice } from '@/lib/domain-types'
import { Button, Dialog, Input, Select } from '@/components/ui'
import { CurrencySelect, Field } from '@/components/forms'
import { notifyError } from '@/components/toast'

/* -------------------------------------------------------------------------- */
/* Record payment                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Record a payment against an invoice.
 *
 * This is a money-creating form, so the request carries an idempotency key: a
 * double click or a network retry cannot create two payments against the same
 * invoice.
 */
export function RecordPaymentDialog({
  invoice,
  triggerLabel = 'Record payment',
}: {
  invoice: Invoice | null
  triggerLabel?: string
}) {
  const { activeCompanyPublicId } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [amount, setAmount] = React.useState('')
  const [method, setMethod] = React.useState('ACH')
  const [fee, setFee] = React.useState('0')
  const [reference, setReference] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [key, setKey] = React.useState(() => newKey())

  function newKey(): string {
    const random =
      typeof crypto !== 'undefined' && 'randomUUID' in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    return `payment_${random.replace(/-/g, '')}`
  }

  React.useEffect(() => {
    if (open && invoice) {
      setAmount(invoice.balance_due)
      setMethod('ACH')
      setFee('0')
      setReference('')
      setError(null)
      setKey(newKey())
    }
  }, [open, invoice])

  const record = useCompanyMutation<unknown>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: async () => {
      if (!invoice) throw new Error('No invoice selected.')
      const payment = await api.post<{ public_id: string }>(
        '/payments',
        {
          direction: invoice.direction === 'PAYABLE' ? 'PAYABLE' : 'RECEIVABLE',
          amount,
          currency: invoice.currency,
          payment_method: method,
          fee_amount: fee || '0',
          processor: 'manual',
          idempotency_key: key,
          ...(reference ? { metadata: { reference } } : {}),
        },
        { companyPublicId: activeCompanyPublicId, idempotencyKey: key },
      )

      // Allocate in the same flow so the balance is reduced rather than left for
      // a second manual step that may never happen.
      await api.post(
        `/payments/${payment.public_id}/allocations`,
        { allocations: [{ invoice_id: invoice.public_id, amount }], matched_by: 'MANUAL' },
        { companyPublicId: activeCompanyPublicId, idempotencyKey: `${key}_alloc` },
      )

      return payment
    },
    invalidate: [
      ['invoices', 'list'],
      ['invoices', 'detail', invoice?.public_id],
      ['payments', 'list'],
    ],
    onSuccess: () => {
      setOpen(false)
      // A new key so the next intentional payment is not treated as a retry.
      setKey(newKey())
    },
  })

  if (!invoice) return null

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()

    if (!amount || Number(amount) <= 0) {
      setError('Enter an amount greater than zero.')
      return
    }
    if (Number(amount) > Number(invoice.balance_due)) {
      setError('The amount cannot be greater than the balance due on this invoice.')
      return
    }

    setError(null)
    try {
      await record.mutateAsync()
    } catch (cause) {
      notifyError(cause, 'The payment could not be recorded.')
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        {triggerLabel}
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Record a payment"
        description="This records money received and allocates it to this invoice. It does not move money: no processor is contacted."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={record.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="record-payment" loading={record.isPending}>
              Record payment
            </Button>
          </>
        }
      >
        <form id="record-payment" onSubmit={submit} className="space-y-4">
          <div className="rounded-md bg-surface-sunken p-3 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Invoice</span>
              <span className="font-mono text-xs">{invoice.public_id}</span>
            </div>
            <div className="mt-1 flex items-center justify-between gap-3">
              <span className="text-muted-foreground">Balance due</span>
              <span className="font-medium tabular">
                {formatCurrency(invoice.balance_due, invoice.currency)}
              </span>
            </div>
          </div>

          <Field
            label="Amount"
            required
            error={error ?? undefined}
            hint={`In ${invoice.currency}`}
          >
            <Input
              id="payment-amount"
              type="number"
              min="0"
              step="0.01"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              aria-invalid={Boolean(error)}
            />
          </Field>

          <Field label="Payment method">
            <Select id="payment-method" value={method} onChange={(event) => setMethod(event.target.value)}>
              {['ACH', 'WIRE', 'CARD', 'CHECK', 'CASH', 'CRYPTO', 'OTHER'].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Processing fee" hint={`In ${invoice.currency}`}>
            <Input
              id="payment-fee"
              type="number"
              min="0"
              step="0.01"
              value={fee}
              onChange={(event) => setFee(event.target.value)}
            />
          </Field>

          <Field label="Reference" hint="Optional bank reference or note">
            <Input
              id="payment-reference"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
            />
          </Field>

          <Field label="Currency" hint="Taken from the invoice; payments cannot be recorded in a different currency.">
            <CurrencySelect id="payment-currency" value={invoice.currency} onChange={() => undefined} disabled />
          </Field>
        </form>
      </Dialog>
    </>
  )
}
