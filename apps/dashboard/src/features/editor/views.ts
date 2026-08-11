export type EditorViewId = 'inputs' | 'monitor' | 'preview' | 'resources' | 'timeline' | `extension:${string}`

export interface EditorViewOption {
  active: boolean
  icon: string
  id: EditorViewId
  label: string
  open: boolean
}
