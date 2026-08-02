/* SPDX-License-Identifier: MPL-2.0 */
// NOTICE: Vue props preserve the React renderer's public options from
// `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L79-L135`.
// `engine` replaces `useTimeline()` and renderer callbacks become typed Vue events.

import type { TimelineRendererThemeInput, TimelineRenderOptions } from '@proj-airi/canvas-timeline-renderer'
import type { TimelineEngine, TimelineKeyframePropertyId } from '@techsquidtv/canvas-timeline-core'

export interface CanvasRendererError {
  cause?: Error
  message: string
  reason: 'invalid-options' | 'offscreen-unavailable' | 'worker-failed' | 'worker-unavailable'
}

export interface CanvasRendererProps extends Omit<TimelineRenderOptions, 'keyframeGeometry' | 'showKeyframes' | 'theme'> {
  className?: string
  engine: TimelineEngine
  keyframeProperty?: TimelineKeyframePropertyId
  showKeyframes?: boolean
  theme?: TimelineRendererThemeInput
  themeKey?: number | string
}
