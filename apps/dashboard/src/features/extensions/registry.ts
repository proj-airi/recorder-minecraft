import type { Component } from 'vue'

import type { TimelineDataTrackProvider } from '../timeline/data-tracks/types'
import type { PlayExtensionModule } from './domain'

import { airicraftPlannerModule } from './airicraft-planner/module'
import { extensionViewId } from './domain'
import { evidenceExtension } from './evidence/module'

export const playExtensionModules: readonly PlayExtensionModule[] = [airicraftPlannerModule]

/** A Dockview view contributed by an extension. */
export interface WorkspaceExtensionView {
  component: Component
  icon: string
  id: `extension:${string}`
  label: string
}

/** Every extension view: one per Play extension module plus the evidence details view. */
export const workspaceExtensionViews: readonly WorkspaceExtensionView[] = [
  ...playExtensionModules.map(module => ({
    component: module.view.component,
    icon: module.view.icon,
    id: extensionViewId(module.extensionType),
    label: module.view.label,
  })),
  evidenceExtension.view,
]

/**
 * Data track providers contributed by extensions. Play extension modules are served by the
 * built-in `play-extension` provider; these are the providers registered on top of it.
 */
export const extensionDataTrackProviders: readonly TimelineDataTrackProvider[] = evidenceExtension.providers

export function extensionLabel(extensionType: string): string {
  return playExtensionModules.find(module => module.extensionType === extensionType)?.view.label ?? extensionType
}
