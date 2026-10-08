import type { Metadata } from 'next'

import { AuthShell } from '@/components/auth-shell'
import { SignupForm } from './signup-form'

export const metadata: Metadata = { title: 'Sign up' }

export default function SignupPage() {
  return (
    <AuthShell title="Create your account" subtitle="Join your organisation on MyTrakin.">
      <SignupForm />
    </AuthShell>
  )
}
