import type { ReactNode } from 'react'
import {
  Bell,
  Building2,
  Clock,
  CreditCard,
  FolderKanban,
  Home,
  MessageCircle,
  Sparkles,
  User,
  Users,
} from 'lucide-react'

import { cn } from '@/lib/utils'
import { useCompany } from '@/hooks/use-company'

/**
 * The workspace module map.
 *
 * Each entry pairs a permission with a route. `can()` gates navigation for
 * clarity only; the API re-checks the same permission on every request, so a
 * hidden link is a courtesy rather than the control. A link that is visible but
 * refused shows a permission state on arrival rather than a broken page.
 */
export interface NavItem {
  href: string
  label: string
  icon: ReactNode
  /**
   * The permission required to see this link. Omit only for screens that any
   * signed-in company member may open, such as global search.
   */
  permission?: string
  /** When set, the nav hides the link unless the caller holds *any* of these. */
  anyPermission?: string[]
  badge?: 'approvals' | 'notifications'
}

export interface NavSection {
  label: string
  items: NavItem[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Platform Modules',
    items: [
      { href: '/feed', label: 'Feed (Home)', icon: <Home aria-hidden /> },
      {
        href: '/timesheets',
        label: 'Work (Timesheets)',
        icon: <Clock aria-hidden />,
      },
      {
        href: '/companies',
        label: 'Business (Companies)',
        icon: <Building2 aria-hidden />,
      },
      {
        href: '/code',
        label: 'Code (Projects/SOW/Contracts)',
        icon: <FolderKanban aria-hidden />,
      },
      {
        href: '/invoices',
        label: 'Payments (Invoices)',
        icon: <CreditCard aria-hidden />,
      },
      {
        href: '/assistant',
        label: 'AI (Insights & Assistant)',
        icon: <Sparkles aria-hidden />,
      },
      {
        href: '/messages',
        label: 'Messages',
        icon: <MessageCircle aria-hidden />,
      },
      {
        href: '/network',
        label: 'Connections',
        icon: <Users aria-hidden />,
      },
      {
        href: '/notifications',
        label: 'Notifications',
        icon: <Bell aria-hidden />,
        badge: 'notifications',
      },
      {
        href: '/settings/profile',
        label: 'Profile',
        icon: <User aria-hidden />,
      },
    ],
  },
]


export function isNavItemActive(item: NavItem, pathname: string): boolean {
  if (item.href === '/dashboard') return pathname === '/dashboard'
  // `/time` and `/timesheets` both start with `/time`, so an exact match is
  // required for the shorter one or they would both light up.
  if (item.href === '/time') return pathname === '/time'
  return pathname === item.href || pathname.startsWith(`${item.href}/`)
}

export function useVisibleNavigation(): NavSection[] {
  const { can } = useCompany()

  return NAV_SECTIONS.map((section) => ({
    label: section.label,
    items: section.items.filter((item) => {
      if (item.permission && !can(item.permission)) return false
      if (item.anyPermission && !item.anyPermission.some((p) => can(p))) return false
      return true
    }),
  })).filter((section) => section.items.length > 0)
}

export function badgeCountFor(
  item: NavItem,
  unread: { notifications: number; approvals: number },
): number {
  if (item.badge === 'notifications') return unread.notifications
  if (item.badge === 'approvals') return unread.approvals
  return 0
}

/** Compact nav used by the command palette and the mobile drawer. */
export function flatNavigation(): NavItem[] {
  return NAV_SECTIONS.flatMap((section) => section.items)
}

/** True when the caller may see this link at all, for search and command results. */
export function isNavigationVisible(item: NavItem, permissions: string[]): boolean {
  const has = (permission: string) => permissions.includes(permission)
  if (item.permission && !has(item.permission)) return false
  if (item.anyPermission && !item.anyPermission.some(has)) return false
  return true
}

export function NavLink({
  item,
  active,
  badge,
}: {
  item: NavItem
  active: boolean
  badge?: number
}) {
  return (
    <a
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
        active
          ? 'bg-primary-soft text-primary-strong'
          : 'text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      <span className={cn('[&_svg]:size-4', active ? 'text-primary' : 'text-muted-foreground')}>
        {item.icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
      {typeof badge === 'number' && badge > 0 ? (
        <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-2xs font-semibold text-primary-foreground">
          {badge > 99 ? '99+' : badge}
        </span>
      ) : null}
    </a>
  )
}

export { Bell }