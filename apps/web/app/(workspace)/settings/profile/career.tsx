'use client'

import * as React from 'react'
import { GraduationCap, Briefcase, ShieldCheck, Plus, Trash2 } from 'lucide-react'

import { api } from '@/lib/api'
import { formatDate } from '@/lib/utils'
import { Button, Card, CardContent, CardHeader, CardTitle, EmptyState } from '@/components/ui'
import { ErrorState, LoadingBlock } from '@/components/query'
import { notifyError, notifySuccess } from '@/components/toast'

interface Education {
  id: string
  institution: string
  degree: string | null
  field_of_study: string | null
  start_date: string | null
  end_date: string | null
  grade: string | null
  visible: boolean
}

interface Experience {
  id: string
  company_name: string
  title: string
  employment_type: string | null
  location: string | null
  start_date: string | null
  end_date: string | null
  is_current: boolean
  visible: boolean
}

interface Skill {
  skill_id: string
  name: string
  category: string | null
  proficiency: number
  years_experience: string | null
  visible: boolean
}

interface Visa {
  visa_status: string | null
  work_authorization: string | null
}

const VISA_OPTIONS = [
  'CITIZEN',
  'PERMANENT_RESIDENT',
  'H1B',
  'H4_EAD',
  'L1',
  'F1_OPT',
  'TN',
  'EAD_OTHER',
  'OTHER',
]

/**
 * Career history: education, experience, skills and work authorization.
 *
 * Education and experience are professional data that ride along on the visible
 * profile. Work authorization is protected: the API returns it only to its
 * owner, and nothing here writes it anywhere else.
 */
export function CareerSection() {
  const [education, setEducation] = React.useState<Education[] | null>(null)
  const [experience, setExperience] = React.useState<Experience[] | null>(null)
  const [skills, setSkills] = React.useState<Skill[] | null>(null)
  const [visa, setVisa] = React.useState<Visa | null>(null)
  const [error, setError] = React.useState<unknown>(null)

  const load = React.useCallback(async () => {
    try {
      setError(null)
      const [edu, exp, sk, vs] = await Promise.all([
        api.get<Education[]>('/users/me/education'),
        api.get<Experience[]>('/users/me/experience'),
        api.get<Skill[]>('/users/me/skills'),
        api.get<Visa>('/users/me/visa'),
      ])
      setEducation(edu)
      setExperience(exp)
      setSkills(sk)
      setVisa(vs)
    } catch (cause) {
      setError(cause)
    }
  }, [])

  React.useEffect(() => {
    void load()
  }, [load])

  if (error) return <ErrorState error={error} onRetry={() => void load()} />
  if (education === null || experience === null || skills === null || visa === null) {
    return <LoadingBlock rows={4} />
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <EducationCard entries={education} onChange={load} />
      <ExperienceCard entries={experience} onChange={load} />
      <SkillsCard entries={skills} onChange={load} />
      <VisaCard visa={visa} onChange={load} />
    </div>
  )
}

function EducationCard({ entries, onChange }: { entries: Education[]; onChange: () => Promise<void> }) {
  const [adding, setAdding] = React.useState(false)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <GraduationCap aria-hidden className="size-4 text-primary" />
            Education
          </span>
          <Button size="sm" variant="outline" onClick={() => setAdding((v) => !v)}>
            <Plus aria-hidden />
            Add
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {adding ? (
          <EducationForm
            onDone={async () => {
              setAdding(false)
              await onChange()
            }}
            onCancel={() => setAdding(false)}
          />
        ) : null}
        {entries.length === 0 && !adding ? (
          <EmptyState title="No education recorded" description="Add degrees and credentials." />
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li key={entry.id} className="flex items-start justify-between gap-3 border-b border-border/60 pb-2 text-sm">
                <div className="min-w-0">
                  <p className="font-medium">{entry.institution}</p>
                  <p className="text-xs text-muted-foreground">
                    {[entry.degree, entry.field_of_study].filter(Boolean).join(' · ') || '—'}
                  </p>
                  <p className="text-xs text-subtle-foreground">
                    {entry.start_date ? formatDate(entry.start_date) : '—'} –{' '}
                    {entry.end_date ? formatDate(entry.end_date) : 'present'}
                  </p>
                </div>
                <DeleteButton path={`/users/me/education/${entry.id}`} onDone={onChange} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function EducationForm({ onDone, onCancel }: { onDone: () => Promise<void>; onCancel: () => void }) {
  const [institution, setInstitution] = React.useState('')
  const [degree, setDegree] = React.useState('')
  const [field, setField] = React.useState('')
  const [startDate, setStartDate] = React.useState('')
  const [endDate, setEndDate] = React.useState('')
  const [busy, setBusy] = React.useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (institution.trim().length < 1) return
    setBusy(true)
    try {
      await api.post('/users/me/education', {
        institution: institution.trim(),
        degree: degree.trim() || null,
        field_of_study: field.trim() || null,
        start_date: startDate || null,
        end_date: endDate || null,
      })
      notifySuccess('Education added.')
      await onDone()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-2 rounded-md border border-border p-3">
      <input
        aria-label="Institution"
        placeholder="Institution *"
        value={institution}
        onChange={(event) => setInstitution(event.target.value)}
        className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
      />
      <div className="grid grid-cols-2 gap-2">
        <input
          aria-label="Degree"
          placeholder="Degree"
          value={degree}
          onChange={(event) => setDegree(event.target.value)}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          aria-label="Field of study"
          placeholder="Field of study"
          value={field}
          onChange={(event) => setField(event.target.value)}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          aria-label="Start date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          aria-label="End date"
          type="date"
          value={endDate}
          onChange={(event) => setEndDate(event.target.value)}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={busy}>
          Save
        </Button>
      </div>
    </form>
  )
}

function ExperienceCard({ entries, onChange }: { entries: Experience[]; onChange: () => Promise<void> }) {
  const [adding, setAdding] = React.useState(false)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <Briefcase aria-hidden className="size-4 text-primary" />
            Career
          </span>
          <Button size="sm" variant="outline" onClick={() => setAdding((v) => !v)}>
            <Plus aria-hidden />
            Add
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {adding ? (
          <ExperienceForm
            onDone={async () => {
              setAdding(false)
              await onChange()
            }}
            onCancel={() => setAdding(false)}
          />
        ) : null}
        {entries.length === 0 && !adding ? (
          <EmptyState title="No career history" description="Add roles you have held." />
        ) : (
          <ul className="space-y-2">
            {entries.map((entry) => (
              <li key={entry.id} className="flex items-start justify-between gap-3 border-b border-border/60 pb-2 text-sm">
                <div className="min-w-0">
                  <p className="font-medium">{entry.title}</p>
                  <p className="text-xs text-muted-foreground">
                    {entry.company_name}
                    {entry.location ? ` · ${entry.location}` : ''}
                  </p>
                  <p className="text-xs text-subtle-foreground">
                    {entry.start_date ? formatDate(entry.start_date) : '—'} –{' '}
                    {entry.is_current ? 'present' : entry.end_date ? formatDate(entry.end_date) : '—'}
                  </p>
                </div>
                <DeleteButton path={`/users/me/experience/${entry.id}`} onDone={onChange} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function ExperienceForm({ onDone, onCancel }: { onDone: () => Promise<void>; onCancel: () => void }) {
  const [company, setCompany] = React.useState('')
  const [title, setTitle] = React.useState('')
  const [startDate, setStartDate] = React.useState('')
  const [endDate, setEndDate] = React.useState('')
  const [current, setCurrent] = React.useState(false)
  const [busy, setBusy] = React.useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!company.trim() || !title.trim()) return
    setBusy(true)
    try {
      await api.post('/users/me/experience', {
        company_name: company.trim(),
        title: title.trim(),
        start_date: startDate || null,
        end_date: current ? null : endDate || null,
        is_current: current,
      })
      notifySuccess('Career entry added.')
      await onDone()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={(event) => void submit(event)} className="space-y-2 rounded-md border border-border p-3">
      <input
        aria-label="Company"
        placeholder="Company *"
        value={company}
        onChange={(event) => setCompany(event.target.value)}
        className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
      />
      <input
        aria-label="Job title"
        placeholder="Job title *"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
      />
      <div className="grid grid-cols-2 gap-2">
        <input
          aria-label="Start date"
          type="date"
          value={startDate}
          onChange={(event) => setStartDate(event.target.value)}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
        <input
          aria-label="End date"
          type="date"
          value={endDate}
          disabled={current}
          onChange={(event) => setEndDate(event.target.value)}
          className="h-9 rounded-md border border-input bg-background px-2 text-sm"
        />
      </div>
      <label className="flex items-center gap-2 text-xs text-muted-foreground">
        <input type="checkbox" checked={current} onChange={(event) => setCurrent(event.target.checked)} className="size-3.5 accent-primary" />
        I currently work here
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
        <Button type="submit" size="sm" loading={busy}>
          Save
        </Button>
      </div>
    </form>
  )
}

function SkillsCard({ entries, onChange }: { entries: Skill[]; onChange: () => Promise<void> }) {
  const [name, setName] = React.useState('')
  const [proficiency, setProficiency] = React.useState('3')
  const [busy, setBusy] = React.useState(false)

  const add = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return
    setBusy(true)
    try {
      await api.post('/users/me/skills', { skill_name: name.trim(), proficiency: Number(proficiency) })
      setName('')
      notifySuccess('Skill added.')
      await onChange()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Skills</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <form onSubmit={(event) => void add(event)} className="flex gap-2">
          <input
            aria-label="Skill"
            placeholder="Add a skill"
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
          />
          <select
            aria-label="Proficiency"
            value={proficiency}
            onChange={(event) => setProficiency(event.target.value)}
            className="h-9 rounded-md border border-input bg-background px-2 text-sm"
          >
            {[1, 2, 3, 4, 5].map((level) => (
              <option key={level} value={level}>{level}</option>
            ))}
          </select>
          <Button type="submit" size="sm" loading={busy}>
            Add
          </Button>
        </form>
        {entries.length === 0 ? (
          <EmptyState title="No skills yet" description="Skills power workforce matching and search." />
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {entries.map((skill) => (
              <li
                key={skill.skill_id}
                className="flex items-center gap-1.5 rounded-full border border-border bg-muted/50 py-1 pl-3 pr-1.5 text-xs"
              >
                <span>{skill.name}</span>
                <span className="text-subtle-foreground">·{skill.proficiency}</span>
                <button
                  type="button"
                  aria-label={`Remove ${skill.name}`}
                  onClick={() =>
                    void api
                      .delete(`/users/me/skills/${skill.skill_id}`)
                      .then(() => onChange())
                      .catch((cause) => notifyError(cause))
                  }
                  className="text-subtle-foreground hover:text-danger"
                >
                  <Trash2 aria-hidden className="size-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function VisaCard({ visa, onChange }: { visa: Visa; onChange: () => Promise<void> }) {
  const [status, setStatus] = React.useState(visa.visa_status ?? '')
  const [detail, setDetail] = React.useState(visa.work_authorization ?? '')
  const [busy, setBusy] = React.useState(false)

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      await api.put('/users/me/visa', {
        visa_status: status || null,
        work_authorization: detail || null,
      })
      notifySuccess('Work authorization saved.')
      await onChange()
    } catch (cause) {
      notifyError(cause)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck aria-hidden className="size-4 text-primary" />
          Work authorization
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={(event) => void save(event)} className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Visible to you only. Never shown on your public profile or to other
            companies through the API.
          </p>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Status</span>
            <select
              aria-label="Visa status"
              value={status}
              onChange={(event) => setStatus(event.target.value)}
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            >
              <option value="">Not set</option>
              {VISA_OPTIONS.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Detail (optional)</span>
            <input
              aria-label="Work authorization detail"
              value={detail}
              onChange={(event) => setDetail(event.target.value)}
              placeholder="e.g. EAD category"
              className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
            />
          </label>
          <div className="flex justify-end">
            <Button type="submit" size="sm" loading={busy}>
              Save
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function DeleteButton({ path, onDone }: { path: string; onDone: () => Promise<void> }) {
  const [busy, setBusy] = React.useState(false)
  return (
    <button
      type="button"
      aria-label="Remove"
      disabled={busy}
      onClick={() => {
        setBusy(true)
        void api
          .delete(path)
          .then(() => onDone())
          .catch((cause) => notifyError(cause))
          .finally(() => setBusy(false))
      }}
      className="shrink-0 text-subtle-foreground transition-colors hover:text-danger disabled:opacity-50"
    >
      <Trash2 aria-hidden className="size-4" />
    </button>
  )
}
