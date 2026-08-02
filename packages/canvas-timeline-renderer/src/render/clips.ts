/* SPDX-License-Identifier: MPL-2.0 */
// NOTICE: Clip geometry and keyframe rendering are ported from
// `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/render/clips.ts#L1-L138`.
// Recorder intentionally replaces the upstream filled body and vertically centered label with an
// outlined body and a compact label badge so dense video tracks remain easier to scan.

import type { Clip, TimelineKeyframeRenderClip } from '@techsquidtv/canvas-timeline-core';
import { timeToX } from '#renderer/render/geometry';
import type { RenderContext } from '#renderer/render/types';

export type ClipRenderContext = RenderContext & {
  clip: Clip;
  y: number;
  trackHeight: number;
  muted: boolean;
  visible: boolean;
};

export function drawClip(renderContext: ClipRenderContext) {
  const { ctx, clip, y, trackHeight, muted, visible, width, theme } = renderContext;
  const startX = timeToX(renderContext, clip.timelineStart);
  const endX = timeToX(renderContext, clip.timelineEnd);
  const clipWidth = endX - startX;
  const clipInsetY = Math.min(theme.metrics.clipInsetY, Math.max(0, (trackHeight - 1) / 2));
  const clipY = y + clipInsetY;
  const clipHeight = Math.max(1, trackHeight - clipInsetY * 2);
  const clipRadius = Math.min(theme.metrics.clipRadius, clipHeight / 2, Math.max(clipWidth, 1) / 2);

  if (endX < 0 || startX > width) {
    return;
  }

  const globalOpacity = !visible || muted || clip.disabled ? 0.35 : (clip.opacity ?? 1);

  ctx.globalAlpha = globalOpacity;

  const accentColor = clip.selected
    ? theme.colors.clip.borderSelected
    : clip.color || theme.colors.clip.border;
  ctx.fillStyle = accentColor;
  ctx.globalAlpha = globalOpacity * Math.min(1, theme.metrics.clipFillOpacity);

  ctx.beginPath();
  ctx.roundRect(startX, clipY, Math.max(clipWidth, 1), clipHeight, clipRadius);
  ctx.fill();

  ctx.globalAlpha = globalOpacity;
  ctx.strokeStyle = accentColor;
  ctx.lineWidth = clip.selected ? 2 : 1;
  ctx.stroke();

  ctx.globalAlpha = 1;

  ctx.save();
  ctx.beginPath();
  ctx.roundRect(startX, clipY, Math.max(clipWidth, 1), clipHeight, clipRadius);
  ctx.clip();

  if (renderContext.options.showKeyframes) {
    drawClipKeyframes(renderContext);
  }

  if (renderContext.options.showClipLabels && clipWidth > 20) {
    drawClipLabel(renderContext, {
      accentColor,
      clipHeight,
      clipRadius,
      clipWidth,
      clipY,
      startX,
    });
  }

  ctx.restore();
}

interface ClipLabelGeometry {
  accentColor: string;
  clipHeight: number;
  clipRadius: number;
  clipWidth: number;
  clipY: number;
  startX: number;
}

function drawClipLabel(renderContext: ClipRenderContext, geometry: ClipLabelGeometry) {
  const { ctx, clip, theme } = renderContext;
  const { accentColor, clipHeight, clipRadius, clipWidth, clipY, startX } = geometry;
  const clipName = String(clip.label ?? 'Clip');

  ctx.font = theme.fonts.clip;
  const textMetrics = ctx.measureText(clipName);
  // Canvas font strings do not expose a reliable line-height. Actual glyph bounds keep the badge
  // compact across custom fonts; the fallback matches the renderer's default 12px clip font.
  const textHeight = Math.ceil(
    (textMetrics.actualBoundingBoxAscent || 9) + (textMetrics.actualBoundingBoxDescent || 3)
  );
  const labelInset = Math.min(theme.metrics.clipLabelInset, clipHeight / 2);
  const availableHeight = Math.max(0, clipHeight - labelInset * 2);
  const availableWidth = Math.max(0, clipWidth - labelInset * 2);
  const labelHeight = Math.min(
    availableHeight,
    textHeight + theme.metrics.clipLabelPaddingY * 2
  );
  const labelWidth = availableWidth;
  if (labelHeight <= 0 || labelWidth <= 0) {
    return;
  }

  const labelX = startX + labelInset;
  const labelY = clipY + labelInset;
  const labelRadius = Math.min(clipRadius, labelHeight / 2, labelWidth / 2);
  ctx.fillStyle = accentColor;
  ctx.globalAlpha = Math.min(1, theme.metrics.clipLabelOpacity);
  ctx.beginPath();
  // The top corners follow the clip silhouette while square lower corners make this a full-width
  // title band rather than a floating badge.
  ctx.roundRect(labelX, labelY, labelWidth, labelHeight, [labelRadius, labelRadius, 0, 0]);
  ctx.fill();
  ctx.globalAlpha = 1;

  const textWidth = Math.max(0, labelWidth - theme.metrics.clipLabelPaddingX * 2);
  if (textWidth <= 0) {
    return;
  }

  ctx.fillStyle = clip.selected ? theme.colors.clip.textSelected : theme.colors.clip.text;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';
  ctx.fillText(
    clipName,
    labelX + theme.metrics.clipLabelPaddingX,
    labelY + labelHeight / 2,
    textWidth
  );
}

function drawClipKeyframes(renderContext: ClipRenderContext) {
  const { ctx, clip, theme } = renderContext;
  const clipGeometry = renderContext.keyframeGeometryByClip?.get(clip.id);
  if (
    clipGeometry === undefined ||
    (clipGeometry.points.length === 0 && clipGeometry.segments.length === 0)
  ) {
    return;
  }

  const handleSize = 6;
  drawPreparedKeyframeSegments(renderContext, clipGeometry);

  for (const keyframe of clipGeometry.points) {
    const point = keyframe.point;
    ctx.save();
    ctx.translate(point.x, point.y);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = keyframe.selected
      ? theme.colors.keyframe.fillSelected
      : theme.colors.keyframe.fill;
    ctx.strokeStyle = keyframe.selected
      ? theme.colors.keyframe.strokeSelected
      : theme.colors.keyframe.stroke;
    ctx.lineWidth = 1.5;
    ctx.fillRect(-handleSize / 2, -handleSize / 2, handleSize, handleSize);
    ctx.strokeRect(-handleSize / 2, -handleSize / 2, handleSize, handleSize);
    ctx.restore();
  }
}

function drawPreparedKeyframeSegments(
  renderContext: ClipRenderContext,
  clipGeometry: TimelineKeyframeRenderClip
) {
  const { ctx, theme } = renderContext;
  if (clipGeometry.segments.length === 0) {
    return;
  }

  ctx.save();
  ctx.beginPath();
  for (const segment of clipGeometry.segments) {
    ctx.moveTo(segment.startPoint.x, segment.startPoint.y);
    if (segment.interpolation === 'hold') {
      ctx.lineTo(segment.endPoint.x, segment.startPoint.y);
      ctx.lineTo(segment.endPoint.x, segment.endPoint.y);
    } else if (
      segment.interpolation === 'bezier' &&
      segment.controlPoint1 !== undefined &&
      segment.controlPoint2 !== undefined
    ) {
      ctx.bezierCurveTo(
        segment.controlPoint1.x,
        segment.controlPoint1.y,
        segment.controlPoint2.x,
        segment.controlPoint2.y,
        segment.endPoint.x,
        segment.endPoint.y
      );
    } else {
      ctx.lineTo(segment.endPoint.x, segment.endPoint.y);
    }
  }
  ctx.strokeStyle = theme.colors.keyframe.line;
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.restore();
}
