'use client'

import * as React from 'react'
import Link from 'next/link'
import { Activity, Bell, CheckCheck, Sparkles } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatRelative, formatDateTime } from '@/lib/utils'
import type { DashboardResponse, Page as PageEnvelope } from '@/lib/domain-types'
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
import { StatusBadge } from '@/components/badges'
import { PublicId } from '@/components/public-id'
import { CursorFooter, useCursorList } from '@/components/list'
import { FilterCheckbox } from '@/components/filters'
import { ErrorState, LoadingBlock, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

/**
 * The activity feed.
 *
 * There is no posts endpoint in this build, so this surface is composed from what
 * does exist: your notifications, plus the recent activity panel the company
 * dashboard already assembles. It is a real feed of real events rather than a
 * placeholder.
 */
export function Feed() {
  const { activeCompanyPublicId, can } = useCompany()

  const list = useCursorList<PageEnvelope<NotificationItem>>({
    companyPublicId: activeCompanyPublicId,
    path: '/notifications',
    queryKey: ['notifications'],
    enabled: can('notifications.read'),
  })

  const dashboard = useCompanyQuery<DashboardResponse>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['dashboard'],
    path: '/dashboard',
    enabled: can('dashboard.read'),
  })

  const markAll = useCompanyMutation<unknown, void>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: () => api.post('/notifications/read', undefined, { companyPublicId: activeCompanyPublicId }),
    invalidate: [['notifications']],
    onSuccess: () => notifySuccess('All notifications marked as read.'),
  })

  const markOne = useCompanyMutation<unknown, string>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (id) =>
      api.post('/notifications/read', [id], { companyPublicId: activeCompanyPublicId }),
    invalidate: [['notifications']],
  })

  const notifications = list.query.data?.data ?? []
  const alerts = dashboard.data?.panels.ai_alerts ?? []
  const myWork = dashboard.data?.panels.me

  if (!can('notifications.read')) {
    return (
      <Card>
        <EmptyState
          icon={<Bell aria-hidden />}
          title="You do not have a notification inbox"
          description="Reading notifications requires notifications.read in this company. Your own work is shown on the dashboard."
          action={
            <Link href="/dashboard">
              <Button>Go to the dashboard</Button>
            </Link>
          }
        />
      </Card>
    )
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Bell aria-hidden className="size-4 text-primary" />
                  Notifications
                </CardTitle>
                <CardDescription>Everything the platform has flagged for you.</CardDescription>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => markAll.mutate()}
                loading={markAll.isPending}
                disabled={notifications.length === 0}
              >
                <CheckCheck aria-hidden />
                Mark all read
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="mb-4">
              <FilterCheckbox
                id="feed-unread"
                label="Unread only"
                checked={Boolean(list.filters.unread_only)}
                onChange={(checked) => list.setFilter('unread_only', checked)}
              />
            </div>

            {list.query.isPending ? (
              <LoadingBlock rows={6} />
            ) : list.query.isError ? (
              <ErrorState error={list.query.error} onRetry={() => void list.query.refetch()} />
            ) : notifications.length === 0 ? (
              <EmptyState
                icon={<Bell aria-hidden />}
                title={
                  list.filters.unread_only ? 'Nothing unread' : 'No notifications yet'
                }
                description="Approvals, disputes and anything that needs a decision will appear here."
              />
            ) : (
              <>
                <ul className="divide-y divide-border/60">
                  {notifications.map((notification) => (
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
                            onClick={() => {
                              markOne
                                .mutateAsync(notification.public_id)
                                .catch((cause) => notifyError(cause, 'That could not be marked read.'))
                            }}
                          >
                            Mark read
                          </Button>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>

                <div className="mt-4">
                  <CursorFooter
                    meta={list.query.data?.meta}
                    count={notifications.length}
                    onNext={list.next}
                    onPrevious={list.previous}
                    canGoBack={list.canGoBack}
                    busy={list.query.isFetching}
                    noun="notifications"
                  />
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity aria-hidden className="size-4 text-primary" />
              Waiting on you
            </CardTitle>
            <CardDescription>Items the platform is holding for a decision.</CardDescription>
          </CardHeader>
          <CardContent>
            {dashboard.isPending ? (
              <LoadingBlock rows={3} />
            ) : (
              <ul className="space-y-2 text-sm">
                <li className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Approvals</span>
                  <span
                    className={
                      (myWork?.pending_approvals ?? 0) > 0 ? 'font-semibold text-warning' : 'font-medium'
                    }
                  >
                    {myWork?.pending_approvals ?? 0}
                  </span>
                </li>
                <li className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Timesheets in review</span>
                  <span className="font-medium">
                    {dashboard.data?.panels.timesheets?.awaiting ?? 0}
                  </span>
                </li>
                <li className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Leave pending</span>
                  <span className="font-medium">
                    {dashboard.data?.panels.leave?.PENDING ?? 0}
                  </span>
                </li>
                <li className="flex items-center justify-between gap-3">
                  <span className="text-muted-foreground">Unmatched transactions</span>
                  <span className="font-medium">
                    {dashboard.data?.panels.finance?.reconciliation?.pending_suggestions ?? 0}
                  </span>
                </li>
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles aria-hidden className="size-4 text-primary" />
              Recent insights
            </CardTitle>
            <CardDescription>Findings from your own records.</CardDescription>
          </CardHeader>
          <CardContent>
            {dashboard.isPending ? (
              <LoadingBlock rows={3} />
            ) : alerts.length === 0 ? (
              <EmptyState
                title="No insights yet"
                description="Insights appear once there is enough activity to analyse."
              />
            ) : (
              <ul className="space-y-2.5">
                {alerts.slice(0, 6).map((alert) => (
                  <li key={alert.public_id} className="rounded-md border border-border p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-sm font-medium">{alert.title}</p>
                      <StatusBadge status={alert.severity} />
                    </div>
                    <p className="mt-0.5 text-xs text-muted-foreground">{alert.summary}</p>
                    <p className="mt-1 text-2xs text-subtle-foreground">
                      {formatDateTime(alert.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
