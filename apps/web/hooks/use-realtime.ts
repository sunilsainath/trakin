'use client'

import * as React from 'react'

import { getSupabase } from '@/lib/api'

interface RealtimeInsertOptions {
  schema: string
  table: string
  /** PostgREST filter, e.g. `user_id=eq.<uuid>`. Omit for no server filter. */
  filter?: string
  onInsert: () => void
  enabled?: boolean
}

/**
 * Subscribe to INSERTs on one table for as long as the component lives.
 *
 * Supabase enforces the table's RLS policies per subscriber, so this can only
 * ever deliver rows the signed-in user may already read through the API. A
 * failed subscription is silent: polling and refetch-on-focus remain the
 * source of truth, realtime is only the nudge.
 */
export function useRealtimeInsert({
  schema,
  table,
  filter,
  onInsert,
  enabled = true,
}: RealtimeInsertOptions) {
  const handler = React.useRef(onInsert)
  handler.current = onInsert

  React.useEffect(() => {
    if (!enabled) return
    let channel: { unsubscribe: () => void } | null = null
    let cancelled = false
    try {
      const supabase = getSupabase()
      const builder = supabase
        .channel(`rt:${schema}:${table}:${filter ?? 'all'}`)
        .on(
          'postgres_changes',
          { event: 'INSERT', schema, table, ...(filter ? { filter } : {}) },
          () => handler.current(),
        )
      const subscription = builder.subscribe()
      if (!cancelled) {
        channel = { unsubscribe: () => void subscription.unsubscribe() }
      } else {
        void subscription.unsubscribe()
      }
    } catch {
      // Realtime unavailable (offline, misconfigured): polling covers it.
    }
    return () => {
      cancelled = true
      channel?.unsubscribe()
    }
  }, [schema, table, filter, enabled])
}
