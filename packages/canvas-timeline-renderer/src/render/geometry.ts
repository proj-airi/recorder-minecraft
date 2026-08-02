/* SPDX-License-Identifier: MPL-2.0 */
// NOTICE: Ported without behavioral or styling changes from
// `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/render/geometry.ts#L1-L20`.

import { toSeconds } from '@techsquidtv/canvas-timeline-utils';
import type { RenderContext } from '#renderer/render/types';

export function timeToX({ state }: RenderContext, time: Parameters<typeof toSeconds>[0]) {
  return Math.floor(toSeconds(time) * state.zoomScale - state.scrollLeft);
}

export function secondsToX({ state }: RenderContext, seconds: number) {
  return Math.floor(seconds * state.zoomScale - state.scrollLeft);
}

export function getContentWidth({ state, width }: RenderContext) {
  return state.duration
    ? Math.max(0, toSeconds(state.duration) * state.zoomScale - state.scrollLeft)
    : width;
}

export function getActiveWidth(renderContext: RenderContext) {
  return Math.min(renderContext.width, getContentWidth(renderContext));
}
