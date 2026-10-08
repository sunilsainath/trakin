'use client'

import * as React from 'react'
import { useParams, useRouter } from 'next/navigation'
import { Building2, CheckCircle2, MailWarning, XCircle } from 'lucide-react'

import { ApiError, api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { Alert, Button, Card, CardContent, CardHeader, CardTitle, Skeleton } from '@/components/ui'
import { PageHeader, PageShell } from '@/components/page'
import { notifyError, notifySuccess } from '@/components/toast'
import { PublicId } from '@/components/public-id'

interface InvitationPreview {
  public_id: string
  email: string
  status: string
  expires_at: string
  job_title: string | null
  company_public_id: string
  company_name: string
  company_verification_state: string
  role_key: string
  role_name: string
}

/**
 * Redeem a company invitation.
 *
 * The token in the URL is the credential: whoever holds the link sees what it
 * offers, and accepting requires being signed in as the invited email address.
 * The server enforces all of that; this screen only renders the outcome with
 * loading, error, expired and email-mismatch states.
 */
export default function InvitationPage() {
  const params = useParams<{ token: string }>()
  const token = decodeURIComponent(params.token)
  const router = useRouter()
  const { me, refresh, switchCompany } = useCompany()

  const [preview, setPreview] = React.useState<InvitationPreview | null>(null)
  const [error, setError] = React.useState<ApiError | null>(null)
  const [accepting, setAccepting] = React.useState(false)
  const [accepted, setAccepted] = React.useState(false)

  React.useEffect(() => {
    let cancelled = false
    setPreview(null)
    setError(null)
    api
      .get<InvitationPreview>(`/companies/invitations/${encodeURIComponent(token)}`)
      .then((result) => {
        if (!cancelled) setPreview(result)
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof ApiError ? cause : new ApiError('INTERNAL_ERROR', String(cause), 0))
        }
      })
    return () => {
      cancelled = true
    }
  }, [token])

  const emailMismatch =
    preview != null &&
    me?.email != null &&
    preview.email.toLowerCase() !== me.email.toLowerCase()

  const accept = async () => {
    setAccepting(true)
    try {
      const result = await api.post<{ company_public_id: string }>(
        '/companies/invitations/accept',
        { token },
      )
      await refresh()
      await switchCompany(result.company_public_id)
      setAccepted(true)
      notifySuccess('Welcome aboard.', `You joined ${preview?.company_name ?? 'the company'}.`)
      router.push('/network')
    } catch (cause) {
      notifyError(cause, 'The invitation could not be accepted.')
      setAccepting(false)
    }
  }

  return (
    <PageShell>
      <div className="mx-auto w-full max-w-xl space-y-6">
        <PageHeader
          crumbs={[{ label: 'Invitation' }]}
          title="Company invitation"
          description="Review what is being offered before you accept."
        />

        {error ? (
          <Card>
            <CardContent className="space-y-3 pt-5 text-center">
              <XCircle aria-hidden className="mx-auto size-8 text-danger" />
              <p className="font-medium">This invitation link does not work</p>
              <p className="text-sm text-muted-foreground">{error.userMessage}</p>
              <Button variant="outline" onClick={() => router.push('/network')}>
                Back to the network
              </Button>
            </CardContent>
          </Card>
        ) : preview == null ? (
          <Card>
            <CardContent className="space-y-3 pt-5">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-2/3" />
            </CardContent>
          </Card>
        ) : accepted ? (
          <Card>
            <CardContent className="space-y-3 pt-5 text-center">
              <CheckCircle2 aria-hidden className="mx-auto size-8 text-success" />
              <p className="font-medium">You joined {preview.company_name}</p>
              <p className="text-sm text-muted-foreground">Taking you to your feed…</p>
            </CardContent>
          </Card>
        ) : preview.status !== 'PENDING' ? (
          <Card>
            <CardContent className="space-y-3 pt-5 text-center">
              <XCircle aria-hidden className="mx-auto size-8 text-warning" />
              <p className="font-medium">
                This invitation is {preview.status.toLowerCase()}
              </p>
              <p className="text-sm text-muted-foreground">
                {preview.status === 'ACCEPTED'
                  ? 'It has already been used. Ask for a fresh one if you still need access.'
                  : 'Ask the sender for a fresh invitation.'}
              </p>
              <Button variant="outline" onClick={() => router.push('/network')}>
                Back to the network
              </Button>
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Building2 aria-hidden className="size-4 text-primary" />
                {preview.company_name}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
                <Detail label="Company ID" value={<PublicId value={preview.company_public_id} kind="company" />} />
                <Detail label="Role offered" value={`${preview.role_name} (${preview.role_key})`} />
                <Detail label="Invited email" value={preview.email} />
                <Detail
                  label="Expires"
                  value={new Date(preview.expires_at).toLocaleDateString()}
                />
                {preview.job_title ? <Detail label="Job title" value={preview.job_title} /> : null}
              </dl>

              {emailMismatch ? (
                <Alert tone="warning">
                  <span className="flex items-start gap-2">
                    <MailWarning aria-hidden className="mt-0.5 size-4 shrink-0" />
                    <span>
                      You are signed in as {me?.email}, but this invitation was
                      issued to {preview.email}. Accepting will fail — sign in
                      with the invited address first.
                    </span>
                  </span>
                </Alert>
              ) : null}

              <div className="flex justify-end gap-2">
                <Button variant="ghost" onClick={() => router.push('/network')}>
                  Decline
                </Button>
                <Button onClick={() => void accept()} loading={accepting} disabled={emailMismatch}>
                  Accept invitation
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </PageShell>
  )
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        {label}
      </dt>
      <dd className="truncate">{value}</dd>
    </div>
  )
}
