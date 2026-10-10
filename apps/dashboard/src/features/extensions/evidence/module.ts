import type { TimelineDataTrackProvider } from '../../timeline/data-tracks/types'
import type { EvidenceSources } from './sources'

import EvidenceView from './EvidenceView.vue'

import { EVIDENCE_VIEW_ID } from './payload'
import { createEvidencePlayerProvider, createEvidenceSessionProvider } from './providers'
import { evidenceSources } from './sources'

export { EVIDENCE_PLAYER_PROVIDER_ID, EVIDENCE_SESSION_PROVIDER_ID, EVIDENCE_VIEW_ID } from './payload'

/** Data track providers of the evidence extension: world containers, divergences, interactions, visibility. */
export function createEvidenceProviders(sources: EvidenceSources = evidenceSources): TimelineDataTrackProvider[] {
  return [createEvidenceSessionProvider(sources), createEvidencePlayerProvider(sources)]
}

export const evidenceExtension = {
  providers: createEvidenceProviders(),
  view: {
    component: EvidenceView,
    icon: 'i-mingcute-file-search-line',
    id: EVIDENCE_VIEW_ID,
    label: 'Evidence',
  },
} as const
