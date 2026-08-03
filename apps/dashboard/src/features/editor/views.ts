export type EditorViewId = 'inputs' | 'monitor' | 'preview' | 'resources' | 'timeline'

export interface EditorViewOption {
  active: boolean
  icon: string
  id: EditorViewId
  label: string
  open: boolean
}
