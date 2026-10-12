import type { TimelineDataLaneSelection } from '@proj-airi/canvas-timeline-renderer'
import type { TimelineEngine } from '@techsquidtv/canvas-timeline-core'
import type { ComputedRef, Ref, ShallowRef } from 'vue'

import type { ProjectedDataLane } from '../data-tracks/projection'
import type {
  SelectedTimelineDataItem,
  TimelineDataItemRef,
  TimelineDataTrack,
  TimelineDataTrackProvider,
} from '../data-tracks/types'
import type { CommitSegmentEdit, EpisodeDraft } from '../domain'
import type { TimelineLayout } from '../layout'

import { computed, markRaw, onScopeDispose, readonly, shallowRef, watch } from 'vue'

import { createTimelineEngine, toServerTick, toTickTime } from '../core/adapter'
import { dataLaneEntryAt, resolveDataItem } from '../data-tracks/projection'
import { timelineDataTrackProviders } from '../data-tracks/registry'
import { SERVER_TICK_RATE } from '../domain'
import { buildTimelineLayout } from '../layout'
import { useTimelineDataTracks } from './useTimelineDataTracks'

export interface TimelineSession {
  /** Collapsed group ids: session keys hide a session, lane ids hide a player's data tracks. */
  collapsedGroupIds: Readonly<ShallowRef<ReadonlySet<string>>>
  commitEdit: (edit: CommitSegmentEdit) => void
  /** Latest-starting item active at an episode tick among tracks that pass `filter`. */
  dataItemAt: (filter: (track: TimelineDataTrack) => boolean, episodeTick: number) => null | SelectedTimelineDataItem
  /** Projected lanes keyed by data track id (episode ticks). */
  dataLanes: ComputedRef<ReadonlyMap<string, ProjectedDataLane>>
  /** Renderer selection derived from `selectedDataItem`. */
  dataLaneSelection: ComputedRef<null | TimelineDataLaneSelection>
  /** Every described data track with load state. */
  dataTracks: ComputedRef<readonly TimelineDataTrack[]>
  editable: boolean
  engine: ShallowRef<TimelineEngine>
  goToEnd: () => void
  goToStart: () => void
  isPlaying: Readonly<ShallowRef<boolean>>
  /** Visible rows with offsets; the engine and the track headers are built from it. */
  layout: ComputedRef<TimelineLayout>
  pause: () => void
  play: () => void
  playheadTick: Readonly<ShallowRef<number>>
  rebuild: () => void
  reloadDataTrack: (trackId: string) => void
  renderRevision: Readonly<ShallowRef<number>>
  seekByTicks: (deltaTick: number) => void
  seekToTick: (tick: number) => void
  selectDataItem: (item: null | TimelineDataItemRef) => void
  selectedDataItem: ComputedRef<null | SelectedTimelineDataItem>
  selectedSegmentId: Readonly<ShallowRef<null | string>>
  selectSegment: (segmentId: null | string) => void
  setGroupCollapsed: (groupId: string, collapsed: boolean) => void
  /** Shows or folds the empty data tracks of one target (player lane id or session key). */
  toggleEmptyDataTracks: (targetKey: string) => void
  toggleGroup: (groupId: string) => void
  zoomBy: (factor: number) => void
  /** Zooms out until the whole timeline fits the viewport. */
  zoomToFit: () => void
}

export interface TimelineSessionOptions {
  /** Data track providers; defaults to the global registry. */
  providers?: () => readonly TimelineDataTrackProvider[]
}

export function useTimelineSession(
  episode: Ref<EpisodeDraft>,
  commitEdit: (edit: CommitSegmentEdit) => void,
  editable = true,
  options: TimelineSessionOptions = {},
): TimelineSession {
  const selectedSegmentId = shallowRef<null | string>(null)
  const selectedDataItemRef = shallowRef<null | TimelineDataItemRef>(null)
  const collapsedGroupIds = shallowRef<ReadonlySet<string>>(new Set())
  const shownEmptyTargets = shallowRef<ReadonlySet<string>>(new Set())
  const renderRevision = shallowRef(0)
  const isPlaying = shallowRef(false)
  const dataRuntime = useTimelineDataTracks(episode, options.providers ?? (() => timelineDataTrackProviders.value))
  const layout = computed(() => buildTimelineLayout(episode.value, dataRuntime.tracks.value, collapsedGroupIds.value, shownEmptyTargets.value))
  // Data track load-state changes recompute the layout object but not its rows; rebuild the engine
  // only when the row structure changes.
  const layoutKey = computed(() => layout.value.rows.map(row => `${row.id}:${row.height}`).join('|'))
  const engine = shallowRef(markRaw(createTimelineEngine(episode.value, { editable, layout: layout.value })))
  const playheadTick = shallowRef(toServerTick(engine.value.playheadTime))
  let playbackFrame = 0
  let playbackStartTick = 0
  let playbackStartedAt = 0
  let lastDurationTicks = episode.value.durationTicks
  let fitPending = false
  let unsubscribeEvents: (() => void)[] = []

  function stopPlaybackFrame(): void {
    cancelAnimationFrame(playbackFrame)
    playbackFrame = 0
  }

  function bindEngineEvents(): void {
    unsubscribeEvents.forEach(unsubscribe => unsubscribe())
    unsubscribeEvents = [
      engine.value.on('render', requestRender),
      engine.value.on('playhead:scrub', (time) => {
        playheadTick.value = toServerTick(time)
        requestRender()
      }),
      engine.value.on('playback:state', (playing) => {
        isPlaying.value = playing
        requestRender()
      }),
      engine.value.on('viewport:resize', ({ viewportWidth }) => {
        if (fitPending && viewportWidth)
          zoomToFit()
      }),
    ]
  }

  function requestRender(): void {
    renderRevision.value += 1
  }

  function rebuild(): void {
    stopPlaybackFrame()
    const previous = engine.value
    previous.pause()
    engine.value = markRaw(createTimelineEngine(episode.value, { editable, layout: layout.value, previous, selectedSegmentId: selectedSegmentId.value }))
    playheadTick.value = toServerTick(engine.value.playheadTime)
    bindEngineEvents()
    requestRender()

    // The first content on an empty timeline opens fitted, so every clip and marker is on screen.
    // Later rebuilds keep the user's zoom. The canvas mounts with that first content and measures
    // itself in a ResizeObserver callback, so fitting now would use the engine's 1000px default
    // width; wait for the measured width instead.
    const durationTicks = episode.value.durationTicks
    if (lastDurationTicks === 0 && durationTicks > 0) {
      fitPending = true
      if (engine.value.getState().viewportWidth)
        zoomToFit()
    }
    lastDurationTicks = durationTicks
  }

  function selectSegment(segmentId: null | string): void {
    selectedSegmentId.value = segmentId
    engine.value.selectClip(segmentId)
  }

  function play(): void {
    if (isPlaying.value)
      return

    const durationTick = episode.value.durationTicks
    playbackStartTick = playheadTick.value >= durationTick ? 0 : playheadTick.value
    if (playbackStartTick !== playheadTick.value)
      engine.value.updatePlayhead(toTickTime(playbackStartTick))

    // NOTICE: The upstream internal clock converts each ~16ms animation-frame delta separately
    // to the playhead's 20Hz rate. Since `fromSeconds` rounds each delta to the nearest tick, every
    // frame becomes zero and the fractional time is discarded. The relevant paths are
    // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/core/src/playback.ts#L46-L58`
    // and
    // `https://github.com/techsquidtv/canvas-timeline/blob/1536a2dbc54e3a333ace360894a2e4508b295cf1/packages/utils/src/time.ts#L44-L56`.
    // Use core's external-clock mode and derive ticks from total elapsed time so sub-tick frame
    // deltas accumulate instead of being lost.
    engine.value.play({ clock: 'external' })
    playbackStartedAt = performance.now()
    playbackFrame = requestAnimationFrame(updatePlayback)
  }

  function pause(): void {
    stopPlaybackFrame()
    engine.value.pause()
  }

  function updatePlayback(time: number): void {
    if (!isPlaying.value)
      return stopPlaybackFrame()

    const durationTick = episode.value.durationTicks
    const elapsedTick = Math.floor((time - playbackStartedAt) * SERVER_TICK_RATE / 1_000)
    const nextTick = Math.min(durationTick, playbackStartTick + elapsedTick)
    if (nextTick !== playheadTick.value) {
      engine.value.updatePlayhead(toTickTime(nextTick))
      revealTick(nextTick, 0.1)
    }

    if (nextTick >= durationTick) {
      pause()
      return
    }
    playbackFrame = requestAnimationFrame(updatePlayback)
  }

  function goToEnd(): void {
    pause()
    engine.value.updatePlayhead(toTickTime(episode.value.durationTicks))
    revealTick(episode.value.durationTicks)
  }

  function goToStart(): void {
    pause()
    engine.value.updatePlayhead(toTickTime(0))
    revealTick(0)
  }

  function seekByTicks(deltaTick: number): void {
    if (isPlaying.value)
      pause()

    seekToTick(playheadTick.value + deltaTick)
  }

  function seekToTick(tick: number): void {
    const nextTick = Math.min(episode.value.durationTicks, Math.max(0, Math.round(tick)))
    if (isPlaying.value) {
      // A manual seek changes the origin of the external playback clock. Without rebasing both
      // values, the next animation frame would snap the playhead back to the pre-seek position.
      playbackStartTick = nextTick
      playbackStartedAt = performance.now()
    }
    engine.value.updatePlayhead(toTickTime(nextTick))
    revealTick(nextTick)
  }

  /**
   * Scrolls the timeline so `tick` is on screen. Seeks from other panels (event log, evidence)
   * otherwise moved the playhead off screen and left the timeline showing an unrelated range.
   *
   * `anchor` is where the tick lands as a fraction of the viewport width: a third for jumps, so
   * some context before it stays visible, and near the left edge for playback paging.
   */
  function revealTick(tick: number, anchor = 1 / 3): void {
    const current = engine.value
    const width = current.getState().viewportWidth
    if (!width)
      return
    const x = tick / SERVER_TICK_RATE * current.zoomScale
    if (x >= current.scrollLeft && x <= current.scrollLeft + width)
      return
    current.setScrollLeft(Math.max(0, x - width * anchor))
  }

  function zoomBy(factor: number): void {
    engine.value.setZoomScale(engine.value.zoomScale * factor)
  }

  function zoomToFit(): void {
    fitPending = false
    engine.value.setZoomScale(engine.value.minZoomScale)
    engine.value.setScrollLeft(0)
  }

  function setGroupCollapsed(groupId: string, collapsed: boolean): void {
    if (collapsedGroupIds.value.has(groupId) === collapsed)
      return
    const next = new Set(collapsedGroupIds.value)
    if (collapsed)
      next.add(groupId)
    else
      next.delete(groupId)
    collapsedGroupIds.value = next
  }

  function toggleGroup(groupId: string): void {
    setGroupCollapsed(groupId, !collapsedGroupIds.value.has(groupId))
  }

  function toggleEmptyDataTracks(targetKey: string): void {
    const next = new Set(shownEmptyTargets.value)
    if (!next.delete(targetKey))
      next.add(targetKey)
    shownEmptyTargets.value = next
  }

  const tracksById = computed(() => new Map(dataRuntime.tracks.value.map(track => [track.id, track])))

  const selectedDataItem = computed(() => {
    const ref = selectedDataItemRef.value
    const track = ref ? tracksById.value.get(ref.trackId) : undefined
    const item = track?.items.find(candidate => candidate.id === ref?.itemId)
    if (!track || !item)
      return null
    return resolveDataItem(episode.value, track, dataRuntime.lanes.value.get(track.id), item)
  })

  const dataLaneSelection = computed<null | TimelineDataLaneSelection>(() => {
    const selected = selectedDataItem.value
    const lane = selected ? dataRuntime.lanes.value.get(selected.track.id) : undefined
    if (!selected || !lane)
      return null
    const itemIndex = selected.track.items.indexOf(selected.item)
    const index = lane.itemIndexes.indexOf(itemIndex)
    return index >= 0 ? { index, trackId: selected.track.id } : null
  })

  function selectDataItem(item: null | TimelineDataItemRef): void {
    selectedDataItemRef.value = item
    requestRender()
  }

  function dataItemAt(filter: (track: TimelineDataTrack) => boolean, episodeTick: number): null | SelectedTimelineDataItem {
    let best: null | { entry: number, start: number, track: TimelineDataTrack } = null
    for (const track of dataRuntime.tracks.value) {
      if (!filter(track))
        continue
      const lane = dataRuntime.lanes.value.get(track.id)
      if (!lane)
        continue
      const entry = dataLaneEntryAt(lane, episodeTick)
      if (entry >= 0 && (!best || lane.starts[entry]! >= best.start))
        best = { entry, start: lane.starts[entry]!, track }
    }
    if (!best)
      return null
    const lane = dataRuntime.lanes.value.get(best.track.id)!
    const item = best.track.items[lane.itemIndexes[best.entry]!]!
    return resolveDataItem(episode.value, best.track, lane, item, best.entry)
  }

  bindEngineEvents()
  watch([episode, layoutKey], rebuild)
  watch(layout, (current) => {
    dataRuntime.ensureLoaded(current.rows.flatMap(row => row.role === 'data' ? [row.id] : []))
  }, { immediate: true })
  onScopeDispose(() => {
    pause()
    unsubscribeEvents.forEach(unsubscribe => unsubscribe())
  })

  return {
    collapsedGroupIds: readonly(collapsedGroupIds),
    commitEdit,
    dataItemAt,
    dataLanes: dataRuntime.lanes,
    dataLaneSelection,
    dataTracks: dataRuntime.tracks,
    editable,
    engine,
    goToEnd,
    goToStart,
    isPlaying: readonly(isPlaying),
    layout,
    pause,
    play,
    playheadTick: readonly(playheadTick),
    rebuild,
    reloadDataTrack: dataRuntime.reload,
    renderRevision: readonly(renderRevision),
    seekByTicks,
    seekToTick,
    selectDataItem,
    selectedDataItem,
    selectedSegmentId: readonly(selectedSegmentId),
    selectSegment,
    setGroupCollapsed,
    toggleEmptyDataTracks,
    toggleGroup,
    zoomBy,
    zoomToFit,
  }
}
