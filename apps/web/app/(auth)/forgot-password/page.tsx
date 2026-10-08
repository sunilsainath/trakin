'use client'

import * as React from 'react'
import { z } from 'zod'

import { getSupabase } from '@/lib/api'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert } from '@/components/ui'
import { AuthShell } from '@/components/auth-shell'

const schema = z.object({
  email: z
    .string()
    .min(1, 'Enter your email address.')
    .email('Enter a valid email address.')
    .transform((value) => value.trim().toLowerCase()),
})

type ForgotValues = z.infer<typeof schema>

const fields: FieldConfig[] = [
  {
    name: 'email',
    label: 'Work email',
    type: 'email',
    autoComplete: 'email',
    inputMode: 'email',
    placeholder: 'you@company.com',
  },
]

function ForgotPasswordForm() {
  const [notice, setNotice] = React.useState<{ tone: 'info' | 'danger'; text: string } | null>(
    null,
  )

  const onSubmit = async (values: ForgotValues) => {
    setNotice(null)
    const { error } = await getSupabase().auth.resetPasswordForEmail(values.email, {
      redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
    })
    setNotice(
      error
        ? { tone: 'danger', text: 'Could not send a reset link. Try again shortly.' }
        : {
            tone: 'info',
            text: `If an account exists for ${values.email}, a reset link is on its way.`,
          },
    )
  }

  return (
    <div className="space-y-4">
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}
      <SchemaForm<ForgotValues>
        schema={schema}
        fields={fields}
        defaultValues={{ email: '' }}
        submitLabel="Send reset link"
        onSubmit={onSubmit}
        banner={null}
      />
      <p className="text-center text-sm text-muted-foreground">
        <a href="/login" className="text-primary hover:underline">
          Back to sign in
        </a>
      </p>
    </div>
  )
}

export default function ForgotPasswordPage() {
  return (
    <AuthShell title="Reset your password" subtitle="We will email you a reset link.">
      <ForgotPasswordForm />
    </AuthShell>
  )
}
