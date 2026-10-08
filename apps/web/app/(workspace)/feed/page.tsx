'use client'

import { Feed } from './feed'
import { PageHeader, PageShell } from '@/components/page'

/**
 * The activity feed.
 *
 * There is no posts endpoint in this build, so the surface is composed from what
 * does exist: your notifications, plus the waiting-on-you counts and recent
 * insights the company dashboard already assembles. It is a real feed of real
 * events rather than a placeholder.
 */
export default function FeedPage() {
  return (
    <PageShell width="wide">
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Activity' }]}
          title="Activity"
          description="What needs your attention and what has changed, in one place."
        />
        <Feed />
      </div>
    </PageShell>
  )
}