'use client'

import * as React from 'react'
import { UserPlus, Users } from 'lucide-react'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import { useCompanyMutation } from '@/hooks/use-mutations'
import { formatCurrency, formatDate } from '@/lib/utils'
import type { Membership, Role } from '@/lib/types'
import {
  Avatar,
  Badge,
  Button,
  Dialog,
  EmptyState,
  Input,
  Select,
  Skeleton,
} from '@/components/ui'
import { Field } from '@/components/forms'
import { DataTable, type Column } from '@/components/data-table'
import { PublicId } from '@/components/public-id'
import { FilterBar, FilterInput, useDebouncedValue } from '@/components/filters'
import { PageHeader, PageShell } from '@/components/page'
import { ErrorState, LoadingTable, useCompanyQuery } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'
import { ReasonDialog } from '@/components/destructive'

/**
 * The people directory for the active company.
 *
 * The member list is a bare JSON array rather than a page envelope, so the whole
 * directory is available at once and the filter runs client-side. That is the
 * API's shape, not a choice: there is no server-side search on this endpoint.
 */
export default function PeoplePage() {
  return (
    <PageShell>
      <div className="space-y-6">
        <PageHeader
          crumbs={[{ label: 'People' }]}
          title="People"
          description="Everyone in this company, their role and what they can do. The hourly rate is only returned to you if you hold timesheets.read_rate."
          actions={<InviteDialog />}
        />
        <PeopleList />
      </div>
    </PageShell>
  )
}

function PeopleList() {
  const { activeCompanyPublicId, can, activeCompany } = useCompany()
  const [search, setSearch] = React.useState('')
  const debounced = useDebouncedValue(search.trim())
  const [roleFilter, setRoleFilter] = React.useState('')

  const members = useCompanyQuery<Membership[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['company', 'members'],
    path: '/companies/current/members',
  })

  const roles = useCompanyQuery<Role[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['company', 'roles'],
    path: '/companies/current/roles',
    enabled: can('roles.read'),
  })

  const rows = React.useMemo(() => {
    const all = members.data ?? []
    const needle = debounced.toLowerCase()
    return all.filter((member) => {
      if (roleFilter && member.role_key !== roleFilter) return false
      if (!needle) return true
      return (
        member.user.display_name.toLowerCase().includes(needle) ||
        member.user.public_id.toLowerCase().includes(needle) ||
        (member.job_title ?? '').toLowerCase().includes(needle) ||
        (member.department ?? '').toLowerCase().includes(needle)
      )
    })
  }, [members.data, debounced, roleFilter])

  const columns: Column<Membership>[] = [
    {
      key: 'person',
      header: 'Person',
      cell: (row) => (
        <div className="flex items-center gap-2.5">
          <Avatar name={row.user.display_name} src={row.user.avatar_url} size="sm" />
          <div className="min-w-0">
            <p className="truncate font-medium">{row.user.display_name}</p>
            <p className="truncate text-xs text-muted-foreground">
              {row.user.headline ?? row.job_title ?? '—'}
            </p>
          </div>
        </div>
      ),
    },
    {
      key: 'user_id',
      header: 'User ID',
      hideBelow: 'lg',
      cell: (row) => <PublicId value={row.user.public_id} kind="user" />,
    },
    {
      key: 'role',
      header: 'Role',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate">{row.role_name}</p>
          <p className="truncate font-mono text-2xs text-subtle-foreground">{row.role_key}</p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      hideBelow: 'sm',
      cell: (row) => (
        <Badge
          tone={
            row.status === 'ACTIVE'
              ? 'success'
              : row.status === 'PENDING'
                ? 'warning'
                : 'neutral'
          }
        >
          {row.status.toLowerCase()}
        </Badge>
      ),
    },
    {
      key: 'department',
      header: 'Department',
      hideBelow: 'lg',
      cell: (row) => <span className="text-muted-foreground">{row.department ?? '—'}</span>,
    },
    {
      key: 'rate',
      header: 'Hourly rate',
      numeric: true,
      hideBelow: 'md',
      // Null rather than hidden when the caller cannot read rates: the API
      // decides this, and the UI does not fake a value it was not given.
      cell: (row) =>
        row.hourly_rate
          ? `${formatCurrency(row.hourly_rate, row.currency ?? activeCompany?.default_currency ?? 'USD')}`
          : '—',
    },
    {
      key: 'joined',
      header: 'Joined',
      hideBelow: 'lg',
      cell: (row) => (row.joined_at ? formatDate(row.joined_at) : '—'),
    },
    ...(can('members.manage')
      ? [
          {
            key: 'actions',
            header: 'Actions',
            cell: (row: Membership) => (
              <MemberActions
                member={row}
                roles={roles.data ?? []}
                companyPublicId={activeCompanyPublicId}
              />
            ),
          },
        ]
      : []),
  ]

  if (members.isPending) return <LoadingTable rows={8} columns={5} />

  if (members.isError) {
    return <ErrorState error={members.error} onRetry={() => void members.refetch()} />
  }

  const all = members.data ?? []
  const activeCount = all.filter((member) => member.status === 'ACTIVE').length
  const activeFilters = (debounced ? 1 : 0) + (roleFilter ? 1 : 0)

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Members</p>
          <p className="mt-1.5 text-2xl font-semibold tabular">{all.length}</p>
        </div>
        <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Active</p>
          <p className="mt-1.5 text-2xl font-semibold tabular text-success">{activeCount}</p>
        </div>
        <div className="rounded-lg border border-border bg-surface p-4 shadow-card">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Awaiting acceptance
          </p>
          <p className="mt-1.5 text-2xl font-semibold tabular text-warning">
            {all.length - activeCount}
          </p>
        </div>
      </div>

      <FilterBar
        activeCount={activeFilters}
        onClear={() => {
          setSearch('')
          setRoleFilter('')
        }}
      >
        <FilterInput
          id="people-search"
          label="Search"
          value={search}
          onChange={setSearch}
          placeholder="Name, title or department"
          className="min-w-56 flex-1"
        />
        {roles.data && roles.data.length > 0 ? (
          <div className="w-52">
            <label
              htmlFor="people-role"
              className="mb-1 block text-xs font-medium text-muted-foreground"
            >
              Role
            </label>
            <Select
              id="people-role"
              value={roleFilter}
              onChange={(event) => setRoleFilter(event.target.value)}
            >
              <option value="">All roles</option>
              {roles.data.map((role) => (
                <option key={role.key} value={role.key}>
                  {role.name}
                </option>
              ))}
            </Select>
          </div>
        ) : null}
      </FilterBar>

      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.public_id}
        caption="Members of this company"
        exportName="people"
        csv={[
          { header: 'Membership ID', value: (row) => row.public_id },
          { header: 'User ID', value: (row) => row.user.public_id },
          { header: 'Name', value: (row) => row.user.display_name },
          { header: 'Email-free headline', value: (row) => row.user.headline },
          { header: 'Role', value: (row) => row.role_name },
          { header: 'Role key', value: (row) => row.role_key },
          { header: 'Status', value: (row) => row.status },
          { header: 'Job title', value: (row) => row.job_title },
          { header: 'Department', value: (row) => row.department },
          { header: 'Joined', value: (row) => row.joined_at },
        ]}
        emptyState={
          <EmptyState
            icon={<Users aria-hidden />}
            title={activeFilters > 0 ? 'Nobody matches those filters' : 'No members yet'}
            description={
              activeFilters > 0
                ? 'Try a different name, or clear the role filter.'
                : 'Invite the people you work with to give them access.'
            }
            action={
              activeFilters > 0 ? (
                <Button
                  variant="outline"
                  onClick={() => {
                    setSearch('')
                    setRoleFilter('')
                  }}
                >
                  Clear filters
                </Button>
              ) : (
                <InviteDialog triggerLabel="Invite someone" />
              )
            }
          />
        }
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Invite                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Invite a person.
 *
 * The API creates the membership and returns an `invitation_token`; the token is
 * shown once so it can be shared through whatever channel the team already uses.
 * There is no email send in this build, so claiming one would be false.
 */
function InviteDialog({ triggerLabel = 'Invite' }: { triggerLabel?: string }) {
  const { activeCompanyPublicId, refresh, can } = useCompany()
  const [open, setOpen] = React.useState(false)
  const [email, setEmail] = React.useState('')
  const [roleKey, setRoleKey] = React.useState('')
  const [jobTitle, setJobTitle] = React.useState('')
  const [invitation, setInvitation] = React.useState<{ public_id: string; invitation_token: string } | null>(
    null,
  )
  const [error, setError] = React.useState<string | null>(null)

  const roles = useCompanyQuery<Role[]>({
    companyPublicId: activeCompanyPublicId,
    queryKey: ['company', 'roles'],
    path: '/companies/current/roles',
    enabled: can('roles.read'),
  })

  React.useEffect(() => {
    if (open) {
      setEmail('')
      setRoleKey(roles.data?.[0]?.key ?? '')
      setJobTitle('')
      setInvitation(null)
      setError(null)
    }
  }, [open, roles.data])

  const invite = useCompanyMutation<{ public_id: string; invitation_token: string }, {
    email: string
    role_key: string
    job_title?: string
  }>({
    context: { companyPublicId: activeCompanyPublicId },
    mutationFn: (body) =>
      api.post<{ public_id: string; invitation_token: string }>(
        '/companies/current/invitations',
        body,
        { companyPublicId: activeCompanyPublicId },
      ),
    invalidate: [['company', 'members']],
    onSuccess: (result) => {
      setInvitation(result)
      notifySuccess('Invitation created.', 'Share the invitation link with them.')
      void refresh()
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()

    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) {
      setError('Enter a valid email address.')
      return
    }
    if (!roleKey) {
      setError('Choose a role for this person.')
      return
    }

    setError(null)
    try {
      await invite.mutateAsync({
        email: email.trim().toLowerCase(),
        role_key: roleKey,
        ...(jobTitle.trim() ? { job_title: jobTitle.trim() } : {}),
      })
    } catch (cause) {
      notifyError(cause, 'The invitation could not be created.')
    }
  }

  if (!can('members.invite')) {
    return (
      <p className="text-sm text-muted-foreground">
        You do not have permission to invite people to this company.
      </p>
    )
  }

  if (invitation) {
    return (
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Invitation created"
        description="Share this link with the person you invited. It is shown once and cannot be retrieved later."
        footer={
          <Button
            onClick={() => {
              setInvitation(null)
              setOpen(false)
            }}
          >
            Done
          </Button>
        }
      >
        <div className="space-y-3">
          <Field label="Invitation link">
            <Input readOnly value={`/invitations/${invitation.invitation_token}`} className="font-mono text-xs" />
          </Field>
          <p className="text-xs text-muted-foreground">
            Invitation ID <PublicId value={invitation.public_id} />
          </p>
        </div>
      </Dialog>
    )
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <UserPlus aria-hidden />
        {triggerLabel}
      </Button>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Invite someone"
        description="They will be able to sign in with this email address and will join with the role you choose."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)} disabled={invite.isPending}>
              Cancel
            </Button>
            <Button type="submit" form="invite-member" loading={invite.isPending}>
              Create invitation
            </Button>
          </>
        }
      >
        <form id="invite-member" onSubmit={submit} className="space-y-4">
          <Field label="Work email" error={error ?? undefined} required>
            <Input
              id="invite-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={Boolean(error)}
              placeholder="them@company.com"
            />
          </Field>

          <Field label="Role" required>
            {roles.isPending ? (
              <Skeleton className="h-10 w-full" />
            ) : (roles.data?.length ?? 0) === 0 ? (
              <p className="text-sm text-muted-foreground">
                No assignable roles are available to you.
              </p>
            ) : (
              <Select id="invite-role" value={roleKey} onChange={(event) => setRoleKey(event.target.value)}>
                {(roles.data ?? []).map((role) => (
                  <option key={role.key} value={role.key}>
                    {role.name} ({role.member_count} members)
                  </option>
                ))}
              </Select>
            )}
          </Field>

          <Field label="Job title" hint="Optional">
            <Input
              id="invite-job-title"
              value={jobTitle}
              onChange={(event) => setJobTitle(event.target.value)}
            />
          </Field>
        </form>
      </Dialog>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* Member actions                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Change a member's role or deactivate them. Both endpoints are server-gated
 * on members.manage (and the last-SUPER_ADMIN guard lives in the database),
 * so these buttons only appear for administrators in the first place.
 */
function MemberActions({
  member,
  roles,
  companyPublicId,
}: {
  member: Membership
  roles: Role[]
  companyPublicId: string | null
}) {
  const [changing, setChanging] = React.useState(false)
  const [deactivating, setDeactivating] = React.useState(false)

  const deactivate = useCompanyMutation<unknown, string>({
    context: { companyPublicId },
    mutationFn: (reason) =>
      api.delete(`/companies/current/members/${member.public_id}?reason=${encodeURIComponent(reason)}`, {
        companyPublicId,
      }),
    invalidate: [['company', 'members']],
    onSuccess: () => {
      notifySuccess('Member deactivated.', 'Their history is retained for audit.')
      setDeactivating(false)
    },
  })

  return (
    <div className="flex items-center justify-end gap-1">
      <Button variant="ghost" size="sm" onClick={() => setChanging(true)}>
        Change role
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="text-danger hover:bg-danger-soft"
        onClick={() => setDeactivating(true)}
      >
        Deactivate
      </Button>

      {changing ? (
        <ChangeRoleDialog
          member={member}
          roles={roles}
          companyPublicId={companyPublicId}
          onClose={() => setChanging(false)}
        />
      ) : null}

      <ReasonDialog
        open={deactivating}
        onOpenChange={setDeactivating}
        title={`Deactivate ${member.user.display_name}`}
        description="Deactivation keeps every record they touched. It only ends their access, and the server records who did it and why."
        confirmLabel="Deactivate member"
        label="Reason for deactivation"
        busy={deactivate.isPending}
        error={deactivate.isError ? deactivate.error : null}
        onConfirm={(reason) => deactivate.mutate(reason)}
      />
    </div>
  )
}

function ChangeRoleDialog({
  member,
  roles,
  companyPublicId,
  onClose,
}: {
  member: Membership
  roles: Role[]
  companyPublicId: string | null
  onClose: () => void
}) {
  const [roleKey, setRoleKey] = React.useState(member.role_key)

  const change = useCompanyMutation<unknown, { role_key: string }>({
    context: { companyPublicId },
    mutationFn: (body) =>
      api.patch(`/companies/current/members/${member.public_id}/role`, body, {
        companyPublicId,
      }),
    invalidate: [['company', 'members']],
    onSuccess: () => {
      notifySuccess('Role updated.', `${member.user.display_name} is now ${roleKey}.`)
      onClose()
    },
  })

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!roleKey || roleKey === member.role_key) {
      onClose()
      return
    }
    try {
      await change.mutateAsync({ role_key: roleKey })
    } catch (cause) {
      notifyError(cause, 'The role could not be changed.')
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
      title={`Change ${member.user.display_name}'s role`}
      description={`Currently ${member.role_name}. The change takes effect immediately and is audited.`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={change.isPending}>
            Cancel
          </Button>
          <Button type="submit" form="change-role" loading={change.isPending}>
            Save role
          </Button>
        </>
      }
    >
      <form id="change-role" onSubmit={submit} className="space-y-4">
        <Field label="Role" required>
          <Select id="member-role" value={roleKey} onChange={(event) => setRoleKey(event.target.value)}>
            {roles.map((role) => (
              <option key={role.key} value={role.key}>
                {role.name} ({role.key})
              </option>
            ))}
          </Select>
        </Field>
      </form>
    </Dialog>
  )
}
