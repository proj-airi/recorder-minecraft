/* SPDX-License-Identifier: MPL-2.0 */
// NOTICE: This factory is the bundler-neutral seam replacing the React component's inline worker construction at
// `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L307-L318`.

export function createRendererWorker(): Worker {
  return new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' })
}
