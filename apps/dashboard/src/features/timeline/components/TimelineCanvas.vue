<script setup lang="ts">
import type { TimelineDataLaneSelection } from '@proj-airi/canvas-timeline-renderer'
import type { ClipHitRegion, ClipHitTestResult, TimelineEngine, VisibleTimelineClip } from '@techsquidtv/canvas-timeline-core'

import type { ProjectedDataLane } from '../data-tracks/projection'
import type { TimelineDataItem, TimelineDataItemRef } from '../data-tracks/types'
import type { CommitSegmentEdit } from '../domain'
import type { TimelineLayout } from '../layout'

import { defaultTimelineRendererTheme } from '@proj-airi/canvas-timeline-renderer'
import { CanvasRenderer } from '@proj-airi/canvas-timeline-vue'
import { computed, onBeforeUnmount, onMounted, shallowRef, useTemplateRef, watch } from 'vue'

import { readSegmentEdit, toServerTick, toTickTime } from '../core/adapter'
import { hitTestDataLane } from '../data-tracks/projection'
import { SERVER_TICK_RATE } from '../domain'
import { rowAtOffset } from '../layout'

const props = defineProps<{
  dataLanes: ReadonlyMap<string, ProjectedDataLane>
  dataLaneSelection: null | TimelineDataLaneSelection
  editable: boolean
  engine: TimelineEngine
  layout: TimelineLayout
  renderRevision: number
  scrollTop: number
  selectedSegmentId: string | null
}>()

const emit = defineEmits<{
  cancelEdit: []
  commitEdit: [edit: CommitSegmentEdit]
  seek: [tick: number]
  selectDataItem: [item: TimelineDataItemRef | null]
  selectSegment: [segmentId: string | null]
}>()

const DATA_HIT_TOLERANCE_PX = 5
const laneList = computed(() => [...props.dataLanes.values()])
const hoveredDataItem = shallowRef<null | { item: TimelineDataItem, x: number, y: number }>(null)

const RULER_HEIGHT = defaultTimelineRendererTheme.metrics.rulerHeight
const TRACK_HEIGHT = defaultTimelineRendererTheme.metrics.trackHeight

interface DataHit {
  item: TimelineDataItem
  ref: TimelineDataItemRef
}

/** Data item under a canvas point, using the same row offsets and lane entries as the renderer. */
function hitDataItem(point: { x: number, y: number }): DataHit | null {
  if (point.y <= RULER_HEIGHT)
    return null
  const row = rowAtOffset(props.layout, point.y - RULER_HEIGHT + props.engine.scrollTop)
  if (row?.role !== 'data')
    return null
  const lane = props.dataLanes.get(row.id)
  if (!lane)
    return null
  const ticksPerPixel = SERVER_TICK_RATE / props.engine.zoomScale
  const tick = (props.engine.scrollLeft + point.x) * ticksPerPixel
  const index = hitTestDataLane(lane, tick, DATA_HIT_TOLERANCE_PX * ticksPerPixel)
  const item = index >= 0 ? row.dataTrack.items[lane.itemIndexes[index]!] : undefined
  return item ? { item, ref: { itemId: item.id, trackId: row.id } } : null
}
const canvas = useTemplateRef<HTMLCanvasElement>('canvas')
const hoveredClipId = shallowRef<null | string>(null)
const hoveredRegion = shallowRef<ClipHitRegion>('body')
const pointerCursor = shallowRef('default')
let resizeObserver: ResizeObserver | undefined
let animationFrame = 0

interface Gesture {
  clipId?: string
  edge?: 'end' | 'start'
  mode: 'move' | 'scrub' | 'trim'
  originalEndTick?: number
  originalStartTick?: number
  pointerId: number
  startPointerTick: number
}

let gesture: Gesture | null = null

function canvasPoint(event: PointerEvent): { x: number, y: number } {
  const bounds = canvas.value?.getBoundingClientRect()
  return {
    x: event.clientX - (bounds?.left ?? 0),
    y: event.clientY - (bounds?.top ?? 0),
  }
}

function scheduleDraw(): void {
  cancelAnimationFrame(animationFrame)
  animationFrame = requestAnimationFrame(draw)
}

function syncViewport(): void {
  const element = canvas.value
  if (!element)
    return

  props.engine.setViewportWidth(element.clientWidth)
  props.engine.setViewportHeight(element.clientHeight)
}

function syncScrollTop(): void {
  if (props.engine.scrollTop !== props.scrollTop)
    props.engine.setScrollTop(props.scrollTop)
}

function draw(): void {
  const element = canvas.value
  if (!element)
    return

  const context = element.getContext('2d')
  if (!context)
    return

  const width = element.clientWidth
  const height = element.clientHeight
  const ratio = window.devicePixelRatio || 1
  if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
    element.width = Math.round(width * ratio)
    element.height = Math.round(height * ratio)
  }

  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)
  if (props.editable)
    drawInteractionOverlay(context, width, height)
  drawPlayhead(context, height)
}

function drawInteractionOverlay(context: CanvasRenderingContext2D, width: number, height: number): void {
  if (!props.selectedSegmentId && !hoveredClipId.value)
    return

  const visibleClips = props.engine.getVisibleTimelineClips({
    overscanPixels: 80,
    rulerHeight: RULER_HEIGHT,
    trackHeight: TRACK_HEIGHT,
    viewportHeight: height,
    viewportWidth: width,
  })

  for (const entry of visibleClips) {
    const selected = entry.clip.id === props.selectedSegmentId
    if (selected || entry.clip.id === hoveredClipId.value)
      drawTrimHandles(context, entry, selected)
  }
}

function drawTrimHandles(context: CanvasRenderingContext2D, entry: VisibleTimelineClip, selected: boolean): void {
  const rect = entry.rect
  const handleWidth = 6
  const radius = Math.min(6, rect.width / 2, rect.height / 2)
  const color = selected
    ? defaultTimelineRendererTheme.colors.clip.borderSelected
    : 'rgba(255, 255, 255, 0)'

  context.save()

  // NOTICE: The upstream React adapter exposes two 12px DOM trim handles and an ew-resize cursor.
  // This Canvas affordance follows `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/react/src/components/interactions/ClipInteractionLayer.tsx#L471-L531`.
  context.beginPath()
  context.roundRect(rect.x, rect.y, Math.max(1, rect.width), rect.height, radius)
  context.clip()

  context.fillStyle = color
  if (rect.x >= -handleWidth) {
    context.globalAlpha = hoveredClipId.value === entry.clip.id && hoveredRegion.value === 'start-edge' ? 1 : 0.75
    context.fillRect(rect.x, rect.y, handleWidth, rect.height)
  }
  if (rect.x + rect.width <= context.canvas.clientWidth + handleWidth) {
    context.globalAlpha = hoveredClipId.value === entry.clip.id && hoveredRegion.value === 'end-edge' ? 1 : 0.75
    context.fillRect(rect.x + rect.width - handleWidth, rect.y, handleWidth, rect.height)
  }
  context.restore()
}

function drawPlayhead(context: CanvasRenderingContext2D, height: number): void {
  const x = props.engine.timeToPixel(props.engine.playheadTime)
  context.fillStyle = '#ffb45c'
  context.beginPath()
  context.moveTo(x - 5, 0)
  context.lineTo(x + 5, 0)
  context.lineTo(x, 8)
  context.closePath()
  context.fill()
  context.strokeStyle = '#ffb45c'
  context.beginPath()
  context.moveTo(Math.round(x) + 0.5, 0)
  context.lineTo(Math.round(x) + 0.5, height)
  context.stroke()
}

function pointerTick(x: number): number {
  return toServerTick(props.engine.pixelToTime(x, SERVER_TICK_RATE))
}

function hitAtPoint(point: { x: number, y: number }, pointerType?: string): ClipHitTestResult | null {
  return props.engine.getClipAtPoint({
    pointerType,
    rulerHeight: RULER_HEIGHT,
    trackHeight: TRACK_HEIGHT,
    x: point.x,
    y: point.y,
  })
}

function updateHover(event: PointerEvent): void {
  const point = canvasPoint(event)
  if (point.y <= RULER_HEIGHT) {
    hoveredClipId.value = null
    hoveredDataItem.value = null
    pointerCursor.value = 'col-resize'
    scheduleDraw()
    return
  }

  const hit = hitAtPoint(point, event.pointerType)
  hoveredClipId.value = hit?.clip.id ?? null
  hoveredRegion.value = hit?.region ?? 'body'
  const dataHit = hit ? null : hitDataItem(point)
  hoveredDataItem.value = dataHit ? { item: dataHit.item, x: point.x, y: point.y } : null
  pointerCursor.value = hit?.canMove || hit?.canTrim ? (hit.region === 'body' ? 'grab' : 'ew-resize') : dataHit ? 'pointer' : 'default'
  scheduleDraw()
}

function onPointerDown(event: PointerEvent): void {
  if (event.button !== 0)
    return

  const element = canvas.value
  if (!element)
    return

  const point = canvasPoint(event)
  element.setPointerCapture(event.pointerId)

  if (point.y <= RULER_HEIGHT) {
    gesture = { mode: 'scrub', pointerId: event.pointerId, startPointerTick: pointerTick(point.x) }
    pointerCursor.value = 'col-resize'
    emit('seek', pointerTick(point.x))
    return
  }

  // NOTICE: One delegated overlay hit-tests through core instead of creating one DOM node per clip.
  // This follows `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/react/src/components/interactions/ClipInteractionLayer.tsx#L105-L258`.
  const hit = hitAtPoint(point, event.pointerType)

  if (!hit) {
    const dataHit = hitDataItem(point)
    if (dataHit) {
      emit('selectDataItem', dataHit.ref)
      return
    }
    emit('selectSegment', null)
    emit('selectDataItem', null)
    gesture = { mode: 'scrub', pointerId: event.pointerId, startPointerTick: pointerTick(point.x) }
    pointerCursor.value = 'col-resize'
    emit('seek', pointerTick(point.x))
    return
  }

  emit('selectSegment', hit.clip.id)
  const cannotEditBody = hit.region === 'body' && !hit.canMove
  const cannotEditEdge = hit.region !== 'body' && !hit.canTrim
  if (cannotEditBody || cannotEditEdge)
    return

  props.engine.startDrag()
  props.engine.prepareSnapping(hit.clip.id)
  gesture = {
    clipId: hit.clip.id,
    edge: hit.region === 'body' ? undefined : hit.region === 'start-edge' ? 'start' : 'end',
    mode: hit.region === 'body' ? 'move' : 'trim',
    originalEndTick: toServerTick(hit.clip.timelineEnd),
    originalStartTick: toServerTick(hit.clip.timelineStart),
    pointerId: event.pointerId,
    startPointerTick: pointerTick(point.x),
  }
  pointerCursor.value = hit.region === 'body' ? 'grabbing' : 'ew-resize'
}

function onPointerMove(event: PointerEvent): void {
  if (!gesture) {
    updateHover(event)
    return
  }
  if (gesture.pointerId !== event.pointerId)
    return

  const point = canvasPoint(event)
  const currentTick = pointerTick(point.x)
  if (gesture.mode === 'scrub') {
    emit('seek', currentTick)
    return
  }

  const deltaTick = currentTick - gesture.startPointerTick
  if (!gesture.clipId || gesture.originalStartTick === undefined || gesture.originalEndTick === undefined)
    return

  if (gesture.mode === 'move') {
    props.engine.moveClip({
      clipId: gesture.clipId,
      startTime: toTickTime(gesture.originalStartTick + deltaTick),
    })
    return
  }

  const boundary = gesture.edge === 'start' ? gesture.originalStartTick : gesture.originalEndTick
  props.engine.trimClip(gesture.clipId, gesture.edge ?? 'end', toTickTime(boundary + deltaTick))
}

function onPointerUp(event: PointerEvent): void {
  if (!gesture || gesture.pointerId !== event.pointerId)
    return

  const completedGesture = gesture
  gesture = null
  if (completedGesture.mode === 'scrub') {
    updateHover(event)
    return
  }

  props.engine.endDrag()
  const edit = completedGesture.clipId ? readSegmentEdit(props.engine, completedGesture.clipId) : null
  if (edit)
    emit('commitEdit', edit)
  updateHover(event)
}

function onPointerCancel(event: PointerEvent): void {
  if (!gesture || gesture.pointerId !== event.pointerId)
    return

  gesture = null
  props.engine.endDrag()
  pointerCursor.value = 'default'
  emit('cancelEdit')
}

function onPointerLeave(): void {
  if (gesture)
    return

  hoveredClipId.value = null
  hoveredDataItem.value = null
  pointerCursor.value = 'default'
  scheduleDraw()
}

function onWheel(event: WheelEvent): void {
  const engine = props.engine

  // NOTICE: High-resolution trackpads can deliver wheel events faster than the renderer's frame
  // cadence. Every call below synchronously updates core, emits `render`, and makes the renderer
  // bridge structured-clone a complete `TimelineState`. The upstream React renderer has the same
  // full-state bridge at
  // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/CanvasRenderer.tsx#L405-L418`.
  //
  // The worker keeps only one pending draw per animation frame, but every `UPDATE_STATE` message is
  // still received before that draw is coalesced; see
  // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/worker.ts#L84-L90`
  // and
  // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/renderer/src/worker.ts#L119-L147`.
  // Local profiling with this small fixture found inexpensive draw passes but more state messages
  // than rendered frames, so input/message/frame pacing is the leading explanation for the slight
  // pan lag. This is an inference, not evidence that drawing will remain cheap for large captures.
  //
  // TODO: Add a repeatable high-frequency wheel regression, then accumulate wheel deltas and apply
  // pan/zoom at most once per main-thread animation frame. If large captures still lag, evaluate an
  // `UPDATE_VIEWPORT` worker message carrying only scroll/zoom fields before changing render logic.
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault()
    const point = canvas.value?.getBoundingClientRect()
    const anchorX = event.clientX - (point?.left ?? 0)
    const anchorSecond = (engine.scrollLeft + anchorX) / engine.zoomScale
    engine.setZoomScale(engine.zoomScale * Math.exp(-event.deltaY * 0.002))
    engine.setScrollLeft(anchorSecond * engine.zoomScale - anchorX)
    return
  }

  const horizontalDelta = event.shiftKey ? event.deltaY : event.deltaX
  if (horizontalDelta !== 0 && (event.shiftKey || Math.abs(horizontalDelta) >= Math.abs(event.deltaY))) {
    event.preventDefault()
    engine.setScrollLeft(engine.scrollLeft + horizontalDelta)
  }
}

onMounted(() => {
  const element = canvas.value
  if (!element)
    return

  resizeObserver = new ResizeObserver(([entry]) => {
    if (!entry)
      return
    syncViewport()
    scheduleDraw()
  })
  resizeObserver.observe(element)
  syncScrollTop()
  scheduleDraw()
})

watch(() => props.renderRevision, scheduleDraw)
watch(() => props.scrollTop, syncScrollTop)
watch(() => props.engine, () => {
  syncViewport()
  syncScrollTop()
  scheduleDraw()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame)
  resizeObserver?.disconnect()
})
</script>

<template>
  <div class="relative block h-full min-w-0 w-full">
    <CanvasRenderer
      :data-lanes="laneList"
      :data-selection="dataLaneSelection"
      :engine="engine"
      :ruler="{ format: 'seconds' }"
      :theme="{ metrics: { clipRadius: 6 } }"
    />
    <canvas
      ref="canvas"
      :aria-label="editable
        ? 'Video editing timeline. Drag clips between compatible tracks, or drag clip edges to trim and extend.'
        : 'Dataset timeline. Scroll to inspect synchronized replay clips and drag the ruler to seek.'"
      class="absolute inset-0 block h-full w-full touch-none"
      role="application"
      :style="{ cursor: pointerCursor }"
      @pointercancel="onPointerCancel"
      @pointerdown="onPointerDown"
      @pointerleave="onPointerLeave"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @wheel="onWheel"
    />
    <div
      v-if="hoveredDataItem"
      class="pointer-events-none absolute z-10 max-w-72 rounded bg-black/85 px-2 py-1 text-[11px] text-neutral-100 shadow-lg"
      data-testid="timeline-data-tooltip"
      role="tooltip"
      :style="{ left: `${hoveredDataItem.x + 12}px`, top: `${hoveredDataItem.y + 12}px` }"
    >
      <p class="m-0 whitespace-pre-line">
        {{ hoveredDataItem.item.tooltip ?? hoveredDataItem.item.label ?? hoveredDataItem.item.id }}
      </p>
      <p v-if="hoveredDataItem.item.category" class="m-0 mt-0.5 text-[10px] text-neutral-400">
        {{ hoveredDataItem.item.category }}
      </p>
    </div>
  </div>
</template>
