'use client'

import * as React from 'react'
import { usePathname, useRouter } from 'next/navigation'
import {
  Bell,
  Briefcase,
  Building2,
  Check,
  ChevronDown,
  FileSignature,
  FileText,
  FolderKanban,
  Home,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageCircle,
  Moon,
  Receipt,
  Search,
  Settings,
  Sparkles,
  Sun,
  User as UserIcon,
  Users,
  Wallet,
  X,
} from 'lucide-react'

import { cn, initials } from '@/lib/utils'
import { getSupabase } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useRealtimeInsert } from '@/hooks/use-realtime'
import { Button, ProgressBar } from '@/components/ui'
import { NAV_SECTIONS } from '@/components/navigation'

/**
 * Global application shell.
 *
 * Layout (per product spec):
 *
 *   HEADER:  Search | Connections | Messenger | Notifications | Profile
 *   LEFT:    Work | Business | Contracts | Payments | AI | Logout (modules)
 *   CENTER:  page content (the feed on /network)
 *   RIGHT:   page-owned sidebars (Add Centre, suggestions on /network)
 *
 * The network is personal: header and module links render for every signed-in
 * user, company or not. Company-gated controls (switcher, creation hub) appear
 * only when memberships exist; the API still re-checks every permission, so a
 * visible link is a courtesy rather than the control.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { me, unread, refreshUnread, companies, loading, can } = useCompany()

  // Badge nudge: realtime INSERTs refresh counts immediately; the 60s poll in
  // useCompany remains the source of truth. RLS scopes delivery to own rows.
  const [authId, setAuthId] = React.useState<string | null>(null)
  React.useEffect(() => {
    void getSupabase().auth.getUser().then(({ data }) => setAuthId(data.user?.id ?? null))
  }, [])
  useRealtimeInsert({
    schema: 'platform',
    table: 'notifications',
    filter: authId ? `user_id=eq.${authId}` : undefined,
    onInsert: () => void refreshUnread(),
    enabled: authId !== null,
  })

  const [mobileOpen, setMobileOpen] = React.useState(false)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [theme, setTheme] = React.useState<'light' | 'dark'>('light')

  // Close menus whenever the route changes.
  React.useEffect(() => {
    setMobileOpen(false)
    setMenuOpen(false)
  }, [pathname])

  // Light-first enterprise theme: the spec forbids black-heavy UI, so the OS
  // dark preference is never followed automatically. Users can still toggle
  // dark manually; the stored choice wins on every load.
  React.useEffect(() => {
    const stored = window.localStorage.getItem('mytrakin.theme')
    const preferred = stored === 'dark' || stored === 'light' ? stored : 'light'
    setTheme(preferred)
    document.documentElement.classList.toggle('dark', preferred === 'dark')
  }, [])

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    window.localStorage.setItem('mytrakin.theme', next)
    document.documentElement.classList.toggle('dark', next === 'dark')
  }

  const signOut = async () => {
    await getSupabase().auth.signOut()
    window.location.href = '/login'
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      {/* ------------------------------------------------------------ */}
      {/* Global header: Search | Connections | Messenger |              */}
      {/* Notifications | Profile                                      */}
      {/* ------------------------------------------------------------ */}
      <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-1 border-b border-border bg-surface/90 px-3 backdrop-blur sm:gap-2 sm:px-5">
        <Button
          variant="ghost"
          size="icon-sm"
          className="lg:hidden"
          aria-label="Open navigation"
          onClick={() => setMobileOpen(true)}
        >
          <Menu />
        </Button>

        <a href="/network" className="flex items-center gap-2" aria-label="MyTrakin home">
          <span
            aria-hidden
            className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-2xs font-bold text-primary-foreground"
          >
            MT
          </span>
          <span className="hidden text-sm font-semibold tracking-tight sm:inline">MyTrakin</span>
        </a>

        <HeaderSearch />

        <div className="flex-1" />

        <HeaderLink href="/network" label="Connections" active={pathname.startsWith('/network')}>
          <Users />
        </HeaderLink>
        <HeaderLink
          href="/messages"
          label="Messenger"
          active={pathname.startsWith('/messages')}
          badge={unread.messages}
        >
          <MessageCircle />
        </HeaderLink>
        <HeaderLink
          href="/notifications"
          label="Notifications"
          active={pathname.startsWith('/notifications')}
          badge={unread.notifications}
        >
          <Bell />
        </HeaderLink>

        <Button
          variant="ghost"
          size="icon-sm"
          onClick={toggleTheme}
          aria-label={theme === 'dark' ? 'Use light theme' : 'Use dark theme'}
        >
          {theme === 'dark' ? <Sun /> : <Moon />}
        </Button>

        {/* Profile */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            aria-label="Profile"
            className="flex h-9 items-center gap-2 rounded-md px-1.5 transition-colors hover:bg-muted"
          >
            <Avatar />
          </button>

          {menuOpen ? (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} aria-hidden />
              <div
                role="menu"
                aria-label="Profile"
                className="absolute right-0 top-11 z-20 w-60 rounded-lg border border-border bg-surface p-1 shadow-popover"
              >
                <div className="border-b border-border px-3 py-2.5">
                  <p className="truncate text-sm font-medium">
                    {me ? `${me.first_name} ${me.last_name}` : 'Account'}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">{me?.email}</p>
                </div>

                <MenuLink href="/settings/profile" icon={<UserIcon />}>
                  Profile
                </MenuLink>
                <MenuLink href="/settings/security" icon={<Settings />}>
                  Security
                </MenuLink>
                <MenuLink href="/settings/notifications" icon={<Bell />}>
                  Notifications
                </MenuLink>

                <div className="my-1 h-px bg-border" />

                <button
                  type="button"
                  role="menuitem"
                  onClick={signOut}
                  className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-danger transition-colors hover:bg-danger-soft"
                >
                  <LogOut aria-hidden className="size-4" />
                  Sign out
                </button>
              </div>
            </>
          ) : null}
        </div>
      </header>

      {/* ------------------------------------------------------------ */}
      {/* Body row: module rail left, page content center                */}
      {/* ------------------------------------------------------------ */}
      <div id="main" className="flex min-w-0 flex-1">
        <aside
          aria-label="Modules"
          className={cn(
            'fixed inset-y-0 left-0 top-14 z-40 flex w-64 flex-col border-r border-border bg-surface',
            'transition-transform duration-200 lg:static lg:translate-x-0',
            mobileOpen ? 'translate-x-0 shadow-popover' : '-translate-x-full',
          )}
        >
          <div className="flex shrink-0 items-center justify-between border-b border-border p-3 lg:hidden">
            <span className="px-1 text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
              Modules
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
            >
              <X />
            </Button>
          </div>
          {companies.length > 0 ? (
            <div className="shrink-0 border-b border-border p-3">
              <CompanySwitcher />
            </div>
          ) : null}

          <nav aria-label="Modules" className="flex-1 space-y-4 overflow-y-auto p-3 scrollbar-thin">
            {RAIL.map((section, index) => {
              const items = section.items.filter((item) => !item.permission || can(item.permission))
              if (items.length === 0) return null
              return (
                <div key={section.label ?? `section-${index}`}>
                  {section.label ? (
                    <p className="px-3 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                      {section.label}
                    </p>
                  ) : null}
                  <div className="space-y-0.5">
                    {items.map((item) => {
                      const active =
                        pathname === item.href || pathname.startsWith(`${item.href}/`)
                      const children = (item.children ?? []).filter(
                        (child) => !child.permission || can(child.permission),
                      )
                      return (
                        <div key={item.href}>
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
                            <span
                              className={cn(
                                '[&_svg]:size-4',
                                active ? 'text-primary' : 'text-muted-foreground',
                              )}
                            >
                              {item.icon}
                            </span>
                            {item.label}
                          </a>
                          {children.length > 0 ? (
                            <div className="ml-6 space-y-0.5 border-l border-border pl-2">
                              {children.map((child) => (
                                <a
                                  key={child.href}
                                  href={child.href}
                                  className="block truncate rounded-md px-2 py-1 text-[13px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                                >
                                  {child.label}
                                </a>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </nav>

          <div className="shrink-0 space-y-3 border-t border-border p-3">
            <div>
              <p className="px-3 pb-2 text-2xs text-subtle-foreground">
                {me?.onboarding_completed ? 'Workspace ready' : 'Finish onboarding'}
              </p>
              <ProgressBar
                value={me?.onboarding_completed ? 100 : 40}
                tone={me?.onboarding_completed ? 'success' : 'primary'}
                label="Onboarding progress"
              />
            </div>
            <button
              type="button"
              onClick={signOut}
              className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <span aria-hidden className="[&_svg]:size-4">
                <LogOut />
              </span>
              Logout
            </button>
          </div>
        </aside>

        {mobileOpen ? (
          <div
            className="fixed inset-0 z-30 bg-foreground/40 lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
        ) : null}

        <div className="flex min-w-0 flex-1 flex-col">
          {!loading && companies.length === 0 ? (
            <div className="border-b border-border bg-primary-soft px-3 py-2 text-center text-sm sm:px-5">
              <span className="text-primary-strong">
                You are networking personally —{' '}
                <a href="/onboarding" className="font-medium underline">
                  create a company
                </a>{' '}
                to unlock projects, contracts and billing.
              </span>
            </div>
          ) : null}
          <main className="min-w-0 flex-1">{children}</main>
        </div>
      </div>
    </div>
  )
}

/**
 * Module rail.
 *
 * CODE (Projects → SOW → Contracts → Invoices) is the commercial spine and gets
 * its own section with status deep-links; every link lands on a list the API
 * can actually filter, so no entry is decorative. Children render only for
 * permissions the caller holds.
 */
interface RailChild {
  href: string
  label: string
  permission?: string
}

interface RailSection {
  label: string | null
  items: (RailChild & { icon: React.ReactNode; children?: RailChild[] })[]
}

const RAIL: RailSection[] = [
  {
    label: null,
    items: [{ href: '/network', label: 'Feed', icon: <Home aria-hidden /> }],
  },
  {
    label: 'CODE',
    items: [
      { href: '/code', label: 'Overview', icon: <LayoutDashboard aria-hidden /> },
      {
        href: '/projects',
        label: 'Projects',
        icon: <FolderKanban aria-hidden />,
        permission: 'projects.read',
        children: [
          { href: '/projects', label: 'All Projects' },
          { href: '/projects?status=ACTIVE', label: 'Active' },
          { href: '/projects?status=DRAFT', label: 'Drafts' },
        ],
      },
      {
        href: '/sows',
        label: 'SOW',
        icon: <FileSignature aria-hidden />,
        permission: 'sows.read',
        children: [
          { href: '/sows', label: 'All' },
          { href: '/sows?status=ACTIVE', label: 'Active' },
          { href: '/sows?status=DRAFT', label: 'Draft' },
          { href: '/sows?status=PENDING_APPROVAL', label: 'Pending' },
          { href: '/sows?status=REJECTED', label: 'Rejected' },
          { href: '/sows?status=CLOSED', label: 'Closed' },
        ],
      },
      {
        href: '/contracts',
        label: 'Contracts',
        icon: <FileText aria-hidden />,
        permission: 'contracts.read',
        children: [
          { href: '/contracts', label: 'All Contracts' },
          { href: '/contracts?status=ACTIVE', label: 'Active' },
          {
            href: '/contracts?status=PENDING_ACCEPTANCE',
            label: 'Pending Acceptance',
          },
          { href: '/contracts?status=DRAFT', label: 'Drafts' },
          { href: '/contracts?expiring_within_days=30', label: 'Expiring' },
          { href: '/contracts?status=DECLINED', label: 'Rejected' },
          { href: '/contracts?status=TERMINATED', label: 'Terminated / Closed' },
        ],
      },
      {
        href: '/invoices',
        label: 'Invoices',
        icon: <Receipt aria-hidden />,
        permission: 'invoices.read',
        children: [
          { href: '/invoices', label: 'All' },
          { href: '/invoices?status=DRAFT', label: 'Draft' },
          { href: '/invoices?status=PENDING', label: 'Pending' },
          { href: '/invoices?status=PAID', label: 'Paid' },
          { href: '/invoices?status=REJECTED', label: 'Rejected' },
          { href: '/invoices?overdue_only=true', label: 'Overdue' },
        ],
      },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { href: '/time', label: 'Work', icon: <Briefcase aria-hidden /> },
      {
        href: '/companies',
        label: 'Business',
        icon: <Building2 aria-hidden />,
        permission: 'companies.read',
      },
      {
        href: '/payments',
        label: 'Payments',
        icon: <Wallet aria-hidden />,
        permission: 'payments.read',
      },
      { href: '/assistant', label: 'AI', icon: <Sparkles aria-hidden /> },
    ],
  },
]

function HeaderSearch() {
  const router = useRouter()
  const [term, setTerm] = React.useState('')

  return (
    <form
      role="search"
      aria-label="Global search"
      className="ml-2 hidden min-w-0 flex-1 max-w-md items-center md:flex"
      onSubmit={(event) => {
        event.preventDefault()
        const q = term.trim()
        if (q.length >= 2) router.push(`/search?q=${encodeURIComponent(q)}`)
      }}
    >
      <div className="relative w-full">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <input
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="Search"
          aria-label="Search"
          className="h-9 w-full rounded-md border border-input bg-background pl-8 pr-3 text-sm placeholder:text-muted-foreground/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
        />
      </div>
    </form>
  )
}

function HeaderLink({
  href,
  label,
  active,
  badge,
  children,
}: {
  href: string
  label: string
  active?: boolean
  badge?: number
  children: React.ReactNode
}) {
  return (
    <a
      href={href}
      aria-label={label}
      aria-current={active ? 'page' : undefined}
      title={label}
      className={cn(
        'relative flex h-9 items-center gap-2 rounded-md px-2 text-sm font-medium transition-colors',
        active ? 'text-primary-strong' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
      )}
    >
      <span aria-hidden className="[&_svg]:size-4">
        {children}
      </span>
      <span className="hidden xl:inline">{label}</span>
      {badge != null && badge > 0 ? (
        <span className="absolute right-0.5 top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-2xs font-semibold text-white">
          {badge > 99 ? '99+' : badge}
        </span>
      ) : null}
    </a>
  )
}

function CompanySwitcher() {
  const { companies, activeCompanyPublicId, switchCompany, switching } = useCompany()
  const [open, setOpen] = React.useState(false)

  if (companies.length === 0) {
    return (
      <div className="px-1 py-2 text-center">
        <p className="text-sm font-medium">No companies yet</p>
        <p className="mt-1 text-xs text-muted-foreground">Networking personally.</p>
        <Button className="mt-3 w-full" size="sm" asChild>
          <a href="/onboarding">Create company</a>
        </Button>
      </div>
    )
  }

  const active = companies.find((c) => c.public_id === activeCompanyPublicId)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex h-9 w-full items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-sm transition-colors hover:bg-muted"
      >
        <Building2 aria-hidden className="size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-left font-medium">
          {active?.display_name ?? 'Select company'}
        </span>
        <ChevronDown aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
      </button>

      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden />
          <div
            role="listbox"
            aria-label="Switch company"
            className="absolute left-0 right-0 top-11 z-20 rounded-lg border border-border bg-surface p-1 shadow-popover"
          >
            {companies.map((company) => {
              const isActive = company.public_id === activeCompanyPublicId
              return (
                <button
                  key={company.public_id}
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  disabled={switching}
                  onClick={() => {
                    if (!isActive) void switchCompany(company.public_id)
                    setOpen(false)
                  }}
                  className="flex w-full items-start gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-muted disabled:opacity-50"
                >
                  <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded bg-secondary text-2xs font-semibold text-secondary-foreground">
                    {initials(company.display_name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      {company.display_name}
                    </span>
                    <span className="font-mono text-2xs text-subtle-foreground">
                      {company.public_id}
                    </span>
                  </span>
                  {isActive ? <Check aria-hidden className="mt-1 size-4 text-primary" /> : null}
                </button>
              )
            })}
            <div className="my-1 h-px bg-border" />
            <MenuLink href="/onboarding" icon={<Building2 />} onNavigate={() => setOpen(false)}>
              New company
            </MenuLink>
          </div>
        </>
      ) : null}
    </div>
  )
}

function Avatar() {
  const { me } = useCompany()
  const name = me ? `${me.first_name} ${me.last_name}` : 'Account'

  if (me?.avatar_url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={me.avatar_url}
        alt=""
        className="h-7 w-7 rounded-full object-cover ring-2 ring-border"
      />
    )
  }

  return (
    <span
      aria-hidden
      className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-soft text-2xs font-semibold text-primary-strong ring-2 ring-border"
    >
      {initials(name)}
    </span>
  )
}

function MenuLink({
  href,
  icon,
  children,
  onNavigate,
}: {
  href: string
  icon: React.ReactNode
  children: React.ReactNode
  onNavigate?: () => void
}) {
  return (
    <a
      role="menuitem"
      href={href}
      onClick={onNavigate}
      className="flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors hover:bg-muted"
    >
      <span className="text-muted-foreground [&_svg]:size-4">{icon}</span>
      {children}
    </a>
  )
}

export { NAV_SECTIONS }
