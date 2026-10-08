'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { z } from 'zod'

import { getSupabase } from '@/lib/api'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert } from '@/components/ui'
import { AuthShell } from '@/components/auth-shell'

const schema = z
  .object({
    password: z.string().min(8, 'Use at least 8 characters.').max(128),
    confirm_password: z.string().min(1, 'Confirm your password.'),
  })
  .refine((values) => values.password === values.confirm_password, {
    message: 'Passwords do not match.',
    path: ['confirm_password'],
  })

type ResetValues = z.infer<typeof schema>

const fields: FieldConfig[] = [
  { name: 'password', label: 'New password', type: 'password', autoComplete: 'new-password' },
  {
    name: 'confirm_password',
    label: 'Confirm new password',
    type: 'password',
    autoComplete: 'new-password',
  },
]

function ResetPasswordForm() {
  const router = useRouter()
  const [notice, setNotice] = React.useState<{ tone: 'info' | 'danger'; text: string } | null>(
    null,
  )

  const onSubmit = async (values: ResetValues) => {
    setNotice(null)
    const { error } = await getSupabase().auth.updateUser({ password: values.password })
    if (error) {
      setNotice({ tone: 'danger', text: error.message })
      return
    }
    router.replace('/login')
    router.refresh()
  }

  return (
    <div className="space-y-4">
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}
      <SchemaForm<ResetValues>
        schema={schema}
        fields={fields}
        defaultValues={{ password: '', confirm_password: '' }}
        submitLabel="Set new password"
        onSubmit={onSubmit}
        banner={null}
      />
    </div>
  )
}

export default function ResetPasswordPage() {
  return (
    <AuthShell title="Choose a new password" subtitle="Signed in via your reset link.">
      <ResetPasswordForm />
    </AuthShell>
  )
}
