import type { Draggable } from 'animejs/draggable'
import type { ShallowRef } from 'vue'

import { animate } from 'animejs/animation'
import { createDraggable } from 'animejs/draggable'
import { nextTick, onMounted, onScopeDispose, readonly, shallowRef, watch } from 'vue'

interface TrackSortable {
  activeIndex: Readonly<ShallowRef<null | number>>
}

const AUTO_SCROLL_ACCELERATION = 1_000
const AUTO_SCROLL_DECELERATION = 2_000
const AUTO_SCROLL_MAX_SPEED = 500
const AUTO_SCROLL_THRESHOLD = 50

interface UseTrackSortableOptions {
  container: Readonly<ShallowRef<HTMLElement | null>>
  enabled?: () => boolean
  itemIds: () => string[]
  onReorder: (sourceIndex: number, targetIndex: number) => void
  rowHeight: number
  scrollContainer: () => HTMLElement | null
  scrollPaddingTop: number
}

export function useTrackSortable(options: UseTrackSortableOptions): TrackSortable {
  const activeIndex = shallowRef<null | number>(null)
  const draggables = new Map<string, Draggable>()
  let activeDraggable: Draggable | null = null
  let activeTrackId: null | string = null
  let autoScrollFrame = 0
  let autoScrollSpeed = 0
  let dragSourceIndex = -1
  let lastAutoScrollTime = 0
  let pointerClientY = 0
  let mutationObserver: MutationObserver | null = null
  let pendingReorder: [sourceIndex: number, targetIndex: number] | null = null

  function moveTowards(value: number, target: number, maxDelta: number): number {
    if (value < target)
      return Math.min(value + maxDelta, target)
    return Math.max(value - maxDelta, target)
  }

  function moveMountedRows(sourceIndex: number, targetIndex: number, animateRows: boolean): void {
    const order = options.itemIds()
    draggables.forEach((draggable, id) => {
      if (draggable === activeDraggable)
        return

      const index = order.indexOf(id)
      let y = 0
      if (sourceIndex < targetIndex && index > sourceIndex && index <= targetIndex)
        y = -options.rowHeight
      else if (sourceIndex > targetIndex && index >= targetIndex && index < sourceIndex)
        y = options.rowHeight

      if (animateRows) {
        animate(draggable, {
          duration: 350,
          ease: 'out(4)',
          y,
        })
      }
      else {
        draggable.y = y
      }
    })
  }

  function resetMountedRows(animateRows: boolean): void {
    moveMountedRows(-1, -1, animateRows)
    if (activeDraggable)
      activeDraggable.y = 0
  }

  function updatePendingReorder(draggable: Draggable): void {
    // Animating displaced Draggable instances also updates their `y` values. Only the row
    // currently held by the pointer may mutate visual order or publish a store reorder.
    if (draggable !== activeDraggable)
      return

    const order = options.itemIds()
    const sourceIndex = activeTrackId === null ? -1 : order.indexOf(activeTrackId)
    if (sourceIndex < 0)
      return

    const targetIndex = Math.max(0, Math.min(
      order.length - 1,
      sourceIndex + Math.round(draggable.destY / options.rowHeight),
    ))
    if (sourceIndex === targetIndex) {
      pendingReorder = null
      moveMountedRows(sourceIndex, targetIndex, true)
      return
    }

    pendingReorder = [dragSourceIndex, targetIndex]
    moveMountedRows(sourceIndex, targetIndex, true)
  }

  function stopAutoScroll(): void {
    cancelAnimationFrame(autoScrollFrame)
    autoScrollFrame = 0
    autoScrollSpeed = 0
    lastAutoScrollTime = 0
  }

  function updateAutoScroll(time: number): void {
    const draggable = activeDraggable
    const scrollContainer = options.scrollContainer()
    if (!draggable || !scrollContainer)
      return stopAutoScroll()

    const rect = scrollContainer.getBoundingClientRect()
    const top = rect.top + options.scrollPaddingTop
    const threshold = Math.min(AUTO_SCROLL_THRESHOLD, (rect.bottom - top) / 2)
    let targetSpeed = 0
    if (pointerClientY < top + threshold)
      targetSpeed = -AUTO_SCROLL_MAX_SPEED * (1 - Math.max(0, pointerClientY - top) / threshold)
    else if (pointerClientY > rect.bottom - threshold)
      targetSpeed = AUTO_SCROLL_MAX_SPEED * (1 - Math.max(0, rect.bottom - pointerClientY) / threshold)

    // Clamp long frames so returning from a background tab cannot jump across many tracks.
    const elapsedSeconds = lastAutoScrollTime === 0 ? 0 : Math.min(time - lastAutoScrollTime, 32) / 1_000
    lastAutoScrollTime = time
    const isAccelerating = Math.sign(autoScrollSpeed) === Math.sign(targetSpeed)
      && Math.abs(targetSpeed) > Math.abs(autoScrollSpeed)
    const speedChange = (isAccelerating ? AUTO_SCROLL_ACCELERATION : AUTO_SCROLL_DECELERATION) * elapsedSeconds
    autoScrollSpeed = moveTowards(autoScrollSpeed, targetSpeed, speedChange)

    const previousScrollTop = scrollContainer.scrollTop
    scrollContainer.scrollTop += autoScrollSpeed * elapsedSeconds
    const appliedScroll = scrollContainer.scrollTop - previousScrollTop
    if (appliedScroll !== 0) {
      // NOTICE: Anime.js applies this same scroll delta to its internal drag coordinate before
      // rendering. Repeating that invariant here keeps the row under a stationary pointer while
      // our edge/speed policy comes from DragDoll. References:
      // `https://github.com/juliangarnier/anime/blob/2c9cf8ea00329f6768c7d7902252ed977d75ce42/src/draggable/draggable.js#L753-L779`
      // `https://github.com/niklasramo/dragdoll/blob/7c108a170f07072b6c75899d7b3bf88c00d97b56/packages/dragdoll/src/auto-scroll/auto-scroll.ts#L413-L444`
      draggable.coords[1] += appliedScroll
      draggable.setY(draggable.coords[1])
      updatePendingReorder(draggable)
    }

    autoScrollFrame = requestAnimationFrame(updateAutoScroll)
  }

  function startAutoScroll(draggable: Draggable): void {
    stopAutoScroll()
    const targetRect = draggable.$target.getBoundingClientRect()
    pointerClientY = targetRect.top + targetRect.height / 2
    autoScrollFrame = requestAnimationFrame(updateAutoScroll)
  }

  function onPointerMove(event: PointerEvent): void {
    if (activeDraggable)
      pointerClientY = event.clientY
  }

  function createRowDraggable(element: HTMLElement, id: string): Draggable {
    const trigger = element.querySelector<HTMLElement>('.timeline-track-drag-handle')
    if (!trigger)
      throw new Error(`Cannot make timeline track "${id}" sortable: drag handle is missing`)

    // NOTICE: Virtua owns absolute row placement, while Anime.js applies only temporary local
    // transforms. The snap/reorder/displaced-row behavior follows Anime.js' maintained list demo;
    // see
    // `https://github.com/juliangarnier/anime/blob/2c9cf8ea00329f6768c7d7902252ed977d75ce42/examples/draggable-playground/index.js#L253-L296`.
    return createDraggable(element, {
      // The shared viewport defines both drag bounds and the scrollable range; the track-list
      // element itself never scrolls.
      container: options.scrollContainer() ?? undefined,
      containerPadding: [options.scrollPaddingTop, 0, 0, 0],
      cursor: false,
      // NOTICE: Track ordering must resolve from the pointer's snapped row, not Anime.js' default
      // velocity projection. Its release path extrapolates `destY` from pointer velocity at
      // `https://github.com/juliangarnier/anime/blob/2c9cf8ea00329f6768c7d7902252ed977d75ce42/src/draggable/draggable.js#L1049-L1058`.
      maxVelocity: 0,
      onGrab: (draggable) => {
        activeDraggable = draggable
        activeTrackId = id
        dragSourceIndex = options.itemIds().indexOf(id)
        activeIndex.value = dragSourceIndex
        pendingReorder = null
        draggable.$target.classList.add('timeline-track-row--dragging')
        startAutoScroll(draggable)
      },
      onRelease: (draggable) => {
        stopAutoScroll()
        // NOTICE: Anime.js starts `scrollInView` before invoking `onRelease`. Stop that release
        // animation so it cannot add a final viewport jump after our edge loop has ended; see
        // `https://github.com/juliangarnier/anime/blob/2c9cf8ea00329f6768c7d7902252ed977d75ce42/src/draggable/draggable.js#L1117-L1135`.
        draggable.stop()
        draggable.$target.classList.remove('timeline-track-row--dragging')
        const reorder = pendingReorder
        resetMountedRows(true)
        activeDraggable = null
        activeTrackId = null
        activeIndex.value = null
        dragSourceIndex = -1
        pendingReorder = null
        if (!reorder)
          return

        const [sourceIndex, targetIndex] = reorder
        options.onReorder(sourceIndex, targetIndex)
      },
      onSnap: updatePendingReorder,
      // NOTICE: Anime.js starts auto-scroll only after the dragged target passes the container
      // boundary, whereas DragDoll activates inside a 50px edge zone. Disable Anime's scroll
      // delta here; `updateAutoScroll` supplies DragDoll's distance-based 500px/s policy instead.
      // References:
      // `https://github.com/juliangarnier/anime/blob/2c9cf8ea00329f6768c7d7902252ed977d75ce42/src/draggable/draggable.js#L753-L779`
      // `https://github.com/niklasramo/dragdoll/blob/7c108a170f07072b6c75899d7b3bf88c00d97b56/packages/dragdoll/src/auto-scroll/auto-scroll.ts#L32`
      // `https://github.com/niklasramo/dragdoll/blob/7c108a170f07072b6c75899d7b3bf88c00d97b56/packages/dragdoll/src/auto-scroll/auto-scroll.ts#L413-L444`
      scrollSpeed: 0,
      scrollThreshold: 0,
      snap: options.rowHeight,
      trigger,
      x: false,
    })
  }

  function syncMountedRows(): void {
    const container = options.container.value
    if (!container)
      return

    if (options.enabled?.() === false) {
      draggables.forEach(draggable => draggable.revert())
      draggables.clear()
      return
    }

    const mountedIds = new Set<string>()
    container.querySelectorAll<HTMLElement>('[data-track-id]').forEach((element) => {
      const id = element.dataset.trackId
      if (!id)
        return

      mountedIds.add(id)
      if (!draggables.has(id))
        draggables.set(id, createRowDraggable(element, id))
    })

    draggables.forEach((draggable, id) => {
      if (mountedIds.has(id) || draggable === activeDraggable)
        return

      draggable.revert()
      draggables.delete(id)
    })
  }

  function initialize(): void {
    const container = options.container.value
    if (!container)
      return

    syncMountedRows()
    document.addEventListener('pointermove', onPointerMove, { passive: true })
    // Virtua mounts and recycles only the rows around the shared scroll viewport. Keep Anime.js
    // bindings aligned with that live DOM subset instead of creating one draggable per track.
    mutationObserver = new MutationObserver(syncMountedRows)
    mutationObserver.observe(container, { childList: true, subtree: true })
  }

  onMounted(initialize)

  watch([options.itemIds, () => options.enabled?.() ?? true], async () => {
    await nextTick()
    resetMountedRows(true)
    syncMountedRows()
  })

  onScopeDispose(() => {
    stopAutoScroll()
    document.removeEventListener('pointermove', onPointerMove)
    mutationObserver?.disconnect()
    mutationObserver = null
    draggables.forEach(draggable => draggable.revert())
    draggables.clear()
  })

  return {
    activeIndex: readonly(activeIndex),
  }
}
