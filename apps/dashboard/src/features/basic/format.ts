const TICKS_PER_SECOND = 20

export function formatDurationSeconds(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0)
    return '—'
  const seconds = Math.floor(totalSeconds % 60)
  const minutes = Math.floor(totalSeconds / 60) % 60
  const hours = Math.floor(totalSeconds / 3600)
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`
}

/** `m:ss` or `h:mm:ss` for a tick count at 20 ticks per second. */
export function formatDurationTicks(ticks: number): string {
  if (!Number.isFinite(ticks) || ticks < 0)
    return '—'
  return formatDurationSeconds(Math.round(ticks / TICKS_PER_SECOND))
}

/** Episode time as `mm:ss.cc` (one tick is 0.05 s). */
export function formatEpisodeTime(tick: number): string {
  if (!Number.isFinite(tick))
    return '—'
  const sign = tick < 0 ? '-' : ''
  const centiseconds = Math.round(Math.abs(tick) * 100 / TICKS_PER_SECOND)
  const minutes = Math.floor(centiseconds / 6000)
  const seconds = Math.floor(centiseconds / 100) % 60
  return `${sign}${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(centiseconds % 100).padStart(2, '0')}`
}

/** Full UTC instant for details, e.g. `2026-10-10 08:00:03.300 UTC`. */
export function formatInstantFull(value?: string): string {
  const time = value ? Date.parse(value) : Number.NaN
  if (!Number.isFinite(time))
    return '—'
  return `${new Date(time).toISOString().replace('T', ' ').replace('Z', '')} UTC`
}

/** Short UTC date and time, e.g. `Oct 10, 08:00:03`. */
export function formatInstantShort(value?: string): string {
  const time = value ? Date.parse(value) : Number.NaN
  if (!Number.isFinite(time))
    return 'Unknown time'
  return new Intl.DateTimeFormat('en-US', {
    day: 'numeric',
    hour: '2-digit',
    hourCycle: 'h23',
    minute: '2-digit',
    month: 'short',
    second: '2-digit',
    timeZone: 'UTC',
  }).format(new Date(time))
}

/** Parses an int64 ProtoJSON string. Returns undefined when it is missing or not a safe integer. */
export function parseTick(value: number | string | undefined): number | undefined {
  if (value === undefined || value === '')
    return undefined
  const tick = Number(value)
  return Number.isSafeInteger(tick) ? tick : undefined
}
