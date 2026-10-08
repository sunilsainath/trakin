/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,          // do not advertise the framework
  compress: true,
  eslint: { dirs: ['app', 'components', 'lib', 'hooks'] },

  // Security headers. A strict CSP is the single highest-value control here:
  // it is what stops an XSS payload from reaching the session cookie.
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
          { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
          { key: 'X-DNS-Prefetch-Control', value: 'off' },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=63072000; includeSubDomains; preload',
          },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              // NOTE: a previous revision shipped
              //   script-src 'self' 'nonce-{{nonce}}' 'strict-dynamic'
              // but no middleware ever substituted {{nonce}}, so browsers saw a
              // literal invalid nonce and blocked every Next.js inline bootstrap
              // script (React never hydrated; login/dashboard stayed on their
              // loading fallbacks). Framework-injected scripts cannot carry a
              // per-request nonce without middleware + layout wiring, so until
              // that hardening lands (TODO: real nonce middleware with
              // 'strict-dynamic' for production), scripts allow 'unsafe-inline'.
              // 'unsafe-eval' is dev-only: webpack HMR needs it, production
              // does not.
              "script-src 'self' 'unsafe-inline'" +
                (process.env.NODE_ENV === 'production' ? '' : " 'unsafe-eval'"),
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob: https:",
              "font-src 'self' data:",
              // The browser calls Supabase Auth directly (sign-in, session
              // refresh), so its host must be an allowed connection target in
              // addition to the API.
              "connect-src 'self' " +
                (process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000') +
                ' ' +
                (process.env.NEXT_PUBLIC_SUPABASE_URL ?? ''),
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "form-action 'self'",
              "object-src 'none'",
              // Local dev is plain http; forcing https upgrades breaks it.
              ...(process.env.NODE_ENV === 'production' ? ['upgrade-insecure-requests'] : []),
            ].join('; '),
          },
        ],
      },
    ]
  },

  // The service-role key must never reach the browser bundle. If a module that
  // imports it is pulled into a client component, the build fails here rather
  // than shipping a credential.
  serverExternalPackages: ['@supabase/supabase-js'],

  async redirects() {
    return [
      { source: '/home', destination: '/dashboard', permanent: false },
    ]
  },
}

export default nextConfig
