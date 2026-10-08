'use client'

import * as React from 'react'
import { z } from 'zod'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { PageHeader, PageShell } from '@/components/page'
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
} from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { ErrorState, LoadingBlock } from '@/components/query'
import { formatRelative } from '@/lib/utils'

interface Post {
  public_id: string
  author_id: string
  author_name: string | null
  company_id: string | null
  company_public_id: string | null
  company_name: string | null
  content: string
  post_type: string
  reaction_count: number
  comment_count: number
  created_at: string
}

interface Connection {
  public_id: string
  display_name: string | null
  headline: string | null
}

const postSchema = z.object({
  content: z.string().min(1, 'Write something.').max(5000),
})

const postFields: FieldConfig[] = [{ name: 'content', label: 'Share an update' }]

export default function NetworkPage() {
  const { me, can, activeCompany, activeCompanyPublicId } = useCompany()
  const [posts, setPosts] = React.useState<Post[] | null>(null)
  const [connections, setConnections] = React.useState<Connection[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [invite, setInvite] = React.useState('')
  const [asCompany, setAsCompany] = React.useState(false)

  const canPublishAsCompany =
    activeCompany != null && activeCompanyPublicId != null && can('posts.create')

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const [feed, directory] = await Promise.all([
        api.get<{ data: Post[] }>('/posts?limit=25'),
        api.get<{ data: Connection[] }>('/connections?limit=100'),
      ])
      setPosts(feed.data)
      setConnections(directory.data)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  const publish = async (values: { content: string }) => {
    await api.post(
      '/posts',
      { content: values.content, ...(asCompany ? { as_company: true } : {}) },
      asCompany && activeCompanyPublicId
        ? { companyPublicId: activeCompanyPublicId }
        : undefined,
    )
    await load()
  }

  const react = async (publicId: string, reaction: string) => {
    await api.post(`/posts/${publicId}/reactions`, { reaction })
    await load()
  }

  const connect = async () => {
    const target = invite.trim()
    if (!target) return
    setNotice(null)
    try {
      await api.post('/connections/requests', { user_id: target })
      setInvite('')
      setNotice('Connection request sent.')
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'Could not send the request.')
    }
  }

  return (
    <PageShell>
      <PageHeader title="Network" description="Posts and professional connections." />
      {notice ? (
        <div className="mb-4">
          <Alert tone="info">{notice}</Alert>
        </div>
      ) : null}
      {error ? (
        <ErrorState error={error} onRetry={() => void load()} />
      ) : posts === null ? (
        <LoadingBlock />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <div className="space-y-4">
            <Card>
              <CardContent className="pt-5">
                {canPublishAsCompany ? (
                  <label className="mb-3 flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={asCompany}
                      onChange={(event) => setAsCompany(event.target.checked)}
                    />
                    <span>
                      Post as{' '}
                      <span className="font-medium">{activeCompany?.display_name}</span>
                    </span>
                  </label>
                ) : null}
                <SchemaForm<{ content: string }>
                  schema={postSchema}
                  fields={postFields}
                  defaultValues={{ content: '' }}
                  submitLabel={asCompany ? 'Publish as company' : 'Post'}
                  onSubmit={publish}
                  banner={null}
                />
              </CardContent>
            </Card>
            {posts.length === 0 ? (
              <EmptyState
                title="Quiet here"
                description="Be the first to post something your network should see."
              />
            ) : (
              posts.map((post) => (
                <Card key={post.public_id}>
                  <CardContent className="space-y-2 pt-5">
                    <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                        {post.company_public_id ? (
                          <Badge tone="primary">{post.company_name ?? post.company_public_id}</Badge>
                        ) : null}
                        <span className="font-medium text-foreground">
                          {post.author_id === me?.public_id
                            ? 'You'
                            : (post.author_name ?? post.author_id)}
                        </span>
                      </span>
                      <span className="shrink-0">{formatRelative(post.created_at)}</span>
                    </div>
                    <p className="whitespace-pre-wrap text-sm">{post.content}</p>
                    <div className="flex items-center gap-2 pt-1">
                      {['LIKE', 'CELEBRATE', 'INSIGHTFUL'].map((kind) => (
                        <Button
                          key={kind}
                          variant="outline"
                          size="sm"
                          onClick={() => void react(post.public_id, kind)}
                        >
                          {kind.toLowerCase()}
                        </Button>
                      ))}
                      <span className="text-xs text-muted-foreground">
                        {post.reaction_count} reactions · {post.comment_count} comments
                      </span>
                    </div>
                  </CardContent>
                </Card>
              ))
            )}
          </div>

          <div className="space-y-4">
            <AddCentre can={can} />
            <Card>
              <CardHeader>
                <CardTitle>Connection Suggestions · {connections?.length ?? 0}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex gap-2">
                  <input
                    value={invite}
                    onChange={(event) => setInvite(event.target.value)}
                    placeholder="User ID (U…)"
                    aria-label="User public id"
                    className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
                  />
                  <Button size="sm" onClick={() => void connect()}>
                    Connect
                  </Button>
                </div>
                <a
                  href="/search"
                  className="block text-sm text-primary hover:underline"
                >
                  Find people to connect with
                </a>
                <div className="space-y-2">
                  {(connections ?? []).map((connection) => (
                    <div key={connection.public_id} className="text-sm">
                      <p className="font-medium">
                        {connection.display_name ?? connection.public_id}
                      </p>
                      {connection.headline ? (
                        <p className="text-xs text-muted-foreground">{connection.headline}</p>
                      ) : null}
                    </div>
                  ))}
                  {(connections ?? []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">No connections yet.</p>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </PageShell>
  )
}

/**
 * Creation hub: every entry links to a real page that performs the action.
 * Entries render only when the caller holds the matching permission, so the
 * card never shows a button that would be refused — and never shows one for
 * users without a company where a company is required.
 */
function AddCentre({ can }: { can: (permission: string) => boolean }) {
  const items: { href: string; label: string; permission?: string }[] = [
    { href: '/network', label: 'Share an update' },
    { href: '/projects', label: 'New project', permission: 'projects.create' },
    { href: '/sows', label: 'New SOW', permission: 'sows.create' },
    { href: '/contracts', label: 'New contract', permission: 'contracts.create' },
    { href: '/documents', label: 'Upload document', permission: 'documents.upload' },
    {
      href: '/payments/accounts',
      label: 'Connect bank',
      permission: 'payments.connect_bank',
    },
  ]
  const visible = items.filter((item) => !item.permission || can(item.permission))
  if (visible.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add Centre</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-1">
          {visible.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
