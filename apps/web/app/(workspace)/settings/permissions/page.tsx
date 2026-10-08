'use client'

import * as React from 'react'
import { ShieldCheck } from 'lucide-react'

import { PageHeader, PageShell } from '@/components/page'
import { useCompany } from '@/hooks/use-company'
import { EmptyState, Card } from '@/components/ui'
import { RolesAndPermissions } from '../../people/roles-and-permissions'

/**
 * Roles and permissions.
 *
 * The catalogue is the same component the people screen renders, because the
 * question it answers — "what can this role do, and what can I do?" — is asked
 * from both places.
 */
export default function SettingsPermissionsPage() {
  const { can } = useCompany()

  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'Settings', href: '/settings' }, { label: 'Roles & Permissions' }]}
          title="Roles and permissions"
          description="What each role in this company can do, and which of those permissions apply to you right now."
        />

        {can('roles.read') ? (
          <RolesAndPermissions />
        ) : (
          <Card>
            <EmptyState
              icon={<ShieldCheck aria-hidden />}
              title="You cannot read the role catalogue"
              description="Reading roles and permissions requires the roles.read permission in this company. Ask an administrator to grant it."
            />
          </Card>
        )}
      </div>
    </PageShell>
  )
}