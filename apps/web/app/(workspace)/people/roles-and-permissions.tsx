'use client'

import * as React from 'react'
import { Pencil, ShieldCheck } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import type { Permission, Role } from '@/lib/types'
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Dialog,
  EmptyState,
  Input,
} from '@/components/ui'
import { Field } from '@/components/forms'
import { DataTable } from '@/components/data-table'
import { ErrorState, LoadingTable, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

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
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck aria-hidden className="size-4 text-primary" />
                Company roles
              </CardTitle>
              <CardDescription>
                The roles available in this company and how many people hold each one.
              </CardDescription>
            </div>
            {can('roles.manage') ? <CreateRoleDialog catalogue={catalogue.data ?? []} /> : null}
          </div>
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
              ...(can('roles.manage')
                ? [
                    {
                      key: 'actions',
                      header: 'Actions',
                      cell: (row: Role) => (
                        <RoleActions role={row} catalogue={catalogue.data ?? []} />
                      ),
                    },
                  ]
                : []),
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

/* -------------------------------------------------------------------------- */
/* Role management                                                            */
/* -------------------------------------------------------------------------- */

/**
 * Per-row actions for administrators. There is deliberately no delete: a role
 * still referenced by memberships cannot be removed, and the API exposes no
 * delete endpoint, so the UI does not offer one it cannot honour.
 */
function RoleActions({ role, catalogue }: { role: Role; catalogue: Permission[] }) {
  const [editing, setEditing] = React.useState(false)
  const [duplicating, setDuplicating] = React.useState(false)

  return (
    <div className="flex items-center justify-end gap-1">
      <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
        <Pencil aria-hidden />
        Edit
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setDuplicating(true)}>
        Duplicate
      </Button>
      {editing ? (
        <EditRoleDialog role={role} catalogue={catalogue} onClose={() => setEditing(false)} />
      ) : null}
      {duplicating ? (
        <CreateRoleDialog
          catalogue={catalogue}
          initial={{ name: `${role.name} copy`, permissions: role.permissions }}
          open
          onClose={() => setDuplicating(false)}
        />
      ) : null}
    </div>
  )
}

function groupByModule(catalogue: Permission[]): [string, Permission[]][] {
  const byModule = new Map<string, Permission[]>()
  for (const permission of catalogue) {
    const list = byModule.get(permission.module) ?? []
    list.push(permission)
    byModule.set(permission.module, list)
  }
  return [...byModule.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([module, permissions]) => [
      module,
      permissions.slice().sort((a, b) => a.key.localeCompare(b.key)),
    ])
}

function PermissionChecklist({
  catalogue,
  selected,
  onToggle,
}: {
  catalogue: Permission[]
  selected: Set<string>
  onToggle: (key: string) => void
}) {
  return (
    <div className="max-h-72 space-y-4 overflow-y-auto rounded-md border border-border p-3">
      {groupByModule(catalogue).map(([module, permissions]) => (
        <fieldset key={module}>
          <legend className="px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {module}
          </legend>
          <ul className="mt-1.5 space-y-1">
            {permissions.map((permission) => (
              <li key={permission.key}>
                <label className="flex cursor-pointer items-start gap-2 rounded px-1.5 py-1 text-sm hover:bg-primary-soft/40">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={selected.has(permission.key)}
                    onChange={() => onToggle(permission.key)}
                  />
                  <span className="min-w-0">
                    <span className="block font-mono text-2xs">{permission.key}</span>
                    <span className="block text-xs text-muted-foreground">
                      {permission.description}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      ))}
    </div>
  )
}

function CreateRoleDialog({
  catalogue,
  initial,
  triggerLabel,
  open,
  onClose,
}: {
  catalogue: Permission[]
  initial?: { name: string; permissions: string[] }
  triggerLabel?: string
  open?: boolean
  onClose?: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [internalOpen, setInternalOpen] = React.useState(false)
  const shown = open ?? internalOpen
  const close = onClose ?? (() => setInternalOpen(false))
  const [name, setName] = React.useState(initial?.name ?? '')
  const [description, setDescription] = React.useState('')
  const [selected, setSelected] = React.useState<Set<string>>(
    () => new Set(initial?.permissions ?? []),
  )
  const [error, setError] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (shown) {
      setName(initial?.name ?? '')
      setDescription('')
      setSelected(new Set(initial?.permissions ?? []))
      setError(null)
    }
    // Initial values seed each opening; edits after that belong to the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown])

  const toggle = (key: string) =>
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const create = useCompanyMutation<unknown, { name: string; permissions: string[] }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.post('/companies/current/roles', body, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [['company', 'roles']],
    onSuccess: () => {
      notifySuccess('Role created.', `${name.trim()} can now be assigned.`)
      close()
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (name.trim().length < 2) {
      setError('Give the role a name of at least two characters.')
      return
    }
    setError(null)
    try {
      await create.mutateAsync({
        name: name.trim(),
        permissions: [...selected].sort(),
      })
    } catch (cause) {
      notifyError(cause, 'The role could not be created.')
    }
  }

  return (
    <>
      {open === undefined ? (
        <Button onClick={() => setInternalOpen(true)}>{triggerLabel ?? 'New role'}</Button>
      ) : null}
      <Dialog
        open={shown}
        onOpenChange={(next) => {
          if (!next) close()
        }}
        title={initial ? `Duplicate ${initial.name}` : 'Create a role'}
        description="Compose a company-specific role from the permission catalogue. The server rejects unknown keys and duplicate names."
        footer={
          <>
            <Button variant="ghost" onClick={close} disabled={create.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="create-role" loading={create.isPending}>
              {initial ? 'Duplicate role' : 'Create role'}
            </Button>
          </>
        }
      >
        <form id="create-role" onSubmit={submit} className="space-y-4">
          <Field label="Role name" error={error ?? undefined} required>
            <Input
              id="role-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              aria-invalid={Boolean(error)}
              placeholder="Support Lead"
            />
          </Field>
          <Field label="Description" hint="Optional">
            <Input
              id="role-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this role is for"
            />
          </Field>
          <Field label={`Permissions (${selected.size} selected)`} required>
            <PermissionChecklist catalogue={catalogue} selected={selected} onToggle={toggle} />
          </Field>
        </form>
      </Dialog>
    </>
  )
}

function EditRoleDialog({
  role,
  catalogue,
  onClose,
}: {
  role: Role
  catalogue: Permission[]
  onClose: () => void
}) {
  const { activeCompanyPublicId } = useCompany()
  const [selected, setSelected] = React.useState<Set<string>>(() => new Set(role.permissions))

  const toggle = (key: string) =>
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const save = useCompanyMutation<unknown, { permissions: string[] }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.put(`/companies/current/roles/${role.public_id}/permissions`, body, {
        companyPublicId: activeCompanyPublicId,
      }),
    invalidate: [['company', 'roles']],
    onSuccess: () => {
      notifySuccess('Permissions updated.', `${role.name} now grants ${selected.size} permissions.`)
      onClose()
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await save.mutateAsync({ permissions: [...selected].sort() })
    } catch (cause) {
      notifyError(cause, 'The permissions could not be updated.')
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={`Edit ${role.name}`}
      description="Replace the permission set. The change is audited with the previous set attached."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button type="submit" form="edit-role" loading={save.isPending}>
            Save permissions
          </Button>
        </>
      }
    >
      <form id="edit-role" onSubmit={submit} className="space-y-4">
        <Field label={`Permissions (${selected.size} selected)`} required>
          <PermissionChecklist catalogue={catalogue} selected={selected} onToggle={toggle} />
        </Field>
      </form>
    </Dialog>
  )
}
