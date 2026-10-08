'use client'

import * as React from 'react'
import Link from 'next/link'
import { Bell, CheckCheck } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { formatRelative } from '@/lib/utils'
import type { NotificationItem } from '@/lib/types'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
} from '@/components/ui'
import { PublicId } from '@/components/public-id'
import { ErrorState, LoadingBlock, PermissionState, isPermissionError } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'
import { PageHeader, PageShell } from '@/components/page'

/**
 * The notification inbox.
 *
 * Reads and marks through the user-scoped notification endpoints, so it works
 * with or without a company in context: the rows belong to the user, not to
 * a tenant. The bell in the shell links here.
 */
export default function NotificationsPage() {
  const { activeCompanyPublicId } = useCompany()
  const [items, setItems] = React.useState<NotificationItem[] | null>(null)
  const [error, setError] = React.useState<unknown>(null)
  const [unreadOnly, setUnreadOnly] = React.useState(false)
  const [busy, setBusy] = React.useState(false)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const rows = await api.get<{ data: NotificationItem[] }>(
        '/notifications?limit=50',
        { companyPublicId: activeCompanyPublicId },
      )
      setItems(rows.data)
    } catch (cause) {
      setError(cause)
    }
  }, [activeCompanyPublicId])

  React.useEffect(() => {
    void load()
  }, [load])

  const markAll = async () => {
    setBusy(true)
    try {
      await api.post(
        '/notifications/read',
        undefined,
        { companyPublicId: activeCompanyPublicId },
      )
      notifySuccess('All notifications marked as read.')
      await load()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  const markOne = async (publicId: string) => {
    try {
      await api.post('/notifications/read', [publicId], {
        companyPublicId: activeCompanyPublicId,
      })
      await load()
    } catch (cause) {
      notifyError(cause, 'That could not be marked read.')
    }
  }

  const visible = (items ?? []).filter((item) => !unreadOnly || !item.read)

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Notifications' }]}
          title="Notifications"
          description="Everything the platform has flagged for you."
        />
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Bell aria-hidden className="size-4 text-primary" />
                  Inbox
                </CardTitle>
                <CardDescription>Approvals, disputes and anything needing a decision.</CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={unreadOnly}
                    onChange={(event) => setUnreadOnly(event.target.checked)}
                    className="size-3.5 accent-primary"
                  />
                  Unread only
                </label>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void markAll()}
                  loading={busy}
                  disabled={visible.length === 0}
                >
                  <CheckCheck aria-hidden />
                  Mark all read
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {error ? (
              isPermissionError(error) ? (
                <PermissionState error={error} />
              ) : (
                <ErrorState error={error} onRetry={() => void load()} />
              )
            ) : items === null ? (
              <LoadingBlock rows={6} />
            ) : visible.length === 0 ? (
              <EmptyState
                icon={<Bell aria-hidden />}
                title={unreadOnly ? 'Nothing unread' : 'No notifications yet'}
                description="Approvals, disputes and anything that needs a decision will appear here."
              />
            ) : (
              <ul className="divide-y divide-border/60">
                {visible.map((notification) => (
                  <li
                    key={notification.public_id}
                    className="flex flex-wrap items-start justify-between gap-3 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge
                          tone={
                            notification.severity === 'CRITICAL'
                              ? 'danger'
                              : notification.severity === 'WARNING'
                                ? 'warning'
                                : notification.severity === 'SUCCESS'
                                  ? 'success'
                                  : 'neutral'
                          }
                        >
                          {notification.severity.toLowerCase()}
                        </Badge>
                        <p className="text-sm font-medium">{notification.title}</p>
                        {!notification.read ? (
                          <span
                            aria-label="Unread"
                            className="size-2 shrink-0 rounded-full bg-primary"
                          />
                        ) : null}
                      </div>
                      {notification.body ? (
                        <p className="mt-0.5 text-sm text-muted-foreground">{notification.body}</p>
                      ) : null}
                      <p className="mt-1 flex flex-wrap items-center gap-2 text-2xs text-subtle-foreground">
                        {notification.type}
                        {notification.resource_public_id ? (
                          <PublicId value={notification.resource_public_id} />
                        ) : null}
                        <span>{formatRelative(notification.created_at)}</span>
                      </p>
                    </div>
                    <div className="flex shrink-0 gap-1.5">
                      {notification.action_url ? (
                        <Link
                          href={notification.action_url}
                          className="inline-flex h-8 items-center rounded-md border border-border px-3 text-sm font-medium transition-colors hover:bg-muted"
                        >
                          Open
                        </Link>
                      ) : null}
                      {!notification.read ? (
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() => void markOne(notification.public_id)}
                        >
                          Mark read
                        </Button>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </PageShell>
  )
}
