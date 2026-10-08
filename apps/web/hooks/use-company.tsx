'use client'

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { usePathname, useRouter } from 'next/navigation'

import { api, ApiError, getSupabase } from '@/lib/api'
import type { Company, Me, UnreadCounts } from '@/lib/types'
import type { UnreadCountResponse as UnreadCountsResponse } from '@/lib/domain-types'

const COMPANY_STORAGE_KEY = 'mytrakin.activeCompanyPublicId'

interface CompanyContextValue {
  me: Me | null
  companies: Company[]
  activeCompany: Company | null
  activeCompanyPublicId: string | null
  switching: boolean
  unread: UnreadCounts
  loading: boolean
  error: ApiError | null
  /** The role keys the signed-in user holds in the active company. */
  roleKeys: string[]
  /** Every permission the caller resolves to in the active company. */
  permissions: string[]
  /**
   * RBAC check for hiding a control.
   *
   * The API re-checks the same permission on every request, so a `false` here is
   * final and a `true` may still be refused if the list is stale.
   */
  can: (permission: string) => boolean
  switchCompany: (publicId: string) => Promise<void>
  refresh: () => Promise<void>
  setUnread: (next: UnreadCounts) => void
}

const CompanyContext = createContext<CompanyContextValue | null>(null)

export function useCompany(): CompanyContextValue {
  const context = useContext(CompanyContext)
  if (!context) {
    throw new Error('useCompany must be used inside <CompanyProvider>')
  }
  return context
}

/** Permission check convenience wrapper. */
export function useCan(): (permission: string) => boolean {
  return useCompany().can
}

export function CompanyProvider({ children }: { children: ReactNode }) {
  const router = useRouter()
  const pathname = usePathname()

  const [me, setMe] = useState<Me | null>(null)
  const [companies, setCompanies] = useState<Company[]>([])
  const [activeCompanyPublicId, setActiveCompanyPublicId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [switching, setSwitching] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      // Syncs email verification from Supabase on every session start; throws
      // EMAIL_NOT_VERIFIED when the platform row is still unconfirmed.
      await api.post('/auth/bootstrap')
      // The profile lives under the identity router (GET /api/v1/users/me).
      const profile = await api.get<Me>('/users/me')
      setMe(profile)

      // GET /companies returns a bare JSON array, not a page envelope.
      const list = await api.get<Company[]>('/companies')
      setCompanies(list ?? [])

      const stored =
        typeof window !== 'undefined' ? window.localStorage.getItem(COMPANY_STORAGE_KEY) : null
      const stillValid = (list ?? []).some((c) => c.public_id === stored)
      const next = stillValid ? stored : (list?.[0]?.public_id ?? null)

      setActiveCompanyPublicId(next)
      if (next) window.localStorage.setItem(COMPANY_STORAGE_KEY, next)
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'AUTHENTICATION_FAILED') {
        router.replace(`/login?next=${encodeURIComponent(pathname)}`)
      } else if (cause instanceof ApiError && cause.code === 'EMAIL_NOT_VERIFIED') {
        router.replace('/login?error=unverified')
      } else if (cause instanceof ApiError) {
        setError(cause)
      } else {
        setError(new ApiError('INTERNAL_ERROR', 'Could not load your workspace.', 0))
      }
    } finally {
      setLoading(false)
    }
  }, [pathname, router])

  useEffect(() => {
    void load()
  }, [load])

  // Sign-out anywhere in the tree must clear the cached workspace context too.
  useEffect(() => {
    const { data } = getSupabase().auth.onAuthStateChange((event) => {
      if (event === 'SIGNED_OUT') {
        window.localStorage.removeItem(COMPANY_STORAGE_KEY)
        setMe(null)
        setCompanies([])
        setActiveCompanyPublicId(null)
      }
    })
    return () => data.subscription.unsubscribe()
  }, [])

  // The navigation badges come from the server rather than being invented
  // locally. Polled on an interval because a badge that only updates on
  // navigation is indistinguishable from a badge that is stuck.
  const [unreadCounts, setUnreadCounts] = useState<UnreadCountsResponse | null>(null)

  const loadUnread = useCallback(async () => {
    if (!activeCompanyPublicId) {
      setUnreadCounts(null)
      return
    }
    try {
      const counts = await api.get<UnreadCountsResponse>('/notifications/unread-count', {
        companyPublicId: activeCompanyPublicId,
      })
      setUnreadCounts(counts)
    } catch {
      // A missing badge must never break the shell. The API refuses this call
      // without `notifications.read`, which is a normal state for a role that
      // has nothing to be notified about, so the counts stay as they were.
    }
  }, [activeCompanyPublicId])

  /** `setUnread` is kept for callers that want to clear a badge optimistically. */
  const setUnread = useCallback((next: UnreadCounts) => {
    setUnreadCounts((current) => ({
      notifications: next.notifications,
      approvals: next.approvals,
      timesheets: 0,
      invoices: 0,
      contracts: 0,
      leave: 0,
      ...(current ?? {}),
      messages: next.messages,
    }))
  }, [])

  // The badge counts the shell renders. Kept separate from the server shape so
  // the rest of the app depends on the three numbers it actually uses.
  const unread = useMemo<UnreadCounts>(
    () => ({
      notifications: unreadCounts?.notifications ?? 0,
      // The platform has no separate message inbox; the counter the API does
      // return is broken out into approvals so the two badges are accurate.
      messages: 0,
      approvals: unreadCounts?.approvals ?? 0,
    }),
    [unreadCounts],
  )

  useEffect(() => {
    void loadUnread()
    const timer = window.setInterval(() => void loadUnread(), 60_000)
    return () => window.clearInterval(timer)
  }, [loadUnread])

  const switchCompany = useCallback(
    async (publicId: string) => {
      setSwitching(true)
      window.localStorage.setItem(COMPANY_STORAGE_KEY, publicId)
      setActiveCompanyPublicId(publicId)
      // Every screen is company-scoped, so a change invalidates the route tree
      // and every cached query keyed on the old company.
      router.refresh()
      setSwitching(false)
    },
    [router],
  )

  const activeCompany = useMemo(
    () => companies.find((c) => c.public_id === activeCompanyPublicId) ?? null,
    [companies, activeCompanyPublicId],
  )

  const roleKeys = useMemo(() => activeCompany?.my_role_keys ?? [], [activeCompany])
  const permissions = useMemo(() => activeCompany?.my_permissions ?? [], [activeCompany])

  const can = useCallback(
    (permission: string) => permissions.includes(permission),
    [permissions],
  )

  const value = useMemo<CompanyContextValue>(
    () => ({
      me,
      companies,
      activeCompany,
      activeCompanyPublicId,
      switching,
      unread,
      loading,
      error,
      roleKeys,
      permissions,
      can,
      switchCompany,
      refresh: load,
      setUnread,
    }),
    [
      me,
      companies,
      activeCompany,
      activeCompanyPublicId,
      switching,
      unread,
      loading,
      error,
      roleKeys,
      permissions,
      can,
      switchCompany,
      setUnread,
      load,
    ],
  )

  return <CompanyContext.Provider value={value}>{children}</CompanyContext.Provider>
}