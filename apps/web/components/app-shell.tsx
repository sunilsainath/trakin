'use client'

import * as React from 'react'
import { usePathname } from 'next/navigation'
import {
  Bell,
  Check,
  ChevronDown,
  LogOut,
  Menu,
  Moon,
  Search,
  Settings,
  Sun,
  Building2,
  User as UserIcon,
  X,
} from 'lucide-react'

import { cn, initials } from '@/lib/utils'
import { getSupabase } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useRealtimeInsert } from '@/hooks/use-realtime'
import { Badge, Button, ProgressBar } from '@/components/ui'
import {
  NAV_SECTIONS,
  NavLink,
  badgeCountFor,
  isNavItemActive,
  useVisibleNavigation,
} from '@/components/navigation'

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const { me, unread, refreshUnread } = useCompany()
  const sections = useVisibleNavigation()

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
  const [switcherOpen, setSwitcherOpen] = React.useState(false)
  const [menuOpen, setMenuOpen] = React.useState(false)
  const [theme, setTheme] = React.useState<'light' | 'dark'>('light')

  // Close the mobile drawer whenever the route changes.
  React.useEffect(() => {
    setMobileOpen(false)
  }, [pathname])

  // The theme must be resolved before first paint to avoid a flash.
  React.useEffect(() => {
    const stored = window.localStorage.getItem('mytrakin.theme')
    const preferred =
      stored === 'dark' || stored === 'light'
        ? stored
        : window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark'
          : 'light'
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
    <div className="flex min-h-dvh bg-background">
      {/* Skip link target */}
      <div id="main" className="flex-1">
        {/* ------------------------------------------------------------ */}
        {/* Left rail                                                      */}
        {/* ------------------------------------------------------------ */}
        <aside
          className={cn(
            'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-border bg-surface',
            'transition-transform duration-200 lg:static lg:translate-x-0',
            mobileOpen ? 'translate-x-0 shadow-popover' : '-translate-x-full',
          )}
        >
          <div className="flex h-14 shrink-0 items-center justify-between border-b border-border px-4">
            <a href="/dashboard" className="flex items-center gap-2">
              <span
                aria-hidden
                className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-2xs font-bold text-primary-foreground"
              >
                MT
              </span>
              <span className="text-sm font-semibold tracking-tight">MyTrakin</span>
            </a>
            <Button
              variant="ghost"
              size="icon-sm"
              className="lg:hidden"
              aria-label="Close navigation"
              onClick={() => setMobileOpen(false)}
            >
              <X />
            </Button>
          </div>

          <nav aria-label="Main" className="flex-1 space-y-5 overflow-y-auto p-3 scrollbar-thin">
            {sections.map((section) => (
              <div key={section.label}>
                <p className="px-3 pb-1.5 text-2xs font-semibold uppercase tracking-wider text-subtle-foreground">
                  {section.label}
                </p>
                <div className="space-y-0.5">
                  {section.items.map((item) => (
                    <NavLink
                      key={item.href}
                      item={item}
                      active={isNavItemActive(item, pathname)}
                      badge={item.badge ? badgeCountFor(item, unread) : undefined}
                    />
                  ))}
                </div>
              </div>
            ))}
          </nav>

          <div className="shrink-0 border-t border-border p-3">
            <p className="px-3 pb-2 text-2xs text-subtle-foreground">
              {me?.onboarding_completed ? 'Workspace ready' : 'Finish onboarding'}
            </p>
            <ProgressBar
              value={me?.onboarding_completed ? 100 : 40}
              tone={me?.onboarding_completed ? 'success' : 'primary'}
              label="Onboarding progress"
            />
          </div>
        </aside>

        {mobileOpen ? (
          <div
            className="fixed inset-0 z-30 bg-foreground/40 lg:hidden"
            onClick={() => setMobileOpen(false)}
            aria-hidden
          />
        ) : null}

        {/* ------------------------------------------------------------ */}
        {/* Main column                                                    */}
        {/* ------------------------------------------------------------ */}
        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-2 border-b border-border bg-surface/90 px-3 backdrop-blur sm:px-5">
            <Button
              variant="ghost"
              size="icon-sm"
              className="lg:hidden"
              aria-label="Open navigation"
              onClick={() => setMobileOpen(true)}
            >
              <Menu />
            </Button>

            {/* Company switcher */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setSwitcherOpen((v) => !v)}
                aria-expanded={switcherOpen}
                aria-haspopup="listbox"
                className="flex h-9 items-center gap-2 rounded-md border border-border bg-surface px-2.5 text-sm transition-colors hover:bg-muted"
              >
                <Building2 aria-hidden className="size-4 text-muted-foreground" />
                <CompanyName />
                <ChevronDown aria-hidden className="size-3.5 text-muted-foreground" />
              </button>

              {switcherOpen ? (
                <>
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setSwitcherOpen(false)}
                    aria-hidden
                  />
                  <div
                    role="listbox"
                    aria-label="Switch company"
                    className="absolute left-0 top-11 z-20 w-72 rounded-lg border border-border bg-surface p-1 shadow-popover"
                  >
                    <CompanySwitcher
                      onSelect={() => setSwitcherOpen(false)}
                    />
                  </div>
                </>
              ) : null}
            </div>

            <div className="flex-1" />

            <Button variant="ghost" size="icon-sm" asChild>
              <a href="/search" aria-label="Global search">
                <Search />
              </a>
            </Button>

            <Button variant="ghost" size="icon-sm" asChild>
              <a href="/notifications" aria-label="Notifications" className="relative">
                <Bell />
                {unread.notifications > 0 ? (
                  <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-danger ring-2 ring-surface" />
                ) : null}
              </a>
            </Button>

            <Button
              variant="ghost"
              size="icon-sm"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? 'Use light theme' : 'Use dark theme'}
            >
              {theme === 'dark' ? <Sun /> : <Moon />}
            </Button>

            {/* User menu */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setMenuOpen((v) => !v)}
                aria-expanded={menuOpen}
                aria-haspopup="menu"
                className="flex h-9 items-center gap-2 rounded-md px-1.5 transition-colors hover:bg-muted"
              >
                <Avatar />
              </button>

              {menuOpen ? (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} aria-hidden />
                  <div
                    role="menu"
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

          <main className="min-w-0 flex-1">{children}</main>
        </div>
      </div>
    </div>
  )
}

function CompanyName() {
  const { activeCompany, loading } = useCompany()
  if (loading) return <span className="text-muted-foreground">Loading…</span>
  if (!activeCompany) return <span className="text-muted-foreground">No company</span>
  return <span className="max-w-40 truncate font-medium">{activeCompany.display_name}</span>
}

function CompanySwitcher({ onSelect }: { onSelect: () => void }) {
  const { companies, activeCompanyPublicId, switchCompany, switching } = useCompany()

  if (companies.length === 0) {
    return (
      <div className="px-3 py-6 text-center">
        <p className="text-sm font-medium">No companies yet</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Create a company to start collaborating with your team.
        </p>
        <Button className="mt-3 w-full" size="sm" asChild>
          <a href="/companies/new" onClick={onSelect}>
            Create company
          </a>
        </Button>
      </div>
    )
  }

  return (
    <>
      {companies.map((company) => {
        const active = company.public_id === activeCompanyPublicId
        return (
          <button
            key={company.public_id}
            type="button"
            role="option"
            aria-selected={active}
            disabled={switching}
            onClick={() => {
              if (!active) void switchCompany(company.public_id)
              onSelect()
            }}
            className="flex w-full items-start gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-muted disabled:opacity-50"
          >
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded bg-secondary text-2xs font-semibold text-secondary-foreground">
              {initials(company.display_name)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{company.display_name}</span>
              <span className="flex items-center gap-1.5">
                <span className="font-mono text-2xs text-subtle-foreground">
                  {company.public_id}
                </span>
                {company.verification_state !== 'VERIFIED' ? (
                  <Badge tone="warning" className="px-1.5 py-0 text-2xs">
                    {company.verification_state === 'UNVERIFIED' ? 'Unverified' : 'Pending'}
                  </Badge>
                ) : null}
              </span>
            </span>
            {active ? <Check aria-hidden className="mt-1 size-4 text-primary" /> : null}
          </button>
        )
      })}
      <div className="my-1 h-px bg-border" />
      <MenuLink href="/companies/new" icon={<Building2 />} onNavigate={onSelect}>
        New company
      </MenuLink>
    </>
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