'use client'

import * as React from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { z } from 'zod'
import { Mail, KeyRound } from 'lucide-react'

import { getSupabase } from '@/lib/api'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert, Button } from '@/components/ui'

const schema = z.object({
  email: z
    .string()
    .min(1, 'Enter your email address.')
    .email('Enter a valid email address.')
    .transform((value) => value.trim().toLowerCase()),
  password: z.string().min(1, 'Enter your password.'),
})

type LoginValues = z.infer<typeof schema>

const fields: FieldConfig[] = [
  {
    name: 'email',
    label: 'Work email',
    type: 'email',
    autoComplete: 'email',
    inputMode: 'email',
    placeholder: 'you@company.com',
  },
  {
    name: 'password',
    label: 'Password',
    type: 'password',
    autoComplete: 'current-password',
  },
]

/**
 * Wrapped in Suspense because `useSearchParams` opts the page out of static
 * prerendering; the boundary keeps the rest of the page prerenderable.
 */
export function LoginForm() {
  return (
    <React.Suspense fallback={<div className="h-40 skeleton" />}>
      <LoginFormInner />
    </React.Suspense>
  )
}

function LoginFormInner() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const next = searchParams.get('next') ?? '/dashboard'

  const [notice, setNotice] = React.useState<{ tone: 'info' | 'danger'; text: string } | null>(null)
  // Remembered so the email-link button can reuse the address already typed.
  const [email, setEmail] = React.useState('')

  const onSubmit = async (values: LoginValues) => {
    setNotice(null)
    setEmail(values.email)
    window.localStorage.setItem('mytrakin.lastLoginEmail', values.email)

    const { data, error } = await getSupabase().auth.signInWithPassword({
      email: values.email,
      password: values.password,
    })

    if (error) {
      // Supabase deliberately returns the same message for an unknown address
      // and a wrong password, so that the form cannot enumerate accounts.
      setNotice({ tone: 'danger', text: 'That email and password combination is not correct.' })
      return
    }

    if (data.session) {
      router.replace(next)
      router.refresh()
    }
  }

  const [sendingLink, setSendingLink] = React.useState(false)

  const sendMagicLink = async () => {
    const target = email || window.localStorage.getItem('mytrakin.lastLoginEmail') || ''
    if (!target) {
      setNotice({
        tone: 'info',
        text: 'Enter your email address above, then choose email sign-in link.',
      })
      return
    }

    setSendingLink(true)
    // shouldCreateUser: false — this endpoint must never create an account.
    const { error } = await getSupabase().auth.signInWithOtp({
      email: target,
      options: { shouldCreateUser: false },
    })
    setSendingLink(false)

    setNotice(
      error
        ? { tone: 'danger', text: 'Could not send a sign-in link. Try again shortly.' }
        : { tone: 'info', text: `Check ${target} for a sign-in link.` },
    )
  }

  return (
    <div className="space-y-4">
      {notice ? <Alert tone={notice.tone}>{notice.text}</Alert> : null}

      <SchemaForm<LoginValues>
        schema={schema}
        fields={fields}
        defaultValues={{ email: '', password: '' }}
        submitLabel="Sign in"
        onSubmit={onSubmit}
        banner={null}
      />

      <div className="flex items-center justify-between text-sm">
        <a href="/forgot-password" className="text-primary hover:underline">
          Forgot password?
        </a>
        <button
          type="button"
          onClick={sendMagicLink}
          disabled={sendingLink}
          className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground disabled:opacity-60"
        >
          <Mail aria-hidden className="size-3.5" />
          {sendingLink ? 'Sending…' : 'Email sign-in link'}
        </button>
      </div>

      <div className="relative py-1">
        <div className="absolute inset-0 flex items-center" aria-hidden>
          <div className="w-full border-t border-border" />
        </div>
        <div className="relative flex justify-center text-2xs uppercase tracking-wider text-subtle-foreground">
          <span className="bg-background px-2">or</span>
        </div>
      </div>

      <Button
        variant="outline"
        className="w-full"
        onClick={() => {
          void getSupabase()
            .auth.signInWithOAuth({
              provider: 'google',
              options: { redirectTo: `${window.location.origin}/auth/callback` },
            })
            .then(({ error }) => {
              if (error) setNotice({ tone: 'danger', text: 'Google sign-in is not available right now.' })
            })
        }}
      >
        <KeyRound aria-hidden className="size-4" />
        Continue with Google
      </Button>

      <p className="text-center text-sm text-muted-foreground">
        Need an account?{' '}
        <a href="/request-access" className="text-primary hover:underline">
          Request access
        </a>
      </p>
    </div>
  )
}

