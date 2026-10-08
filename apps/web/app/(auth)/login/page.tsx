import type { Metadata } from 'next'

import { AuthShell } from '@/components/auth-shell'
import { LoginForm } from './login-form'

export const metadata: Metadata = { title: 'Sign in' }

export default function LoginPage() {
  return (
    <AuthShell title="Sign in to MyTrakin" subtitle="Use your work email address to continue.">
      <LoginForm />
    </AuthShell>
  )
}