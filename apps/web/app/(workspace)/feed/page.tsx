'use client'

import { useCompany } from '@/hooks/use-company'
import { PageShell } from '@/components/page'
import { ProfessionalFeed } from '@/components/post-feed'
import { RightRail } from './rail'

/**
 * Home after sign-in: the professional feed.
 *
 * The Platform Modules list is the persistent left sidebar in the app shell, so
 * this page is the feed plus the right-hand ads and connection suggestions,
 * exactly as the product flow lays it out. Nothing here is mock content.
 */
export default function FeedPage() {
  const { me, activeCompanyPublicId } = useCompany()

  return (
    <PageShell width="wide">
      <h1 className="sr-only">Feed</h1>
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_19rem]">
        <div className="min-w-0">
          <ProfessionalFeed
            mePublicId={me?.public_id ?? null}
            companyPublicId={activeCompanyPublicId}
          />
        </div>
        <div className="min-w-0">
          <RightRail companyPublicId={activeCompanyPublicId} />
        </div>
      </div>
    </PageShell>
  )
}
