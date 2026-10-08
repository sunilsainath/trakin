'use client'

import * as React from 'react'
import { z } from 'zod'

import { api } from '@/lib/api'
import { useCompany } from '@/hooks/use-company'
import type { Me } from '@/lib/types'
import { PageHeader, PageShell } from '@/components/page'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui'
import { SchemaForm, type FieldConfig } from '@/components/ui/schema-form'
import { Alert } from '@/components/ui'
import { ErrorState, LoadingBlock } from '@/components/query'

const profileSchema = z.object({
  first_name: z.string().min(1, 'Enter your first name.').max(100),
  last_name: z.string().min(1, 'Enter your last name.').max(100),
  phone_e164: z.string().max(20).optional().or(z.literal('')),
  headline: z.string().max(200).optional().or(z.literal('')),
  bio: z.string().max(5000).optional().or(z.literal('')),
  location_city: z.string().max(100).optional().or(z.literal('')),
  location_country: z.string().max(2).optional().or(z.literal('')),
  timezone: z.string().max(64).optional().or(z.literal('')),
  years_experience: z.string().optional().or(z.literal('')),
  availability_status: z.string().max(64).optional().or(z.literal('')),
  visa_status: z.string().optional().or(z.literal('')),
})

type ProfileValues = z.infer<typeof profileSchema>

const VISA_STATUSES = [
  { value: '', label: 'Prefer not to say' },
  { value: 'CITIZEN', label: 'Citizen' },
  { value: 'PERMANENT_RESIDENT', label: 'Permanent resident' },
  { value: 'WORK_VISA', label: 'Work visa' },
  { value: 'STUDENT_VISA', label: 'Student visa' },
  { value: 'OTHER', label: 'Other' },
  { value: 'PREFER_NOT_TO_SAY', label: 'Prefer not to say' },
]

const profileFields: FieldConfig[] = [
  { name: 'first_name', label: 'First name' },
  { name: 'last_name', label: 'Last name' },
  { name: 'phone_e164', label: 'Phone', type: 'tel', hint: 'E.164 format, e.g. +14155550132' },
  { name: 'headline', label: 'Professional headline', placeholder: 'Senior Java Developer' },
  { name: 'bio', label: 'Bio' },
  { name: 'location_city', label: 'City' },
  { name: 'location_country', label: 'Country code', placeholder: 'IN', maxLength: 2 },
  { name: 'timezone', label: 'Timezone', placeholder: 'Asia/Kolkata' },
  { name: 'years_experience', label: 'Years of experience', placeholder: '6' },
  { name: 'availability_status', label: 'Availability', placeholder: 'Open to work' },
  {
    name: 'visa_status',
    label: 'Visa status',
    options: VISA_STATUSES,
    hint: 'Optional. Shown under your profile visibility rules.',
  },
]

const VISIBILITIES = ['PUBLIC', 'CONNECTIONS', 'PRIVATE'] as const

interface EducationRow {
  id: string
  institution: string
  degree: string | null
  field_of_study: string | null
  start_date: string | null
  end_date: string | null
}

interface ExperienceRow {
  id: string
  company_name: string | null
  title: string
  employment_type: string | null
  start_date: string | null
  end_date: string | null
  is_current: boolean
}

interface SkillRow {
  skill_id: string
  name: string
  proficiency: number
  years_experience: string | null
}

const PROFICIENCY_LABELS = ['', 'Learning', 'Basic', 'Intermediate', 'Advanced', 'Expert']

/**
 * Education, experience and skills. Each section lists what is stored and
 * offers an inline add form plus removal; everything is owner-scoped
 * server-side, so these screens cannot touch anyone else's history.
 */
function CareerSections() {
  const [education, setEducation] = React.useState<EducationRow[] | null>(null)
  const [experience, setExperience] = React.useState<ExperienceRow[] | null>(null)
  const [skills, setSkills] = React.useState<SkillRow[] | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const [edu, exp, skl] = await Promise.all([
        api.get<EducationRow[]>('/users/me/education'),
        api.get<ExperienceRow[]>('/users/me/experience'),
        api.get<SkillRow[]>('/users/me/skills'),
      ])
      setEducation(edu)
      setExperience(exp)
      setSkills(skl)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Career history could not be loaded.')
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  return (
    <Card>
      <CardHeader>
        <CardTitle>Career history</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {error ? (
          <Alert tone="danger">{error}</Alert>
        ) : education === null ? (
          <LoadingBlock />
        ) : (
          <>
            <EducationSection rows={education} onChanged={() => void load()} />
            <ExperienceSection rows={experience ?? []} onChanged={() => void load()} />
            <SkillsSection rows={skills ?? []} onChanged={() => void load()} />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function useAsyncAction() {
  const [busy, setBusy] = React.useState(false)
  const [problem, setProblem] = React.useState<string | null>(null)
  const run = async (work: () => Promise<unknown>) => {
    setBusy(true)
    setProblem(null)
    try {
      await work()
      return true
    } catch (cause) {
      setProblem(cause instanceof Error ? cause.message : 'That did not work.')
      return false
    } finally {
      setBusy(false)
    }
  }
  return { busy, problem, setProblem, run }
}

function EducationSection({ rows, onChanged }: { rows: EducationRow[]; onChanged: () => void }) {
  const action = useAsyncAction()
  const [institution, setInstitution] = React.useState('')
  const [degree, setDegree] = React.useState('')

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Education</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No education added yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-2 text-sm">
              <span className="min-w-0">
                <span className="font-medium">{row.institution}</span>
                {row.degree ? <span className="text-muted-foreground"> · {row.degree}</span> : null}
                {row.field_of_study ? (
                  <span className="text-muted-foreground"> · {row.field_of_study}</span>
                ) : null}
              </span>
              <RemoveButton
                label={`Remove ${row.institution}`}
                onRemove={() => action.run(() => api.delete(`/users/me/education/${row.id}`).then(onChanged))}
              />
            </li>
          ))}
        </ul>
      )}
      {action.problem ? <p role="alert" className="text-xs text-danger">{action.problem}</p> : null}
      <div className="flex flex-wrap gap-2">
        <input
          value={institution}
          onChange={(event) => setInstitution(event.target.value)}
          placeholder="Institution"
          aria-label="Institution"
          className="h-9 min-w-40 flex-1 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          value={degree}
          onChange={(event) => setDegree(event.target.value)}
          placeholder="Degree (optional)"
          aria-label="Degree"
          className="h-9 min-w-40 flex-1 rounded-md border border-input bg-background px-2 text-sm"
        />
        <button
          type="button"
          disabled={action.busy || !institution.trim()}
          onClick={() =>
            void action
              .run(() => api.post('/users/me/education', { institution: institution.trim(), degree: degree.trim() || undefined }))
              .then((ok) => {
                if (ok) {
                  setInstitution('')
                  setDegree('')
                  onChanged()
                }
              })
          }
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {action.busy ? 'Adding…' : 'Add'}
        </button>
      </div>
    </section>
  )
}

function ExperienceSection({ rows, onChanged }: { rows: ExperienceRow[]; onChanged: () => void }) {
  const action = useAsyncAction()
  const [company, setCompany] = React.useState('')
  const [title, setTitle] = React.useState('')
  const [start, setStart] = React.useState('')

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Experience</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No career entries yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((row) => (
            <li key={row.id} className="flex items-center justify-between gap-2 text-sm">
              <span className="min-w-0">
                <span className="font-medium">{row.title}</span>
                {row.company_name ? <span className="text-muted-foreground"> · {row.company_name}</span> : null}
                {row.start_date ? (
                  <span className="block text-xs text-muted-foreground">
                    {row.start_date}{row.end_date ? ` – ${row.end_date}` : row.is_current ? ' – present' : ''}
                  </span>
                ) : null}
              </span>
              <RemoveButton
                label={`Remove ${row.title}`}
                onRemove={() => action.run(() => api.delete(`/users/me/experience/${row.id}`).then(onChanged))}
              />
            </li>
          ))}
        </ul>
      )}
      {action.problem ? <p role="alert" className="text-xs text-danger">{action.problem}</p> : null}
      <div className="flex flex-wrap gap-2">
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Role title"
          aria-label="Role title"
          className="h-9 min-w-40 flex-1 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          value={company}
          onChange={(event) => setCompany(event.target.value)}
          placeholder="Company (optional)"
          aria-label="Company"
          className="h-9 min-w-40 flex-1 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          type="date"
          value={start}
          onChange={(event) => setStart(event.target.value)}
          aria-label="Start date"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
        <button
          type="button"
          disabled={action.busy || !title.trim() || !start}
          onClick={() =>
            void action
              .run(() =>
                api.post('/users/me/experience', {
                  title: title.trim(),
                  company_name: company.trim() || undefined,
                  start_date: start || undefined,
                }),
              )
              .then((ok) => {
                if (ok) {
                  setTitle('')
                  setCompany('')
                  setStart('')
                  onChanged()
                }
              })
          }
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {action.busy ? 'Adding…' : 'Add'}
        </button>
      </div>
    </section>
  )
}

function SkillsSection({ rows, onChanged }: { rows: SkillRow[]; onChanged: () => void }) {
  const action = useAsyncAction()
  const [name, setName] = React.useState('')
  const [proficiency, setProficiency] = React.useState('3')

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Skills</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No skills yet.</p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {rows.map((row) => (
            <li
              key={row.skill_id}
              className="flex items-center gap-1.5 rounded-full border border-border px-3 py-1 text-xs"
            >
              <span className="font-medium">{row.name}</span>
              <span className="text-muted-foreground">
                {PROFICIENCY_LABELS[row.proficiency] ?? row.proficiency}
              </span>
              <RemoveButton
                label={`Remove ${row.name}`}
                onRemove={() => action.run(() => api.delete(`/users/me/skills/${row.skill_id}`).then(onChanged))}
              />
            </li>
          ))}
        </ul>
      )}
      {action.problem ? <p role="alert" className="text-xs text-danger">{action.problem}</p> : null}
      <div className="flex flex-wrap gap-2">
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Skill, e.g. Welding"
          aria-label="Skill name"
          className="h-9 min-w-40 flex-1 rounded-md border border-input bg-background px-2 text-sm"
        />
        <select
          value={proficiency}
          onChange={(event) => setProficiency(event.target.value)}
          aria-label="Proficiency"
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        >
          {[1, 2, 3, 4, 5].map((level) => (
            <option key={level} value={level}>
              {level} · {PROFICIENCY_LABELS[level]}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={action.busy || !name.trim()}
          onClick={() =>
            void action
              .run(() => api.post('/users/me/skills', { skill_name: name.trim(), proficiency: Number(proficiency) }))
              .then((ok) => {
                if (ok) {
                  setName('')
                  onChanged()
                }
              })
          }
          className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
        >
          {action.busy ? 'Adding…' : 'Add'}
        </button>
      </div>
    </section>
  )
}

function RemoveButton({ label, onRemove }: { label: string; onRemove: () => Promise<boolean> }) {
  const [busy, setBusy] = React.useState(false)
  return (
    <button
      type="button"
      aria-label={label}
      disabled={busy}
      onClick={() => {
        setBusy(true)
        void onRemove().finally(() => setBusy(false))
      }}
      className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-danger-soft hover:text-danger disabled:opacity-50"
    >
      Remove
    </button>
  )
}

export default function ProfileSettingsPage() {
  const { me, loading, error, refresh } = useCompany()
  const [notice, setNotice] = React.useState<string | null>(null)
  const [visibility, setVisibility] = React.useState<Record<string, string>>({})
  const [savingVisibility, setSavingVisibility] = React.useState(false)

  React.useEffect(() => {
    const privacy = me?.settings?.privacy
    if (privacy && typeof privacy === 'object') {
      setVisibility(privacy as Record<string, string>)
    }
  }, [me])

  if (loading) {
    return (
      <PageShell>
        <LoadingBlock />
      </PageShell>
    )
  }

  if (error || !me) {
    return (
      <PageShell>
        <ErrorState error={error} onRetry={() => void refresh()} />
      </PageShell>
    )
  }

  const onSubmit = async (values: ProfileValues) => {
    setNotice(null)
    const changes: Record<string, string | number | null> = {}
    for (const [key, value] of Object.entries(values)) {
      if (value === '') {
        changes[key] = null
      } else {
        changes[key] = value
      }
    }
    if (typeof changes.years_experience === 'string') {
      const years = Number(changes.years_experience)
      changes.years_experience =
        changes.years_experience.trim() === '' || !Number.isFinite(years) || years < 0
          ? null
          : years
    }
    if (changes.visa_status === '') changes.visa_status = null
    await api.patch<Me>('/users/me', changes)
    setNotice('Profile saved.')
    await refresh()
  }

  const saveVisibility = async () => {
    setSavingVisibility(true)
    try {
      await api.put('/users/me/privacy', visibility)
      setNotice('Visibility saved.')
      await refresh()
    } finally {
      setSavingVisibility(false)
    }
  }

  return (
    <PageShell>
      <PageHeader title="Profile" description="How you appear across the platform." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Personal information</CardTitle>
          </CardHeader>
          <CardContent>
            {notice ? (
              <div className="mb-4">
                <Alert tone="info">{notice}</Alert>
              </div>
            ) : null}
            <SchemaForm<ProfileValues>
              schema={profileSchema}
              fields={profileFields}
              defaultValues={{
                first_name: me.first_name ?? '',
                last_name: me.last_name ?? '',
                phone_e164: me.phone_e164 ?? '',
                headline: me.headline ?? '',
                bio: me.bio ?? '',
                location_city: me.location_city ?? '',
                location_country: me.country_code ?? '',
                timezone: me.timezone ?? '',
                years_experience: me.years_experience?.toString() ?? '',
                availability_status: me.availability_status ?? '',
                visa_status: me.visa_status ?? '',
              }}
              submitLabel="Save profile"
              onSubmit={onSubmit}
              banner={null}
            />
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Field visibility</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Control who sees contact and location fields. Your company membership list
                is never visible to other users.
              </p>
              {['contact.email', 'contact.phone', 'location'].map((field) => (
                <label key={field} className="flex items-center justify-between gap-3 text-sm">
                  <span className="font-medium">{field}</span>
                  <select
                    value={visibility[field] ?? 'CONNECTIONS'}
                    onChange={(event) =>
                      setVisibility((current) => ({ ...current, [field]: event.target.value }))
                    }
                    className="h-9 rounded-md border border-input bg-surface px-2 text-sm"
                  >
                    {VISIBILITIES.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
              <button
                type="button"
                disabled={savingVisibility}
                onClick={() => void saveVisibility()}
                className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
              >
                {savingVisibility ? 'Saving…' : 'Save visibility'}
              </button>
            </CardContent>
          </Card>

          <CareerSections />
        </div>
      </div>
    </PageShell>
  )
}
