'use client'

import Link from 'next/link'
import {
  Briefcase,
  Building2,
  CreditCard,
  FileText,
  LogOut,
  Sparkles,
} from 'lucide-react'

import { getSupabase } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { PageShell } from '@/components/page'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui'
import { ProfessionalFeed } from '@/components/post-feed'
import { RightRail } from './rail'

const MODULES = [
  { href: '/time', label: 'Work', icon: Briefcase },
  { href: '/companies', label: 'Business', icon: Building2 },
  { href: '/contracts', label: 'Contracts', icon: FileText },
  { href: '/payments', label: 'Payments', icon: CreditCard },
  { href: '/assistant', label: 'AI', icon: Sparkles },
] as const

/**
 * Home after sign-in: the professional feed in three columns.
 *
 * The centre column is the real posts feed (own, connection and public posts
 * with reactions, comments and shares). The left rail jumps to the product
 * modules; the right rail introduces people and companies from the live
 * professional graph. Nothing here is mock content.
 */
export default function FeedPage() {
  const { me } = useCompany()

  const signOut = async () => {
    await getSupabase().auth.signOut()
    window.location.href = '/login'
  }

  return (
    <PageShell width="wide">
      <h1 className="sr-only">Feed</h1>
      <div className="grid items-start gap-4 lg:grid-cols-[13rem_minmax(0,1fr)_19rem]">
        <Card className="max-lg:order-2">
          <CardHeader>
            <CardTitle>Modules</CardTitle>
          </CardHeader>
          <CardContent className="space-y-0.5">
            <nav aria-label="Product modules">
              {MODULES.map((module) => (
                <Link
                  key={module.href}
                  href={module.href}
                  className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors hover:bg-muted"
                >
                  <module.icon aria-hidden className="size-4 text-muted-foreground" />
                  {module.label}
                </Link>
              ))}
            </nav>
            <div className="pt-2">
              <button
                type="button"
                onClick={() => void signOut()}
                className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-danger transition-colors hover:bg-danger-soft"
              >
                <LogOut aria-hidden className="size-4" />
                Logout
              </button>
            </div>
          </CardContent>
        </Card>

        <div className="min-w-0 max-lg:order-1">
          <ProfessionalFeed mePublicId={me?.public_id ?? null} />
        </div>

        <div className="min-w-0 max-lg:order-3">
          <RightRail />
        </div>
      </div>
    </PageShell>
  )
}
