import type { Metadata } from 'next'

import { ProjectDetail } from '../project-detail'

export const metadata: Metadata = { title: 'Project Team' }

/**
 * A deep link into the project dashboard.
 *
 * A project is one record with one aggregate payload, so a separate page per
 * tab would mean a second fetch of identical data. These routes exist so a tab
 * can be linked to and bookmarked, and each simply opens its tab.
 */
export default function ProjectTeamPage() {
  return <ProjectDetail initialTab="team" />
}