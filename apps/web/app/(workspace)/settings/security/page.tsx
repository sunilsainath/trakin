'use client'

import * as React from 'react'
import { z } from 'zod'

import { api, getSupabase } from '@/lib/api'
import type { Session } from '@/lib/types'
import { PageHeader, PageShell } from '@/components/page'
import { Alert, Button, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { ErrorState, LoadingBlock } from '@/components/query'

const passwordSchema = z
  .object({
    password: z.string().min(8, 'Use at least 8 characters.').max(128),
    confirm_password: z.string().min(1, 'Confirm your password.'),
  })
  .refine((values) => values.password === values.confirm_password, {
    message: 'Passwords do not match.',
    path: ['confirm_password'],
  })

type PasswordValues = z.infer<typeof passwordSchema>

const passwordFields: FieldConfig[] = [
  { name: 'password', label: 'New password', type: 'password', autoComplete: 'new-password' },
  {
    name: 'confirm_password',
    label: 'Confirm new password',
    type: 'password',
    autoComplete: 'new-password',
  },
]

const verifySchema = z.object({
  code: z.string().min(6, 'Enter the 6-digit code.').max(8),
})

const verifyFields: FieldConfig[] = [{ name: 'code', label: 'Verification code', inputMode: 'numeric' }]

export default function SecuritySettingsPage() {
  const [sessions, setSessions] = React.useState<Session[] | null>(null)
  const [sessionsError, setSessionsError] = React.useState<unknown>(null)
  const [notice, setNotice] = React.useState<{ tone: 'info' | 'danger'; text: string } | null>(
    null,
  )
  const [factors, setFactors] = React.useState<{ id: string; friendly_name?: string }[]>([])
  const [enrolling, setEnrolling] = React.useState<{
    factorId: string
    challengeId: string
    qr: string
  } | null>(null)

  const loadSessions = React.useCallback(async () => {
    try {
      setSessionsError(null)
      setSessions(await api.get<Session[]>('/auth/sessions'))
    } catch (cause) {
      setSessionsError(cause)
    }
  }, [])

  const loadFactors = React.useCallback(async () => {
    const { data, error } = await getSupabase().auth.mfa.listFactors()
    if (!error) {
      setFactors((data?.totp ?? []).map((factor) => ({ id: factor.id })))
    }
  }, [])

  React.useEffect(() => {
    void loadSessions()
    void loadFactors()
  }, [loadSessions, loadFactors])

  const revokeSession = async (publicId: string) => {
    await api.post(`/auth/sessions/${publicId}/revoke`)
    await loadSessions()
  }

  const revokeAll = async () => {
    const result = await api.post<{ message?: string }>('/auth/sessions/revoke-all')
    setNotice({ tone: 'info', text: result.message ?? 'Other sessions signed out.' })
    await loadSessions()
  }

  const changePassword = async (values: PasswordValues) => {
    setNotice(null)
    const { error } = await getSupabase().auth.updateUser({ password: values.password })
    setNotice(
      error
        ? { tone: 'danger', text: error.message }
        : { tone: 'info', text: 'Password changed.' },
    )
  }

  const startEnroll = async () => {
    setNotice(null)
    const supabase = getSupabase()
    const { data: enrolled, error: enrollError } = await supabase.auth.mfa.enroll({
      factorType: 'totp',
    })
    if (enrollError || !enrolled) {
      setNotice({ tone: 'danger', text: enrollError?.message ?? 'Could not start enrollment.' })
      return
    }
    const { data: challenged, error: challengeError } = await supabase.auth.mfa.challenge({
      factorId: enrolled.id,
    })
    if (challengeError || !challenged) {
      setNotice({
        tone: 'danger',
        text: challengeError?.message ?? 'Could not start verification.',
      })
      return
    }
    setEnrolling({
      factorId: enrolled.id,
      challengeId: challenged.id,
      qr: enrolled.totp.qr_code,
    })
  }

  const verifyEnroll = async (values: { code: string }) => {
    if (!enrolling) return
    const { error } = await getSupabase().auth.mfa.verify({
      factorId: enrolling.factorId,
      challengeId: enrolling.challengeId,
      code: values.code.trim(),
    })
    if (error) {
      setNotice({ tone: 'danger', text: error.message })
      return
    }
    setEnrolling(null)
    setNotice({ tone: 'info', text: 'Two-factor authentication is on.' })
    await loadFactors()
  }

  const unenroll = async (factorId: string) => {
    const { error } = await getSupabase().auth.mfa.unenroll({ factorId })
    setNotice(
      error
        ? { tone: 'danger', text: error.message }
        : { tone: 'info', text: 'Two-factor authentication removed.' },
    )
    await loadFactors()
  }

  return (
    <PageShell>
      <PageHeader title="Security" description="Password, sessions and two-factor authentication." />
      {notice ? (
        <div className="mb-4">
          <Alert tone={notice.tone}>{notice.text}</Alert>
        </div>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Change password</CardTitle>
          </CardHeader>
          <CardContent>
            <SchemaForm<PasswordValues>
              schema={passwordSchema}
              fields={passwordFields}
              defaultValues={{ password: '', confirm_password: '' }}
              submitLabel="Change password"
              onSubmit={changePassword}
              banner={null}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Two-factor authentication</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {factors.length > 0 ? (
              <div className="space-y-2">
                <p className="text-sm text-muted-foreground">
                  {factors.length} authenticator factor{factors.length > 1 ? 's' : ''} active.
                </p>
                {factors.map((factor) => (
                  <div key={factor.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="font-mono text-2xs">{factor.id.slice(0, 8)}…</span>
                    <Button variant="outline" size="sm" onClick={() => void unenroll(factor.id)}>
                      Remove
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Add an authenticator app for a second sign-in step.
              </p>
            )}
            {enrolling ? (
              <div className="space-y-3">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={enrolling.qr} alt="Authenticator QR code" className="h-40 w-40" />
                <SchemaForm<{ code: string }>
                  schema={verifySchema}
                  fields={verifyFields}
                  defaultValues={{ code: '' }}
                  submitLabel="Verify and enable"
                  onSubmit={verifyEnroll}
                  banner={null}
                />
              </div>
            ) : (
              <Button variant="outline" onClick={() => void startEnroll()}>
                Set up authenticator
              </Button>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Active sessions</CardTitle>
          </CardHeader>
          <CardContent>
            {sessionsError ? (
              <ErrorState error={sessionsError} onRetry={() => void loadSessions()} />
            ) : sessions === null ? (
              <LoadingBlock />
            ) : sessions.length === 0 ? (
              <EmptyState title="No sessions" description="Nothing signed in right now." />
            ) : (
              <div className="space-y-2">
                {sessions.map((session) => (
                  <div
                    key={session.public_id}
                    className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-sm"
                  >
                    <div>
                      <p className="font-medium">
                        {session.device ?? 'Unknown device'}
                        {session.current ? ' · this device' : ''}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {session.ip_address ?? 'no IP recorded'}
                      </p>
                    </div>
                    {!session.current ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => void revokeSession(session.public_id)}
                      >
                        Revoke
                      </Button>
                    ) : null}
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => void revokeAll()}>
                  Sign out all other sessions
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </PageShell>
  )
}
