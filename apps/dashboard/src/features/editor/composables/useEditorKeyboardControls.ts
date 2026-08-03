import type { TimelineSession } from '../../timeline/composables/useTimelineSession'

import { useActiveElement, useEventListener, useIntervalFn, useMagicKeys, useTimeoutFn, whenever } from '@vueuse/core'
import { computed, watch } from 'vue'

interface EditorKeyboardControlsOptions {
  deleteSelected: () => void
  redo: () => void
  session: TimelineSession
  undo: () => void
}

const HOLD_DELAY_MS = 300
const SEEK_INTERVAL_MS = 80
const STEP_TICKS = 1
const SEEK_TICKS = 5
const ACCELERATION_WINDOW_MS = 600

export function useEditorKeyboardControls(options: EditorKeyboardControlsOptions): void {
  let accelerating = false
  let activeDirection: -1 | 0 | 1 = 0
  let holdStarted = false
  let pressedAt = 0
  const activeElement = useActiveElement()
  // NOTICE: `useMagicKeys` updates refs for both `KeyboardEvent.code` and `key` before invoking the
  // custom handler, so watchers below can use stable key names while the handler only suppresses
  // browser defaults. See
  // `https://github.com/vueuse/vueuse/blob/24160f9a8f1dcc576f1234008134ed47491c8183/packages/core/useMagicKeys/index.ts#L138-L182`.
  const keys = useMagicKeys({
    onEventFired(event) {
      if (!isTextEntry(event.target) && isEditorShortcut(event))
        event.preventDefault()
    },
    passive: false,
  })
  const editingText = computed(() => isTextEntry(activeElement.value ?? null))
  const primaryModifier = computed(() => keys.ctrl.value || keys.meta.value)
  const undoPressed = computed(() => !editingText.value && !keys.shift.value && keys.z.value && primaryModifier.value)
  const redoPressed = computed(() => !editingText.value && keys.shift.value && keys.z.value && primaryModifier.value)
  const goToStartPressed = computed(() => !editingText.value && keys.shift.value && keys.arrowleft.value && primaryModifier.value)
  const goToEndPressed = computed(() => !editingText.value && keys.shift.value && keys.arrowright.value && primaryModifier.value)
  const deletePressed = computed(() => !editingText.value && (keys.delete.value || keys.backspace.value))
  const { pause: pauseSeeking, resume: resumeSeeking } = useIntervalFn(
    () => options.session.seekByTicks(activeDirection * seekTickCount()),
    SEEK_INTERVAL_MS,
    { immediate: false },
  )
  const { start: startHoldTimer, stop: stopHoldTimer } = useTimeoutFn(() => {
    holdStarted = true
    options.session.seekByTicks(activeDirection * seekTickCount())
    resumeSeeking()
  }, HOLD_DELAY_MS, { immediate: false })

  function clearDirection(): void {
    stopHoldTimer()
    pauseSeeking()
    accelerating = false
    activeDirection = 0
    holdStarted = false
  }

  function seekTickCount(): number {
    if (!accelerating)
      return SEEK_TICKS

    // NOTICE: Option/Alt seek acceleration follows a quadratic curve rather than multiplying a
    // fixed browser key-repeat delta. The 600ms normalization keeps early movement controllable;
    // holding longer increases distance per 80ms pulse without a discontinuous speed jump.
    const elapsed = Math.max(0, performance.now() - pressedAt)
    const normalized = elapsed / ACCELERATION_WINDOW_MS
    return Math.max(SEEK_TICKS, Math.round(SEEK_TICKS * (1 + normalized * normalized)))
  }

  function updateDirection(direction: -1 | 1, pressed: boolean): void {
    if (pressed) {
      if (editingText.value || keys.ctrl.value || keys.meta.value || keys.shift.value || activeDirection !== 0)
        return

      accelerating = keys.alt.value
      activeDirection = direction
      pressedAt = performance.now()
      startHoldTimer()
      return
    }

    if (activeDirection !== direction)
      return

    if (!holdStarted)
      options.session.seekByTicks(direction * STEP_TICKS)
    clearDirection()
  }

  watch(keys.space, (pressed) => {
    if (pressed && !editingText.value) {
      if (options.session.isPlaying.value)
        options.session.pause()
      else
        options.session.play()
    }
  })
  watch(keys.arrowleft, pressed => updateDirection(-1, pressed))
  watch(keys.arrowright, pressed => updateDirection(1, pressed))
  whenever(undoPressed, options.undo)
  whenever(redoPressed, options.redo)
  whenever(goToStartPressed, options.session.goToStart)
  whenever(goToEndPressed, options.session.goToEnd)
  whenever(deletePressed, options.deleteSelected)
  useEventListener(window, 'blur', clearDirection)
}

function isEditorShortcut(event: KeyboardEvent): boolean {
  const primaryModifier = event.ctrlKey || event.metaKey
  const undo = primaryModifier && event.key.toLowerCase() === 'z'
  const playback = event.code === 'Space'
  const jump = event.shiftKey && primaryModifier && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
  const seek = !primaryModifier && !event.shiftKey && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')
  const deletion = event.key === 'Backspace' || event.key === 'Delete'
  return undo || playback || jump || seek || deletion
}

function isTextEntry(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (target.isContentEditable || target.matches('input, textarea, select'))
}
