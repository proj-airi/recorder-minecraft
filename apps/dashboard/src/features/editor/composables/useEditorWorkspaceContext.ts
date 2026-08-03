import { inject } from 'vue'

import { editorWorkspaceContextKey } from '../workspaceContext'

export function useEditorWorkspaceContext() {
  const context = inject(editorWorkspaceContextKey)
  if (!context)
    throw new Error('Editor workspace context is unavailable')
  return context
}
