import type { ReactNode } from 'react'
import {
  Building2,
  FolderKanban,
  FileSignature,
  FileText,
  Clock,
  CreditCard,
  Wallet,
  Sparkles,
  Users,
  ShieldCheck,
  Search,
  LayoutDashboard,
  Bell,
  Landmark,
  ArrowLeftRight,
  MessageCircle,
  FileStack,
  Handshake,
  BookOpen,
  Lightbulb,
  Zap,
  Settings,
  History,
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
    label: 'Workspace',
    items: [
      { href: '/dashboard', label: 'Dashboard', icon: <LayoutDashboard aria-hidden /> },
      { href: '/feed', label: 'Feed', icon: <History aria-hidden /> },
      {
        href: '/network',
        label: 'Network',
        icon: <Users aria-hidden />,
        permission: 'posts.read',
      },
      {
        href: '/messages',
        label: 'Messages',
        icon: <MessageCircle aria-hidden />,
        permission: 'messages.read',
      },
      {
        href: '/companies',
        label: 'Companies',
        icon: <Building2 aria-hidden />,
        permission: 'companies.read',
      },
      {
        href: '/people',
        label: 'People',
        icon: <Users aria-hidden />,
        permission: 'members.read',
      },
    ],
  },
  {
    label: 'Core',
    items: [
      {
        href: '/core',
        label: 'Overview',
        icon: <LayoutDashboard aria-hidden />,
        permission: 'dashboard.read',
      },
    ],
  },
  {
    label: 'Delivery',
    items: [
      {
        href: '/projects',
        label: 'Projects',
        icon: <FolderKanban aria-hidden />,
        permission: 'projects.read',
      },
      {
        href: '/sows',
        label: 'Statements of Work',
        icon: <FileSignature aria-hidden />,
        permission: 'sows.read',
      },
      {
        href: '/contracts',
        label: 'Contracts',
        icon: <FileText aria-hidden />,
        permission: 'contracts.read',
      },
      {
        href: '/time',
        label: 'My Time & Leave',
        icon: <Clock aria-hidden />,
        permission: 'timesheets.create',
      },
      {
        href: '/timesheets',
        label: 'All Timesheets',
        icon: <Clock aria-hidden />,
        permission: 'timesheets.read_any',
      },
      {
        href: '/leave',
        label: 'Leave',
        icon: <Clock aria-hidden />,
        anyPermission: ['leave.read_any', 'leave.approve'],
      },
    ],
  },
  {
    label: 'Commercial',
    items: [
      {
        href: '/invoices',
        label: 'Invoices',
        icon: <CreditCard aria-hidden />,
        permission: 'invoices.read',
        badge: 'approvals',
      },
      {
        href: '/billing',
        label: 'Billing',
        icon: <Wallet aria-hidden />,
        anyPermission: ['billing_runs.read', 'invoices.read', 'dashboard.read'],
      },
      {
        href: '/documents',
        label: 'Documents',
        icon: <FileStack aria-hidden />,
        permission: 'documents.read',
      },
      {
        href: '/msas',
        label: 'Agreements',
        icon: <Handshake aria-hidden />,
        permission: 'msas.read',
      },
      {
        href: '/payments',
        label: 'Payments',
        icon: <Landmark aria-hidden />,
        permission: 'payments.read',
      },
      {
        href: '/payments/accounts',
        label: 'Bank Accounts',
        icon: <Landmark aria-hidden />,
        permission: 'payments.connect_bank',
      },
      {
        href: '/payments/transactions',
        label: 'Bank Transactions',
        icon: <ArrowLeftRight aria-hidden />,
        permission: 'transactions.read',
      },
      {
        href: '/payments/reconciliation',
        label: 'Reconciliation',
        icon: <ArrowLeftRight aria-hidden />,
        permission: 'reconciliation.read',
      },
    ],
  },
  {
    label: 'Platform',
    items: [
      {
        href: '/assistant',
        label: 'AI Assistant',
        icon: <Sparkles aria-hidden />,
        permission: 'ai.assistant',
      },
      {
        href: '/ai/insights',
        label: 'AI Insights',
        icon: <Lightbulb aria-hidden />,
        permission: 'ai.insights.read',
      },
      {
        href: '/ai/actions',
        label: 'AI Actions',
        icon: <Zap aria-hidden />,
        permission: 'ai.read',
        badge: 'approvals',
      },
      {
        href: '/ai/automations',
        label: 'Automations',
        icon: <Zap aria-hidden />,
        permission: 'ai.read',
      },
      {
        href: '/ai/knowledge',
        label: 'Knowledge Base',
        icon: <BookOpen aria-hidden />,
        permission: 'ai.read',
      },
      { href: '/search', label: 'Global Search', icon: <Search aria-hidden /> },
      {
        href: '/settings',
        label: 'Settings',
        icon: <Settings aria-hidden />,
        permission: 'settings.read',
      },
      {
        href: '/settings/permissions',
        label: 'Roles & Permissions',
        icon: <ShieldCheck aria-hidden />,
        permission: 'roles.read',
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