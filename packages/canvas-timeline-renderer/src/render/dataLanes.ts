/* SPDX-License-Identifier: MPL-2.0 */
// Recorder addition: data lanes draw point markers and interval bars on tracks that have no
// engine clips. Items are columnar so large lanes cross the worker boundary cheaply.

import type { RenderContext } from '#renderer/render/types';

import { fitText } from '#renderer/render/text';

export const DATA_LANE_POINT = 0;
export const DATA_LANE_INTERVAL = 1;

/**
 * One data lane, drawn on the track with the same id.
 *
 * Entries are sorted by `starts`. For a point, `ends[i] === starts[i]`.
 */
export interface TimelineDataLane {
  trackId: string;
  /** Timeline units per second of `starts` and `ends` (20 for Minecraft Server ticks). */
  ticksPerSecond: number;
  starts: Float64Array;
  ends: Float64Array;
  /** `DATA_LANE_POINT` or `DATA_LANE_INTERVAL` per entry. */
  kinds: Uint8Array;
  colors: readonly string[];
  labels: readonly string[];
  /** Largest `ends[i] - starts[i]`; bounds the backward scan for visible intervals. */
  maxSpan: number;
  /** Optional lane background. */
  fill?: string;
  /** Status text drawn when the lane has no entries, e.g. "Loading…". */
  message?: string;
  /** Message color; defaults to a muted foreground. */
  messageColor?: string;
}

export interface TimelineDataLaneSelection {
  trackId: string;
  index: number;
}

/** Points closer than this many pixels are aggregated into a density histogram. */
const DENSE_POINT_SPACING = 3;
const POINT_SIZE = 4;

export function lowerBound(values: Float64Array, target: number): number {
  let low = 0;
  let high = values.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (values[middle]! < target) low = middle + 1;
    else high = middle;
  }
  return low;
}

export function drawDataLane(
  renderContext: RenderContext,
  lane: TimelineDataLane,
  y: number,
  trackHeight: number,
  drawWidth: number,
  selectedIndex: number
) {
  const { ctx, state, theme } = renderContext;
  const pixelsPerTick = state.zoomScale / lane.ticksPerSecond;
  const tickToX = (tick: number) => tick * pixelsPerTick - state.scrollLeft;

  if (lane.fill) {
    ctx.fillStyle = lane.fill;
    ctx.fillRect(0, y, drawWidth, trackHeight);
  }

  const count = lane.starts.length;
  if (count === 0) {
    if (lane.message) {
      ctx.fillStyle = lane.messageColor ?? 'rgba(212, 212, 216, 0.55)';
      ctx.font = theme.fonts.clip;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      const message = fitText(ctx, lane.message, drawWidth - 16);
      if (message) ctx.fillText(message, 8, y + trackHeight / 2);
    }
    return;
  }

  const firstVisibleTick = state.scrollLeft / pixelsPerTick;
  const lastVisibleTick = (state.scrollLeft + drawWidth) / pixelsPerTick;
  const begin = lowerBound(lane.starts, firstVisibleTick - lane.maxSpan - 1);
  const end = lowerBound(lane.starts, lastVisibleTick + 1);
  const midY = y + trackHeight / 2;
  const barInset = Math.max(3, Math.floor(trackHeight * 0.22));
  const barY = y + barInset;
  const barHeight = Math.max(2, trackHeight - barInset * 2);

  // Intervals: merge sub-pixel runs so dense interval lanes cost O(visible pixels) fill calls.
  let runStart = -Infinity;
  let runEnd = -Infinity;
  let runColor = '';
  const flushRun = () => {
    if (runEnd > runStart) {
      ctx.fillStyle = runColor;
      ctx.globalAlpha = 0.55;
      ctx.fillRect(runStart, barY, Math.max(1, runEnd - runStart), barHeight);
      ctx.globalAlpha = 1;
    }
  };
  ctx.font = theme.fonts.clip;
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'left';
  // Right edge of the last outlined bar and of the last drawn label. Intervals are sorted by
  // start, so comparing with the previous one is enough to keep neighbours from painting over
  // each other.
  let previousBarRight = -Infinity;
  let previousLabelRight = -Infinity;
  for (let index = begin; index < end; index += 1) {
    if (lane.kinds[index] !== DATA_LANE_INTERVAL) continue;
    let x0 = Math.max(-2, tickToX(lane.starts[index]!));
    const x1 = Math.min(drawWidth + 2, tickToX(lane.ends[index]!));
    if (x1 < 0 || x0 > drawWidth) continue;
    const color = lane.colors[index] ?? theme.colors.clip.border;
    // Bars that touch within a pixel (back-to-back samples of one target) shared a border and
    // read as one overlapping box; leave a 1px gap. Real overlaps are drawn as they are.
    if (x0 < previousBarRight + 1 && x0 > previousBarRight - 2 && x1 - (previousBarRight + 1) >= 2)
      x0 = previousBarRight + 1;
    const width = x1 - x0;
    if (width < 2) {
      if (color === runColor && x0 <= runEnd + 1) {
        runEnd = Math.max(runEnd, x0 + Math.max(1, width));
        continue;
      }
      flushRun();
      runStart = x0;
      runEnd = x0 + Math.max(1, width);
      runColor = color;
      continue;
    }
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.fillRect(x0, barY, width, barHeight);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.strokeRect(Math.floor(x0) + 0.5, barY + 0.5, Math.max(1, Math.floor(width) - 1), barHeight - 1);
    previousBarRight = Math.max(previousBarRight, x0 + width);
    const label = lane.labels[index];
    // The label stays inside the on-screen part of its own bar and never starts under the
    // previous label; when that leaves too little room, the bar goes unlabeled.
    const labelLeft = Math.max(x0, 0, previousLabelRight) + 4;
    const labelRight = Math.min(x1, drawWidth) - 4;
    const text = label ? fitText(ctx, label, labelRight - labelLeft) : null;
    if (text) {
      ctx.fillStyle = theme.colors.clip.text;
      ctx.fillText(text, labelLeft, midY);
      previousLabelRight = labelLeft + ctx.measureText(text).width;
    }
  }
  flushRun();

  // Points: individual markers when sparse, a density histogram when they crowd together.
  let visiblePoints = 0;
  for (let index = begin; index < end; index += 1) {
    if (lane.kinds[index] === DATA_LANE_POINT) visiblePoints += 1;
  }
  if (visiblePoints > 0) {
    const dense = visiblePoints > drawWidth / DENSE_POINT_SPACING;
    if (dense) drawPointHistogram(renderContext, lane, begin, end, tickToX, y, trackHeight, drawWidth);
    else drawPointMarkers(renderContext, lane, begin, end, tickToX, midY, trackHeight, drawWidth);
  }

  if (selectedIndex >= 0 && selectedIndex < count) {
    drawSelection(renderContext, lane, selectedIndex, tickToX, barY, barHeight, midY);
  }
}

function drawPointMarkers(
  renderContext: RenderContext,
  lane: TimelineDataLane,
  begin: number,
  end: number,
  tickToX: (tick: number) => number,
  midY: number,
  trackHeight: number,
  drawWidth: number
) {
  const { ctx, theme } = renderContext;
  const stemTop = midY - trackHeight * 0.32;
  const stemBottom = midY + trackHeight * 0.32;
  let lastX = -Infinity;
  for (let index = begin; index < end; index += 1) {
    if (lane.kinds[index] !== DATA_LANE_POINT) continue;
    const x = Math.round(tickToX(lane.starts[index]!)) + 0.5;
    if (x < -POINT_SIZE || x > drawWidth + POINT_SIZE) continue;
    const color = lane.colors[index] ?? theme.colors.clip.border;
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, stemTop);
    ctx.lineTo(x, stemBottom);
    ctx.stroke();
    ctx.globalAlpha = 1;
    if (x - lastX < DENSE_POINT_SPACING) continue;
    lastX = x;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x, midY - POINT_SIZE);
    ctx.lineTo(x + POINT_SIZE, midY);
    ctx.lineTo(x, midY + POINT_SIZE);
    ctx.lineTo(x - POINT_SIZE, midY);
    ctx.closePath();
    ctx.fill();
    // Diamonds are 2 * POINT_SIZE wide but only merge below DENSE_POINT_SPACING, so neighbours a
    // few pixels apart overlapped into one blob. A background-colored rim keeps each one distinct.
    ctx.strokeStyle = theme.colors.background;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
}

function drawPointHistogram(
  renderContext: RenderContext,
  lane: TimelineDataLane,
  begin: number,
  end: number,
  tickToX: (tick: number) => number,
  y: number,
  trackHeight: number,
  drawWidth: number
) {
  const { ctx, theme } = renderContext;
  const bucketCount = Math.max(1, Math.ceil(drawWidth / DENSE_POINT_SPACING));
  const counts = new Uint32Array(bucketCount);
  const colors = Array.from<string | undefined>({ length: bucketCount });
  let maxCount = 0;
  for (let index = begin; index < end; index += 1) {
    if (lane.kinds[index] !== DATA_LANE_POINT) continue;
    const x = tickToX(lane.starts[index]!);
    if (x < 0 || x >= drawWidth) continue;
    const bucket = Math.floor(x / DENSE_POINT_SPACING);
    const next = counts[bucket]! + 1;
    counts[bucket] = next;
    colors[bucket] ??= lane.colors[index];
    if (next > maxCount) maxCount = next;
  }
  const scale = Math.log2(maxCount + 1);
  const usable = trackHeight - 6;
  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const value = counts[bucket]!;
    if (value === 0) continue;
    const height = Math.max(2, Math.round((usable * Math.log2(value + 1)) / scale));
    ctx.fillStyle = colors[bucket] ?? theme.colors.clip.border;
    ctx.globalAlpha = 0.8;
    ctx.fillRect(bucket * DENSE_POINT_SPACING, y + trackHeight - 3 - height, DENSE_POINT_SPACING - 1, height);
  }
  ctx.globalAlpha = 1;
}

function drawSelection(
  renderContext: RenderContext,
  lane: TimelineDataLane,
  index: number,
  tickToX: (tick: number) => number,
  barY: number,
  barHeight: number,
  midY: number
) {
  const { ctx, theme } = renderContext;
  ctx.strokeStyle = theme.colors.clip.borderSelected;
  ctx.lineWidth = 2;
  if (lane.kinds[index] === DATA_LANE_INTERVAL) {
    const x0 = tickToX(lane.starts[index]!);
    const x1 = tickToX(lane.ends[index]!);
    ctx.strokeRect(Math.floor(x0) + 1, barY + 1, Math.max(2, Math.floor(x1 - x0) - 2), barHeight - 2);
    return;
  }
  const x = Math.round(tickToX(lane.starts[index]!)) + 0.5;
  const size = POINT_SIZE + 2;
  ctx.fillStyle = lane.colors[index] ?? theme.colors.clip.border;
  ctx.beginPath();
  ctx.moveTo(x, midY - size);
  ctx.lineTo(x + size, midY);
  ctx.lineTo(x, midY + size);
  ctx.lineTo(x - size, midY);
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
}
