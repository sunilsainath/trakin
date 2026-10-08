'use client'

import * as React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'

import { ApiError } from '@/lib/api'

/**
 * Client providers.
 *
 * Query defaults are chosen for a platform where a stale permission decision is
 * a security concern, not just an annoyance: nothing is served from cache
 * without a refetch, and no retry repeats a mutation.
 */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
        retry: (failureCount, error) => {
          // Never retry an auth or permission failure; the answer will not change.
          if (error instanceof ApiError) {
            const terminal = [
              'AUTHENTICATION_FAILED',
              'INVALID_TOKEN',
              'PERMISSION_DENIED',
              'NOT_A_COMPANY_MEMBER',
              'VALIDATION_ERROR',
              'NOT_FOUND',
              'COMPANY_CONTEXT_REQUIRED',
            ]
            if (terminal.includes(error.code)) return false
          }
          return failureCount < 2
        },
      },
      mutations: {
        retry: false,
      },
    },
  })
}

let browserQueryClient: QueryClient | undefined

function getQueryClient(): QueryClient {
  if (typeof window === 'undefined') return makeQueryClient()
  browserQueryClient ??= makeQueryClient()
  return browserQueryClient
}

export function Providers({ children }: { children: React.ReactNode }) {
  const queryClient = getQueryClient()

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster
        position="bottom-right"
        closeButton
        richColors
        toastOptions={{
          // Errors are announced rather than only shown.
          classNames: {
            toast: 'text-sm',
            description: 'text-xs',
          },
        }}
      />
    </QueryClientProvider>
  )
}