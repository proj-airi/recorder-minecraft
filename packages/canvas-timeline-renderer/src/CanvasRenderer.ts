/* SPDX-License-Identifier: MPL-2.0 */
// NOTICE: The React component was removed at this package boundary. These worker protocol types
// retain the declarations used by `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L15-L60`.

export type CanvasRendererRenderReason = 'init' | 'options' | 'playhead' | 'resize' | 'state'

export interface CanvasRendererStats {
  completedAt: number
  drawDurationMs: number
  reason: CanvasRendererRenderReason
  startedAt: number
}
