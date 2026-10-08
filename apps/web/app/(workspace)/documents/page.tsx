'use client'

import * as React from 'react'
import { FileStack, Trash2, Upload } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation, uploadDocument } from '@/hooks/use-mutations'
import { formatDate, formatDateTime } from '@/lib/utils'
import { DOC_TYPES, type DocumentSummary } from '@/lib/domain-types'
import {
  Badge,
  Button,
  Dialog,
  EmptyState,
  Input,
  Select,
} from '@/components/ui'
import { Field, FilePicker } from '@/components/forms'
import { StatusBadge } from '@/components/badges'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { OffsetFooter } from '@/components/list'
import { FilterBar, FilterInput, FilterSelect } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingBlock, errorMessage, useCompanyQuery } from '@/components/query'
import { ReasonDialog } from '@/components/destructive'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * The document library.
 *
 * This endpoint uses `limit`/`offset` rather than a cursor, so it pages by
 * offset. Upload is multipart, so it goes through `uploadDocument` rather than
 * the JSON client — a file cannot be JSON-encoded.
 */
export default function DocumentsPage() {
  const { activeCompanyPublicId, can } = useCompany()
  const [offset, setOffset] = React.useState(0)
  const [docType, setDocType] = React.useState('')
  const [relatedType, setRelatedType] = React.useState('')
  const [search, setSearch] = React.useState('')
  const [uploading, setUploading] = React.useState(false)
  const [deleting, setDeleting] = React.useState<DocumentSummary | null>(null)
  const [detail, setDetail] = React.useState<DocumentSummary | null>(null)

  const limit = 50

  React.useEffect(() => {
    setOffset(0)
  }, [docType, relatedType, search])

  const params = React.useMemo(() => {
    const query = new URLSearchParams()
    query.set('limit', String(limit))
    query.set('offset', String(offset))
    if (docType) query.set('doc_type', docType)
    if (relatedType) query.set('related_type', relatedType)
    if (search) query.set('q', search)
    return `?${query.toString()}`
  }, [docType, relatedType, search, offset])

  const documents = useCompanyQuery<import('@/lib/domain-types').Page<DocumentSummary>>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['documents', 'list', docType, relatedType, search, offset],
    path: '/documents',
    queryParams: params,
  })

  const remove = useCompanyMutation<unknown, { id: string; reason: string }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: ({ id, reason }) =>
      api.delete(`/documents/${id}?reason=${encodeURIComponent(reason)}`, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [['documents', 'list']],
    onSuccess: () => {
      notifySuccess('Document deleted.', 'The file is removed; the audit record remains.')
      setDeleting(null)
    },
  })

  const rows = documents.data?.data ?? []

  const columns: Column<DocumentSummary>[] = [
    {
      key: 'title',
      header: 'Document',
      cell: (row) => (
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => setDetail(row)}
            className="block truncate text-left font-medium hover:text-primary-strong"
          >
            {row.title}
          </button>
          <PublicId value={row.public_id} />
        </div>
      ),
    },
    { key: 'type', header: 'Type', hideBelow: 'sm', cell: (row) => row.doc_type },
    {
      key: 'file',
      header: 'File',
      hideBelow: 'lg',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate text-muted-foreground">{row.file_name ?? '—'}</p>
          <p className="text-2xs text-subtle-foreground">
            {row.byte_size !== null ? `${Math.round(row.byte_size / 1024)} KB` : ''}
            {row.version_no ? ` · v${row.version_no}` : ''}
          </p>
        </div>
      ),
    },
    {
      key: 'related',
      header: 'Related to',
      hideBelow: 'lg',
      cell: (row) => (row.related_type ? row.related_type : '—'),
    },
    {
      key: 'versions',
      header: 'Versions',
      numeric: true,
      hideBelow: 'md',
      cell: (row) => row.version_count,
    },
    {
      key: 'hold',
      header: 'Hold',
      hideBelow: 'md',
      cell: (row) =>
        row.is_legal_hold ? <Badge tone="danger">Legal hold</Badge> : '—',
    },
    {
      key: 'status',
      header: 'Status',
      cell: (row) => <StatusBadge status={row.status} />,
    },
    {
      key: 'created',
      header: 'Added',
      hideBelow: 'sm',
      cell: (row) => formatDate(row.created_at),
    },
    {
      key: 'actions',
      header: '',
      hideBelow: 'md',
      cell: (row) =>
        can('documents.delete') ? (
          <Button
            size="xs"
            variant="ghost"
            className="text-danger hover:bg-danger-soft"
            onClick={() => setDeleting(row)}
          >
            <Trash2 aria-hidden />
            Delete
          </Button>
        ) : null,
    },
  ]

  const activeFilters = (docType ? 1 : 0) + (relatedType ? 1 : 0) + (search ? 1 : 0)

  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Documents' }]}
          title="Documents"
          description="Contracts, SOWs, invoices and tax paperwork. Files are stored by the platform and downloaded through a short-lived signed URL, never a public one."
          actions={
            can('documents.upload') ? (
              <Button onClick={() => setUploading(true)}>
                <Upload aria-hidden />
                Upload
              </Button>
            ) : undefined
          }
        />

        <FilterBar
          activeCount={activeFilters}
          onClear={() => {
            setDocType('')
            setRelatedType('')
            setSearch('')
          }}
        >
          <FilterInput
            id="doc-search"
            label="Search"
            value={search}
            onChange={setSearch}
            placeholder="Title or description"
            className="min-w-52 flex-1"
          />
          <FilterSelect
            id="doc-type"
            label="Type"
            value={docType}
            onChange={setDocType}
            options={[...DOC_TYPES]}
            className="w-44"
          />
          <FilterSelect
            id="doc-related"
            label="Related to"
            value={relatedType}
            onChange={setRelatedType}
            options={['PROJECT', 'CONTRACT', 'SOW', 'INVOICE', 'COMPANY', 'MEMBER']}
            className="w-44"
          />
        </FilterBar>

        {documents.isPending ? (
          <LoadingBlock rows={10} />
        ) : documents.isError ? (
          <ErrorState error={documents.error} onRetry={() => void documents.refetch()} />
        ) : (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.public_id}
              caption="Documents in this company"
              exportName="documents"
              csv={[
                { header: 'Document ID', value: (row) => row.public_id },
                { header: 'Title', value: (row) => row.title },
                { header: 'Type', value: (row) => row.doc_type },
                { header: 'File name', value: (row) => row.file_name },
                { header: 'Versions', value: (row) => row.version_count },
                { header: 'Status', value: (row) => row.status },
                { header: 'Visibility', value: (row) => row.visibility },
                { header: 'Related type', value: (row) => row.related_type },
                { header: 'Legal hold', value: (row) => (row.is_legal_hold ? 'yes' : 'no') },
                { header: 'Added', value: (row) => row.created_at },
              ]}
              emptyState={
                <EmptyState
                  icon={<FileStack aria-hidden />}
                  title={activeFilters > 0 ? 'No documents match' : 'No documents yet'}
                  description={
                    activeFilters > 0
                      ? 'Clear the filters to see everything.'
                      : 'Upload contracts, SOWs, signed paperwork and tax forms to keep them with the records they belong to.'
                  }
                  action={
                    activeFilters > 0 ? (
                      <Button
                        variant="outline"
                        onClick={() => {
                          setDocType('')
                          setRelatedType('')
                          setSearch('')
                        }}
                      >
                        Clear filters
                      </Button>
                    ) : can('documents.upload') ? (
                      <Button onClick={() => setUploading(true)}>Upload the first document</Button>
                    ) : undefined
                  }
                />
              }
            />

            <OffsetFooter
              limit={limit}
              offset={offset}
              meta={documents.data?.meta}
              onOffsetChange={setOffset}
              busy={documents.isFetching}
            />
          </>
        )}
      </div>

      <UploadDialog
        open={uploading}
        onOpenChange={setUploading}
        onDone={() => void documents.refetch()}
      />

      <DocumentDetailDialog document={detail} onClose={() => setDetail(null)} />

      <ReasonDialog
        open={Boolean(deleting)}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
        }}
        title="Delete this document"
        description="The file is removed from storage. Documents under a legal hold cannot be deleted, and the deletion itself is written to the audit log."
        confirmLabel="Delete document"
        label="Reason for deleting"
        busy={remove.isPending}
        error={remove.isError ? remove.error : null}
        onConfirm={(reason) => {
          if (!deleting) return
          remove
            .mutateAsync({ id: deleting.public_id, reason })
            .catch((cause) => notifyError(cause, 'The document could not be deleted.'))
        }}
      />
    </PageShell>
  )
}

/* -------------------------------------------------------------------------- */
/* Upload                                                                     */
/* -------------------------------------------------------------------------- */

function UploadDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onDone: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [file, setFile] = React.useState<File | null>(null)
  const [title, setTitle] = React.useState('')
  const [docType, setDocType] = React.useState('OTHER')
  const [description, setDescription] = React.useState('')
  const [relatedType, setRelatedType] = React.useState('')
  const [relatedId, setRelatedId] = React.useState('')
  const [visibility, setVisibility] = React.useState('CONNECTIONS')
  const [errors, setErrors] = React.useState<Record<string, string>>({})
  const [busy, setBusy] = React.useState(false)

  React.useEffect(() => {
    if (open) {
      setFile(null)
      setTitle('')
      setDocType('OTHER')
      setDescription('')
      setRelatedType('')
      setRelatedId('')
      setVisibility('CONNECTIONS')
      setErrors({})
    }
  }, [open])

  const submit = async () => {
    const next: Record<string, string> = {}
    if (!file) next.file = 'Choose a file to upload.'
    if (title.trim().length < 2) next.title = 'Give the document a title of at least 2 characters.'
    setErrors(next)
    if (Object.keys(next).length > 0) return

    setBusy(true)
    try {
      const result = await uploadDocument({
        companyPublicId: activeCompanyPublicId,
        file: file as File,
        title: title.trim(),
        docType,
        ...(description ? { description } : {}),
        ...(relatedType ? { relatedType } : {}),
        ...(relatedId ? { relatedPublicId: relatedId } : {}),
        visibility,
      })
      notifySuccess('Uploaded.', `${result.public_id} is now in the library.`)
      onOpenChange(false)
      onDone()
    } catch (cause) {
      notifyError(cause, 'The upload was rejected.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Upload a document"
      description="The file is scanned and stored by the platform. Nothing is uploaded to a third party."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button loading={busy} onClick={() => void submit()}>
            Upload
          </Button>
        </>
      }
    >
      <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
        <Field label="File" required error={errors.file} hint={file ? `${file.name} · ${Math.round(file.size / 1024)} KB` : undefined}>
          <FilePicker id="doc-file" label={file ? 'Choose a different file' : 'Choose a file'} onSelect={setFile} disabled={busy} />
        </Field>

        <Field label="Title" required error={errors.title}>
          <Input
            id="doc-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            aria-invalid={Boolean(errors.title)}
            placeholder="Signed master service agreement"
          />
        </Field>

        <Field label="Type" required>
          <Select id="doc-type-input" value={docType} onChange={(event) => setDocType(event.target.value)}>
            {[...DOC_TYPES].map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Description">
          <Input
            id="doc-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Related to" hint="Optional">
            <Select
              id="doc-related-type"
              value={relatedType}
              onChange={(event) => setRelatedType(event.target.value)}
            >
              <option value="">Not related</option>
              {['PROJECT', 'CONTRACT', 'SOW', 'INVOICE', 'COMPANY', 'MEMBER'].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Related public id" hint="The P…, C…, S… or I… it belongs to">
            <Input
              id="doc-related-id"
              value={relatedId}
              onChange={(event) => setRelatedId(event.target.value.toUpperCase())}
              className="font-mono text-xs"
              placeholder="P01H8KM2Q"
            />
          </Field>
        </div>

        <Field label="Visibility" hint="Who can see this once it is shared.">
          <Select id="doc-visibility" value={visibility} onChange={(event) => setVisibility(event.target.value)}>
            <option value="PRIVATE">Private to this company</option>
            <option value="CONNECTIONS">This company and its connections</option>
            <option value="PUBLIC">Public</option>
          </Select>
        </Field>
      </div>
    </Dialog>
  )
}

/* -------------------------------------------------------------------------- */
/* Document detail                                                            */
/* -------------------------------------------------------------------------- */

/**
 * A document with its versions and access log.
 *
 * Downloads are fetched on demand through the signed-URL endpoint: the URL is
 * short-lived and the request is written to the access log, so it is never
 * embedded in the page.
 */
function DocumentDetailDialog({
  document,
  onClose,
}: {
  document: DocumentSummary | null
  onClose: () => void
}) {
  const { activeCompanyPublicId, can } = useCompany()

  const detail = useCompanyQuery<DocumentSummary & { versions?: unknown[] }>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['documents', 'detail', document?.public_id],
    path: `/documents/${document?.public_id ?? ''}`,
    enabled: Boolean(document),
  })

  const accessLog = useCompanyQuery<Record<string, unknown>[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['documents', 'access-log', document?.public_id],
    path: `/documents/${document?.public_id ?? ''}/access-log`,
    enabled: Boolean(document) && can('documents.read_audit'),
  })

  const [downloadError, setDownloadError] = React.useState<string | null>(null)
  const [downloading, setDownloading] = React.useState(false)

  React.useEffect(() => {
    setDownloadError(null)
  }, [document?.public_id])

  const data: (DocumentSummary & { versions?: unknown[] }) | null = document ? (detail.data ?? document) : null

  const download = async () => {
    if (!document) return
    setDownloading(true)
    setDownloadError(null)
    try {
      const link = await api.get<{ url: string }>(
        `/documents/${document.public_id}/download`,
        { companyPublicId: activeCompanyPublicId },
      )
      window.open(link.url, '_blank', 'noopener,noreferrer')
    } catch (cause) {
      setDownloadError(errorMessage(cause))
      notifyError(cause, 'A download link could not be created.')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <Dialog
      open={Boolean(document)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={data?.title ?? 'Document'}
      description={data?.description ?? undefined}
      className="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button onClick={() => void download()} loading={downloading}>
            Download
          </Button>
        </>
      }
    >
      {detail.isPending ? (
        <LoadingBlock rows={6} />
      ) : detail.isError ? (
        <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
      ) : data ? (
        <div className="max-h-[65vh] space-y-5 overflow-y-auto pr-1">
          {downloadError ? (
            <p role="alert" className="text-xs font-medium text-danger">
              {downloadError}
            </p>
          ) : null}

          <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <Detail label="Document ID" value={data.public_id} mono />
            <Detail label="Type" value={data.doc_type} />
            <Detail label="File" value={data.file_name ?? '—'} />
            <Detail
              label="Size"
              value={data.byte_size !== null ? `${Math.round(data.byte_size / 1024)} KB` : '—'}
            />
            <Detail label="Versions" value={String(data.version_count)} />
            <Detail label="Visibility" value={data.visibility} />
            <Detail label="Added" value={formatDateTime(data.created_at)} />
            <Detail label="Updated" value={formatDateTime(data.updated_at)} />
            {data.related_type ? (
              <Detail
                label="Related to"
                value={`${data.related_type} ${data.related_id ?? ''}`}
              />
            ) : null}
            {data.retention_until ? (
              <Detail label="Retained until" value={formatDate(data.retention_until)} />
            ) : null}
          </dl>

          {data.is_legal_hold ? (
            <p className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm">
              This document is under a legal hold. It cannot be deleted and its
              retention period is suspended until the hold is lifted.
            </p>
          ) : null}

          {Array.isArray(data.versions) && data.versions.length > 0 ? (
            <section>
              <h3 className="text-sm font-semibold">Versions</h3>
              <ul className="mt-2 divide-y divide-border/60">
                {data.versions.map((version, index) => {
                  const row = version as Record<string, unknown>
                  return (
                    <li key={index} className="flex items-center justify-between gap-3 py-2 text-sm">
                      <span>
                        v{String(row.version_no ?? row.version ?? index + 1)} ·{' '}
                        {String(row.file_name ?? '')}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {row.created_at ? formatDate(String(row.created_at)) : ''}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </section>
          ) : null}

          {accessLog.data ? (
            <section>
              <h3 className="text-sm font-semibold">Access log</h3>
              <p className="text-xs text-muted-foreground">
                Every time this document has been viewed or downloaded.
              </p>
              <ul className="mt-2 divide-y divide-border/60">
                {accessLog.data.slice(0, 20).map((entry, index) => (
                  <li key={index} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span>{String(entry.action ?? 'access')}</span>
                    <span className="text-xs text-muted-foreground">
                      {entry.at || entry.created_at
                        ? formatDateTime(String(entry.at ?? entry.created_at))
                        : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </Dialog>
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