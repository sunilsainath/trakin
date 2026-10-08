import { cookies } from 'next/headers'
import { NextResponse } from 'next/server'
import { createServerClient } from '@supabase/ssr'

/**
 * PKCE callback for every Supabase email interaction: OAuth sign-in,
 * signup confirmation, magic links and password-recovery links.
 *
 * Exchanges the one-time `code` for a session and honours `next`, so the
 * login form's Google button (`redirectTo: origin/auth/callback`) and the
 * recovery flow (`next=/reset-password`) land in the right place.
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')
  const next = searchParams.get('next') ?? '/feed'

  if (code) {
    const cookieStore = await cookies()
    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL ?? '',
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? '',
      {
        cookies: {
          getAll() {
            return cookieStore.getAll()
          },
          setAll(
            pairs: { name: string; value: string; options?: Record<string, unknown> }[],
          ) {
            for (const { name, value, options } of pairs) {
              cookieStore.set(name, value, options as never)
            }
          },
        },
      },
    )
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`)
    }
  }

  return NextResponse.redirect(`${origin}/login?error=callback`)
}
