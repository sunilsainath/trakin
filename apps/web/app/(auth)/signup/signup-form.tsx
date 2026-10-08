'use client'

import * as React from 'react'
import { useRouter } from 'next/navigation'
import { z } from 'zod'

import { getSupabase } from '@/lib/api'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert } from '@/components/ui'

const schema = z
  .object({
    first_name: z.string().min(1, 'Enter your first name.').max(80),
    last_name: z.string().min(1, 'Enter your last name.').max(80),
    email: z
      .string()
      .min(1, 'Enter your email address.')
      .email('Enter a valid email address.')
      .transform((value) => value.trim().toLowerCase()),
    mobile: z.string().max(32).optional().or(z.literal('')),
    address: z.string().max(200).optional().or(z.literal('')),
    country: z.string().min(1, 'Select your country.').max(80),
    password: z.string().min(8, 'Use at least 8 characters.').max(128),
    confirm_password: z.string().min(1, 'Confirm your password.'),
  })
  .refine((values) => values.password === values.confirm_password, {
    message: 'Passwords do not match.',
    path: ['confirm_password'],
  })

type SignupValues = z.infer<typeof schema>

const fields: FieldConfig[] = [
  { name: 'first_name', label: 'First name', autoComplete: 'given-name', placeholder: 'Asha' },
  { name: 'last_name', label: 'Last name', autoComplete: 'family-name', placeholder: 'Sharma' },
  {
    name: 'email',
    label: 'Work email',
    type: 'email',
    autoComplete: 'email',
    inputMode: 'email',
    placeholder: 'you@company.com',
  },
  {
    name: 'mobile',
    label: 'Mobile (optional)',
    type: 'tel',
    autoComplete: 'tel',
    placeholder: '+91 98200 12345',
  },
  {
    name: 'address',
    label: 'Address (optional)',
    autoComplete: 'street-address',
    placeholder: 'Street, city, state',
  },
  { name: 'country', label: 'Country', autoComplete: 'country-name', placeholder: 'India' },
  { name: 'password', label: 'Password', type: 'password', autoComplete: 'new-password' },
  {
    name: 'confirm_password',
    label: 'Confirm password',
    type: 'password',
    autoComplete: 'new-password',
  },
]

export function SignupForm() {
  const router = useRouter()
  const [notice, setNotice] = React.useState<{ tone: 'info' | 'danger'; text: string } | null>(
    null,
  )
  const [done, setDone] = React.useState<string | null>(null)

  const onSubmit = async (values: SignupValues) => {
    setNotice(null)
    const { error } = await getSupabase().auth.signUp({
      email: values.email,
      password: values.password,
      options: {
        data: {
          first_name: values.first_name,
          last_name: values.last_name,
          mobile: values.mobile || null,
          address: values.address || null,
          country: values.country,
        },
        emailRedirectTo: `${window.location.origin}/auth/callback`,
      },
    })

    if (error) {
      setNotice({ tone: 'danger', text: error.message })
      return
    }

    // With email confirmation enforced there is no session yet: tell the user
    // to verify instead of navigating anywhere.
    setDone(values.email)
    router.refresh()
  }

  if (done) {
    return (
      <Alert tone="info">
        Check {done} for a verification link. Your account activates when you confirm it.
      </Alert>
    )
  }

  return (
    <div className="space-y-4">
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}
      <SchemaForm<SignupValues>
        schema={schema}
        fields={fields}
        defaultValues={{
          first_name: '',
          last_name: '',
          email: '',
          mobile: '',
          address: '',
          country: '',
          password: '',
          confirm_password: '',
        }}
        submitLabel="Create account"
        onSubmit={onSubmit}
        banner={null}
      />
      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{' '}
        <a href="/login" className="text-primary hover:underline">
          Sign in
        </a>
      </p>
    </div>
  )
}
