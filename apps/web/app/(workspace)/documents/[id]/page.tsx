'use client'

import * as React from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ArrowLeft, Download, Trash2 } from 'lucide-react'

import { api, getAccessToken } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatDate, formatDateTime } from '@/lib/utils'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  Input,
} from '@/components/ui'
import { Field } from '@/components/forms'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, errorMessage, useCompanyQuery } from '@/components/query'
import { ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'

interface DocumentVersion {
  version_no: number
  file_name: string | null
  content_type: string | null
  byte_size: number | null
  checksum_sha256: string | null
  created_at: string
  uploaded_by: string | null
  uploaded_by_name: string | null
}

interface DocumentDetail {
  public_id: string
  company_id: string | null
  doc_type: string
  title: string
  description: string | null
  visibility: string
  version_count: number
  status: string
  is_legal_hold: boolean
  retention_until: string | null
  retention_policy: string | null
  related_type: string | null
  related_id: string | null
  file_name: string | null
  byte_size: number | null
  ai_processing_state: string | null
  created_at: string
  updated_at: string
  versions?: DocumentVersion[]
}

/**
 * One document: metadata, version history, access log, download and
 * retention-aware actions.
 *
 * Deep-linked from SOWs, contracts and projects. Downloads always go through
 * the signed-URL endpoint — the URL is short-lived and never embedded — and
 * the access log only renders for holders of documents.read_audit.
 */
export default function DocumentPage() {
  const params = useParams<{ id: string }>()
  const documentId = decodeURIComponent(params.id)
  const router = useRouter()
  const { activeCompanyPublicId, can, refresh } = useCompany()

  const detail = useCompanyQuery<DocumentDetail>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['documents', 'detail', documentId],
    path: `/documents/${encodeURIComponent(documentId)}`,
  })

  const accessLog = useCompanyQuery<Record<string, unknown>[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['documents', 'access-log', documentId],
    path: `/documents/${encodeURIComponent(documentId)}/access-log`,
    enabled: can('documents.read_audit'),
  })

  const [downloadError, setDownloadError] = React.useState<string | null>(null)
  const [downloading, setDownloading] = React.useState(false)
  const [deleting, setDeleting] = React.useState(false)

  const download = async () => {
    setDownloading(true)
    setDownloadError(null)
    try {
      const link = await api.get<{ url: string }>(
        `/documents/${encodeURIComponent(documentId)}/download`,
        { companyPublicId: activeCompanyPublicId },
      )
      window.open(link.url, '_blank', 'noopener,noreferrer')
      void accessLog.refetch()
    } catch (cause) {
      setDownloadError(errorMessage(cause))
      notifyError(cause, 'A download link could not be created.')
    } finally {
      setDownloading(false)
    }
  }

  const remove = useCompanyMutation<unknown, string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (reason) =>
      api.delete(`/documents/${encodeURIComponent(documentId)}?reason=${encodeURIComponent(reason)}`, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [],
    onSuccess: () => {
      notifySuccess('Document deleted.', 'The file is removed; the audit record remains.')
      setDeleting(false)
      router.push('/documents')
      void refresh()
    },
  })

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Documents', href: '/documents' }, { label: documentId }]}
          title={detail.data?.title ?? 'Document'}
          description={detail.data?.description ?? undefined}
          actions={
            <Button variant="outline" size="sm" onClick={() => router.push('/documents')}>
              <ArrowLeft aria-hidden />
              Library
            </Button>
          }
        />

        {detail.isPending ? (
          <LoadingBlock rows={8} />
        ) : detail.isError ? (
          <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
        ) : detail.data ? (
          <>
            <div className="grid gap-6 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Details</CardTitle>
                </CardHeader>
                <CardContent>
                  <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
                    <Detail label="Document ID" value={detail.data.public_id} mono />
                    <Detail label="Type" value={detail.data.doc_type} />
                    <Detail label="File" value={detail.data.file_name ?? '—'} />
                    <Detail
                      label="Size"
                      value={
                        detail.data.byte_size !== null
                          ? `${Math.round(detail.data.byte_size / 1024)} KB`
                          : '—'
                      }
                    />
                    <Detail label="Versions" value={String(detail.data.version_count)} />
                    <Detail label="Visibility" value={detail.data.visibility} />
                    <Detail label="Status" value={detail.data.status} />
                    <Detail
                      label="AI processing"
                      value={detail.data.ai_processing_state ?? '—'}
                    />
                    <Detail label="Added" value={formatDateTime(detail.data.created_at)} />
                    <Detail label="Updated" value={formatDateTime(detail.data.updated_at)} />
                    {detail.data.related_type ? (
                      <Detail
                        label="Related to"
                        value={`${detail.data.related_type} ${detail.data.related_id ?? ''}`}
                      />
                    ) : null}
                    {detail.data.retention_until ? (
                      <Detail
                        label="Retained until"
                        value={formatDate(detail.data.retention_until)}
                      />
                    ) : null}
                  </dl>

                  {detail.data.is_legal_hold ? (
                    <p className="mt-4 rounded-md border border-danger/30 bg-danger-soft p-3 text-sm">
                      This document is under a legal hold. It cannot be deleted
                      and its retention period is suspended until the hold is lifted.
                    </p>
                  ) : null}

                  {downloadError ? (
                    <p role="alert" className="mt-3 text-xs font-medium text-danger">
                      {downloadError}
                    </p>
                  ) : null}

                  <div className="mt-4 flex flex-wrap gap-2">
                    {can('documents.download') ? (
                      <Button size="sm" onClick={() => void download()} loading={downloading}>
                        <Download aria-hidden />
                        Download
                      </Button>
                    ) : null}
                    {can('documents.delete') && !detail.data.is_legal_hold ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-danger hover:bg-danger-soft"
                        onClick={() => setDeleting(true)}
                      >
                        <Trash2 aria-hidden />
                        Delete
                      </Button>
                    ) : null}
                  </div>
                </CardContent>
              </Card>

              <div className="space-y-6">
                <Card>
                  <CardHeader>
                    <CardTitle>Versions</CardTitle>
                    <CardDescription>
                      Signed documents are never overwritten — every upload is a
                      new version.
                    </CardDescription>
                  </CardHeader>
                  <CardContent>
                    <ul className="divide-y divide-border/60">
                      {(detail.data.versions ?? []).map((version) => (
                        <li
                          key={version.version_no}
                          className="flex items-center justify-between gap-3 py-2 text-sm"
                        >
                          <span className="min-w-0">
                            <span className="font-medium">v{version.version_no}</span>
                            <span className="text-muted-foreground">
                              {' '}
                              · {version.file_name ?? 'unnamed'}
                            </span>
                            <span className="block truncate text-xs text-muted-foreground">
                              {version.uploaded_by_name ?? version.uploaded_by ?? ''}{' '}
                              {version.created_at ? `· ${formatDate(version.created_at)}` : ''}
                            </span>
                          </span>
                          <Badge tone="outline">v{version.version_no}</Badge>
                        </li>
                      ))}
                    </ul>
                    {(detail.data.versions ?? []).length === 0 ? (
                      <p className="text-sm text-muted-foreground">No versions recorded.</p>
                    ) : null}
                    {can('documents.upload') ? (
                      <div className="mt-4 border-t border-border/60 pt-4">
                        <UploadVersionForm
                          documentId={documentId}
                          onUploaded={() => void detail.refetch()}
                        />
                      </div>
                    ) : null}
                  </CardContent>
                </Card>

                {can('documents.read_audit') ? (
                  <Card>
                    <CardHeader>
                      <CardTitle>Access log</CardTitle>
                      <CardDescription>
                        Every time this document has been viewed or downloaded.
                      </CardDescription>
                    </CardHeader>
                    <CardContent>
                      {accessLog.isPending ? (
                        <LoadingBlock rows={3} />
                      ) : accessLog.isError ? (
                        <ErrorState
                          error={accessLog.error}
                          onRetry={() => void accessLog.refetch()}
                        />
                      ) : (accessLog.data ?? []).length === 0 ? (
                        <EmptyState
                          title="No recorded access"
                          description="Views and downloads will appear here."
                        />
                      ) : (
                        <ul className="divide-y divide-border/60">
                          {(accessLog.data ?? []).slice(0, 50).map((entry, index) => (
                            <li
                              key={index}
                              className="flex items-center justify-between gap-3 py-2 text-sm"
                            >
                              <span>{String(entry.action ?? entry.access_type ?? 'access')}</span>
                              <span className="text-xs text-muted-foreground">
                                {entry.accessed_at || entry.at || entry.created_at
                                  ? formatDateTime(
                                      String(entry.accessed_at ?? entry.at ?? entry.created_at),
                                    )
                                  : ''}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </CardContent>
                  </Card>
                ) : null}
              </div>
            </div>
          </>
        ) : (
          <EmptyState title="Document not found" description="It may have been deleted." />
        )}
      </div>

      <ReasonDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${detail.data?.title ?? 'document'}`}
        description="Deletion is blocked while a legal hold or retention policy applies, and the server records who deleted it and why."
        confirmLabel="Delete document"
        label="Reason for deletion"
        busy={remove.isPending}
        error={remove.isError ? remove.error : null}
        onConfirm={(reason) => remove.mutate(reason)}
      />
    </PageShell>
  )
}

function UploadVersionForm({
  documentId,
  onUploaded,
}: {
  documentId: string
  onUploaded: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [reason, setReason] = React.useState('')
  const [uploading, setUploading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)

  const upload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const token = await getAccessToken()
      const form = new FormData()
      form.append('file', file)
      if (reason.trim()) form.append('reason', reason.trim())
      const headers: Record<string, string> = {}
      if (token) headers.Authorization = `Bearer ${token}`
      if (activeCompanyPublicId) headers['X-Company-Public-Id'] = activeCompanyPublicId
      const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'
      const response = await fetch(
        `${base}/api/v1/documents/${encodeURIComponent(documentId)}/versions`,
        { method: 'POST', headers, body: form, cache: 'no-store' },
      )
      if (!response.ok) throw new Error('That upload did not work.')
      setReason('')
      notifySuccess('New version uploaded.', `${file.name} is now the current version.`)
      onUploaded()
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'That upload did not work.'
      setError(message)
      notifyError(cause, 'The new version could not be uploaded.')
    } finally {
      setUploading(false)
      event.target.value = ''
    }
  }

  return (
    <div className="space-y-3">
      <Field label="Reason for this version" hint="Optional, recorded with the version">
        <Input
          id="version-reason"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Amendment 1 — updated payment terms"
        />
      </Field>
      <Field
        label="Replacement file"
        error={error ?? undefined}
        hint="Becomes the current version. Previous versions are kept."
      >
        <Input
          id="version-file"
          type="file"
          disabled={uploading}
          onChange={(event) => void upload(event)}
          aria-invalid={Boolean(error)}
        />
      </Field>
      {uploading ? <p className="text-xs text-muted-foreground">Uploading…</p> : null}
    </div>
  )
}

function Detail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs font-medium uppercase tracking-wide text-subtle-foreground">
        {label}
      </dt>
      <dd className={mono ? 'truncate font-mono text-xs' : 'truncate'}>{value}</dd>
    </div>
  )
}
