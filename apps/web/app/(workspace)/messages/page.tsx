'use client'

import * as React from 'react'
import { z } from 'zod'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useRealtimeInsert } from '@/hooks/use-realtime'
import { PageHeader, PageShell } from '@/components/page'
import { Alert, Button, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { ErrorState, LoadingBlock } from '@/components/query'
import { formatRelative } from '@/lib/utils'

interface Thread {
  id: string
  public_id: string
  kind: string
  title: string | null
  last_message_at: string | null
  message_count: number
  unread_count: number
  peer_public_id: string | null
}

interface Message {
  id: string
  sender_public_id: string
  content: string | null
  document_id: string | null
  created_at: string
}

const messageSchema = z.object({
  content: z.string().min(1, 'Write a message.').max(5000),
})

const messageFields: FieldConfig[] = [{ name: 'content', label: 'Message' }]

export default function MessagesPage() {
  const { me } = useCompany()
  const [threads, setThreads] = React.useState<Thread[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [active, setActive] = React.useState<Thread | null>(null)
  const [messages, setMessages] = React.useState<Message[] | null>(null)
  const [notice, setNotice] = React.useState<string | null>(null)
  const [peer, setPeer] = React.useState('')

  const loadThreads = React.useCallback(async () => {
    try {
      setError(null)
      const rows = await api.get<{ data: Thread[] }>('/conversations')
      setThreads(rows.data)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void loadThreads()
  }, [loadThreads])

  const openThread = async (thread: Thread) => {
    setActive(thread)
    setMessages(null)
    const rows = await api.get<{ data: Message[] }>(
      `/conversations/${thread.public_id}/messages?limit=100`,
    )
    setMessages(rows.data)
    void loadThreads()
  }

  // Live thread: a peer message refetches the open conversation. Reading the
  // thread marks it read server-side, so badges settle without a reload.
  useRealtimeInsert({
    schema: 'public',
    table: 'messages',
    onInsert: () => {
      if (active) void openThread(active)
    },
    enabled: active !== null,
  })

  const startConversation = async () => {
    const target = peer.trim()
    if (!target) return
    setNotice(null)
    try {
      const thread = await api.post<Thread>('/conversations', { user_id: target })
      await loadThreads()
      setPeer('')
      await openThread({
        ...thread,
        kind: 'DIRECT',
        title: null,
        last_message_at: null,
        message_count: 0,
        unread_count: 0,
        peer_public_id: target,
      })
    } catch (cause) {
      setNotice(
        cause instanceof Error ? cause.message : 'Could not open that conversation.',
      )
    }
  }

  const send = async (values: { content: string }) => {
    if (!active) return
    await api.post(`/conversations/${active.public_id}/messages`, {
      content: values.content,
    })
    await openThread(active)
  }

  return (
    <PageShell>
      <PageHeader title="Messages" description="Direct conversations with your connections." />
      {notice ? (
        <div className="mb-4">
          <Alert tone="info">{notice}</Alert>
        </div>
      ) : null}
      <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Conversations</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex gap-2">
              <input
                value={peer}
                onChange={(event) => setPeer(event.target.value)}
                placeholder="User ID (U…) to message"
                aria-label="User public id"
                className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
              />
              <Button size="sm" onClick={() => void startConversation()}>
                New
              </Button>
            </div>
            {error ? (
              <ErrorState error={error} onRetry={() => void loadThreads()} />
            ) : threads === null ? (
              <LoadingBlock />
            ) : threads.length === 0 ? (
              <EmptyState
                title="No conversations"
                description="Connect with someone, then start messaging."
              />
            ) : (
              <div className="space-y-1">
                {threads.map((thread) => (
                  <button
                    key={thread.public_id}
                    type="button"
                    onClick={() => void openThread(thread)}
                    className={`flex w-full items-center justify-between gap-2 rounded-md px-3 py-2 text-left text-sm hover:bg-muted ${
                      active?.public_id === thread.public_id ? 'bg-muted' : ''
                    }`}
                  >
                    <span className="truncate font-mono text-xs">
                      {thread.peer_public_id ?? thread.public_id}
                    </span>
                    {thread.unread_count > 0 ? (
                      <span className="rounded-full bg-primary px-2 py-0.5 text-2xs text-primary-foreground">
                        {thread.unread_count}
                      </span>
                    ) : null}
                  </button>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{active ? 'Thread' : 'Select a conversation'}</CardTitle>
          </CardHeader>
          <CardContent>
            {!active ? (
              <EmptyState
                title="Nothing open"
                description="Pick a conversation on the left."
              />
            ) : messages === null ? (
              <LoadingBlock />
            ) : (
              <div className="space-y-3">
                <div className="max-h-[24rem] space-y-2 overflow-y-auto">
                  {messages.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No messages yet.</p>
                  ) : (
                    messages.map((message) => (
                      <div
                        key={message.id}
                        className={`max-w-[80%] rounded-lg border border-border px-3 py-2 text-sm ${
                          me?.public_id === message.sender_public_id
                            ? 'ml-auto bg-primary-soft'
                            : 'bg-surface'
                        }`}
                      >
                        <p>{message.content}</p>
                        <p className="mt-1 text-2xs text-subtle-foreground">
                          {formatRelative(message.created_at)}
                        </p>
                      </div>
                    ))
                  )}
                </div>
                <SchemaForm<{ content: string }>
                  schema={messageSchema}
                  fields={messageFields}
                  defaultValues={{ content: '' }}
                  submitLabel="Send"
                  onSubmit={send}
                  banner={null}
                />
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </PageShell>
  )
}
