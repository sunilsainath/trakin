'use client'

import * as React from 'react'
import { ShieldCheck } from 'lucide-react'

import { useCompany } from '@/hooks/use-company'
import type { Permission, Role } from '@/lib/types'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { DataTable } from '@/components/data-table'
import { ErrorState, LoadingTable, useCompanyQuery } from '@/components/query'

/* -------------------------------------------------------------------------- */
/* Roles and permissions (also used by /settings/permissions)                  */
/* -------------------------------------------------------------------------- */

export function RolesAndPermissions() {
  const { activeCompanyPublicId, can, permissions } = useCompany()

  const roles = useCompanyQuery<Role[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['company', 'roles'],
    path: '/companies/current/roles',
    enabled: can('roles.read'),
  })

  const catalogue = useCompanyQuery<Permission[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['permissions'],
    path: '/permissions',
    enabled: can('roles.read'),
  })

  const held = new Set(permissions)

  if (roles.isPending || catalogue.isPending) return <LoadingTable rows={6} columns={3} />
  if (roles.isError) return <ErrorState error={roles.error} onRetry={() => void roles.refetch()} />
  if (catalogue.isError) {
    return <ErrorState error={catalogue.error} onRetry={() => void catalogue.refetch()} />
  }

  const byModule = new Map<string, Permission[]>()
  for (const permission of catalogue.data ?? []) {
    const list = byModule.get(permission.module) ?? []
    list.push(permission)
    byModule.set(permission.module, list)
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck aria-hidden className="size-4 text-primary" />
            Company roles
          </CardTitle>
          <CardDescription>
            The roles available in this company and how many people hold each one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <DataTable
            columns={[
              {
                key: 'name',
                header: 'Role',
                cell: (row: Role) => (
                  <div className="min-w-0">
                    <p className="truncate font-medium">{row.name}</p>
                    <p className="font-mono text-2xs text-subtle-foreground">{row.key}</p>
                  </div>
                ),
              },
              {
                key: 'description',
                header: 'Description',
                hideBelow: 'lg',
                cell: (row: Role) => (
                  <span className="text-muted-foreground">{row.description || '—'}</span>
                ),
              },
              {
                key: 'members',
                header: 'Members',
                numeric: true,
                cell: (row: Role) => row.member_count,
              },
              {
                key: 'permissions',
                header: 'Permissions',
                numeric: true,
                hideBelow: 'sm',
                cell: (row: Role) => row.permissions.length,
              },
              {
                key: 'assignable',
                header: 'Assignable',
                hideBelow: 'md',
                cell: (row: Role) => (
                  <Badge tone={row.is_assignable ? 'success' : 'neutral'}>
                    {row.is_assignable ? 'Yes' : 'System'}
                  </Badge>
                ),
              },
              {
                key: 'yours',
                header: 'Yours',
                hideBelow: 'md',
                cell: (row: Role) => (
                  <span className="text-xs text-muted-foreground">
                    {row.permissions.filter((key) => held.has(key)).length} of{' '}
                    {row.permissions.length} effective
                  </span>
                ),
              },
            ]}
            rows={roles.data ?? []}
            rowKey={(row) => row.public_id}
            caption="Roles available in this company"
            emptyState={
              <EmptyState
                icon={<ShieldCheck aria-hidden />}
                title="No roles are visible to you"
                description="Roles are company-scoped. Ask an administrator to grant you roles.read."
              />
            }
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Permission catalogue</CardTitle>
          <CardDescription>
            Everything the platform defines. A permission marked with a check is one
            that applies to you in this company right now.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {[...byModule.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([module, modulePermissions]) => (
              <div key={module}>
                <h3 className="text-sm font-semibold">{module}</h3>
                <ul className="mt-2 grid gap-1.5 sm:grid-cols-2">
                  {modulePermissions
                    .slice()
                    .sort((a, b) => a.key.localeCompare(b.key))
                    .map((permission) => {
                      const granted = held.has(permission.key)
                      return (
                        <li
                          key={permission.key}
                          className="flex items-start gap-2 rounded-md border border-border px-2.5 py-1.5"
                        >
                          <span
                            aria-hidden
                            className={`mt-1.5 size-2 shrink-0 rounded-full ${
                              granted ? 'bg-success' : 'bg-border'
                            }`}
                          />
                          <div className="min-w-0">
                            <p className="truncate font-mono text-2xs">{permission.key}</p>
                            <p className="text-xs text-muted-foreground">
                              {permission.description}
                            </p>
                          </div>
                        </li>
                      )
                    })}
                </ul>
              </div>
            ))}
        </CardContent>
      </Card>
    </div>
  )
}
