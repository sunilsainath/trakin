import type { Metadata } from 'next'

import { CompaniesScreen } from './companies'

export const metadata: Metadata = { title: 'Companies' }

export default function CompaniesPage() {
  return <CompaniesScreen />
}