import type { Metadata } from 'next'

import { ProjectDetail } from './project-detail'

export const metadata: Metadata = { title: 'Project' }

export default function ProjectPage() {
  return <ProjectDetail />
}