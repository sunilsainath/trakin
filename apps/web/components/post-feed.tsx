'use client'

import * as React from 'react'
import { z } from 'zod'

import { api } from '@/lib/api'
import { Alert, Button, Card, CardContent, EmptyState } from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { ErrorState, LoadingBlock } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'
import { formatRelative } from '@/lib/utils'

export interface FeedPost {
  public_id: string
  author_public_id: string
  author_name: string | null
  content: string
  post_type: string
  reaction_count: number
  comment_count: number
  share_count: number
  created_at: string
}

export interface FeedComment {
  id: string
  author_public_id: string
  author_name: string | null
  content: string
  created_at: string
}

const postSchema = z.object({
  content: z.string().min(1, 'Write something.').max(5000),
})

const postFields: FieldConfig[] = [{ name: 'content', label: 'Share an update' }]

const commentSchema = z.object({
  content: z.string().min(1, 'Write a comment.').max(2000),
})

const commentFields: FieldConfig[] = [{ name: 'content', label: 'Add a comment' }]

const REACTIONS = ['LIKE', 'CELEBRATE', 'INSIGHTFUL'] as const

/**
 * Professional post feed shared by the home feed and the network page.
 *
 * Reads and writes through the real posts API (`GET /posts`, reactions,
 * comments, shares). Visibility is enforced server-side, so this component
 * never filters rows itself.
 */
export function ProfessionalFeed({
  mePublicId,
  companyPublicId,
}: {
  mePublicId: string | null
  companyPublicId: string | null
}) {
  const [posts, setPosts] = React.useState<FeedPost[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)

  const load = React.useCallback(async () => {
    if (!companyPublicId) return
    try {
      setError(null)
      const feed = await api.get<{ data: FeedPost[] }>('/posts?limit=25', {
        companyPublicId,
      })
      setPosts(feed.data)
    } catch (cause) {
      setError(cause)
    }
  }, [companyPublicId])

  React.useEffect(() => {
    void load()
  }, [load])

  if (!companyPublicId) {
    return (
      <EmptyState
        title="Select a company to continue"
        description="Posts load in a company context. Once you belong to one, pick it in the header to see your network's posts."
      />
    )
  }

  if (error) {
    return <ErrorState error={error} onRetry={() => void load()} />
  }
  if (posts === null) {
    return <LoadingBlock />
  }
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-5">
          <SchemaForm<{ content: string }>
            schema={postSchema}
            fields={postFields}
            defaultValues={{ content: '' }}
            submitLabel="Post"
            onSubmit={async (values) => {
              await api.post('/posts', { content: values.content }, { companyPublicId })
              notifySuccess('Posted to your network.')
              await load()
            }}
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
          <PostCard
            key={post.public_id}
            post={post}
            mePublicId={mePublicId}
            companyPublicId={companyPublicId}
            onChanged={() => void load()}
          />
        ))
      )}
    </div>
  )
}

function PostCard({
  post,
  mePublicId,
  companyPublicId,
  onChanged,
}: {
  post: FeedPost
  mePublicId: string | null
  companyPublicId: string
  onChanged: () => void
}) {
  const [commentsOpen, setCommentsOpen] = React.useState(false)
  const [comments, setComments] = React.useState<FeedComment[] | null>(null)
  const [commentsError, setCommentsError] = React.useState<unknown>(null)
  const [busy, setBusy] = React.useState(false)

  const loadComments = React.useCallback(async () => {
    try {
      setCommentsError(null)
      const rows = await api.get<{ data: FeedComment[] }>(
        `/posts/${post.public_id}/comments?limit=50`,
        { companyPublicId },
      )
      setComments(rows.data)
    } catch (cause) {
      setCommentsError(cause)
    }
  }, [post.public_id, companyPublicId])

  const toggleComments = () => {
    const next = !commentsOpen
    setCommentsOpen(next)
    if (next && comments === null) void loadComments()
  }

  const react = async (reaction: string) => {
    setBusy(true)
    try {
      await api.post(`/posts/${post.public_id}/reactions`, { reaction }, { companyPublicId })
      onChanged()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  const share = async () => {
    setBusy(true)
    try {
      await api.post(`/posts/${post.public_id}/shares`, {}, { companyPublicId })
      notifySuccess('Shared with your network.')
      onChanged()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardContent className="space-y-2 pt-5">
        <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">
            {mePublicId !== null && post.author_public_id === mePublicId
              ? 'You'
              : (post.author_name ?? post.author_public_id)}
          </span>
          <span>{formatRelative(post.created_at)}</span>
        </div>
        <p className="whitespace-pre-wrap text-sm">{post.content}</p>
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {REACTIONS.map((kind) => (
            <Button
              key={kind}
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => void react(kind)}
            >
              {kind.toLowerCase()}
            </Button>
          ))}
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => void share()}>
            Share{post.share_count > 0 ? ` · ${post.share_count}` : ''}
          </Button>
          <Button variant="ghost" size="sm" onClick={toggleComments}>
            {commentsOpen ? 'Hide' : 'Show'} comments · {post.comment_count}
          </Button>
          <span className="text-xs text-muted-foreground">
            {post.reaction_count} reactions
          </span>
        </div>
        {commentsOpen ? (
          <div className="space-y-3 border-t border-border pt-3">
            {commentsError ? (
              <Alert tone="danger">Could not load comments.</Alert>
            ) : comments === null ? (
              <LoadingBlock rows={2} />
            ) : comments.length === 0 ? (
              <p className="text-sm text-muted-foreground">No comments yet.</p>
            ) : (
              <ul className="space-y-2">
                {comments.map((comment) => (
                  <li key={comment.id} className="rounded-md bg-muted px-3 py-2">
                    <p className="text-xs font-medium">
                      {mePublicId !== null && comment.author_public_id === mePublicId
                        ? 'You'
                        : (comment.author_name ?? comment.author_public_id)}{' '}
                      <span className="font-normal text-muted-foreground">
                        · {formatRelative(comment.created_at)}
                      </span>
                    </p>
                    <p className="mt-0.5 whitespace-pre-wrap text-sm">{comment.content}</p>
                  </li>
                ))}
              </ul>
            )}
            <SchemaForm<{ content: string }>
              schema={commentSchema}
              fields={commentFields}
              defaultValues={{ content: '' }}
              submitLabel="Comment"
              onSubmit={async (values) => {
                await api.post(
                  `/posts/${post.public_id}/comments`,
                  {
                    content: values.content,
                  },
                  { companyPublicId },
                )
                await loadComments()
                onChanged()
              }}
              banner={null}
            />
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
