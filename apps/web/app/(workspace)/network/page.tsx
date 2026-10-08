'use client'

import * as React from 'react'
import { z } from 'zod'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { PageHeader, PageShell } from '@/components/page'
import { Alert, Button, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { ErrorState, LoadingBlock } from '@/components/query'
import { formatRelative } from '@/lib/utils'

interface Post {
  public_id: string
  author_id: string
  author_name: string | null
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
  const { me } = useCompany()
  const [posts, setPosts] = React.useState<Post[] | null>(null)
  const [connections, setConnections] = React.useState<Connection[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [invite, setInvite] = React.useState('')

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
    await api.post('/posts', { content: values.content })
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
                <SchemaForm<{ content: string }>
                  schema={postSchema}
                  fields={postFields}
                  defaultValues={{ content: '' }}
                  submitLabel="Post"
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
                      <span className="font-medium text-foreground">
                        {post.author_id === me?.public_id
                          ? 'You'
                          : (post.author_name ?? post.author_id)}
                      </span>
                      <span>{formatRelative(post.created_at)}</span>
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

          <Card>
            <CardHeader>
              <CardTitle>Connections · {connections?.length ?? 0}</CardTitle>
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
      )}
    </PageShell>
  )
}
