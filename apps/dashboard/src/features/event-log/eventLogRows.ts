import type { EpisodeDraft, PlayPlacement } from '../timeline/domain'
import type { CaptureEventKind, CaptureLogEntry, ParsedCaptureEvents } from './captureEvents'

import { serverTickToEpisodeTick } from '../timeline/ticks'
import { lastIndexAtOrBefore } from './captureEvents'

export interface EventLogFilter {
  kinds: ReadonlySet<CaptureEventKind>
  /** Player keys to show; an empty set shows every player. */
  players: ReadonlySet<string>
  query: string
}

/** One event-log row: a capture entry placed on the episode axis through its clip. */
export interface EventLogRow {
  entry: CaptureLogEntry
  /** Episode tick, mapped through the clip (trim, cut and move are respected). */
  episodeTick: number
  /** Unique row key: `<placementId>:<entry id>`. */
  key: string
  laneId: string
  placementId: string
  playerKey: string
  playerName: string
}

/**
 * Builds the rows of every clip on the timeline, sorted by episode tick. Entries outside a clip's
 * trimmed source range are left out, so a cut Play shows each event once, in the clip that shows
 * it. Join and leave rows come from the Play range, not from a capture record.
 */
export function buildEventLogRows(episode: EpisodeDraft, parsedByUrl: ReadonlyMap<string, null | ParsedCaptureEvents>): EventLogRow[] {
  const rows: EventLogRow[] = []
  episode.placements.forEach((placement) => {
    const parsed = placement.source.eventsUrl ? parsedByUrl.get(placement.source.eventsUrl) : null
    for (const entry of connectionEntries(placement)) {
      if (clipShowsServerTick(placement, entry.serverTick))
        rows.push(toRow(placement, entry))
    }
    if (!parsed)
      return
    const entries = parsed.entries
    // Entries are sorted by Server tick: find the first one inside the clip, then walk.
    let index = lastIndexAtOrBefore(entries, placement.sourceStartServerTick - 1) + 1
    for (; index < entries.length; index += 1) {
      const entry = entries[index]!
      if (!clipShowsServerTick(placement, entry.serverTick))
        break
      rows.push(toRow(placement, entry))
    }
  })
  const placementOrder = new Map(episode.placements.map((placement, index) => [placement.id, index]))
  return rows.sort((left, right) => left.episodeTick - right.episodeTick
    || connectionRank(left.entry) - connectionRank(right.entry)
    || placementOrder.get(left.placementId)! - placementOrder.get(right.placementId)!
    || left.entry.sequence - right.entry.sequence)
}

/** Index of the last row at or before the episode tick, or -1. Rows must be sorted by episode tick. */
export function currentRowIndex(rows: readonly EventLogRow[], episodeTick: number): number {
  let low = 0
  let high = rows.length - 1
  while (low <= high) {
    const middle = (low + high) >> 1
    if (rows[middle]!.episodeTick <= episodeTick)
      low = middle + 1
    else
      high = middle - 1
  }
  return high
}

export function filterEventLogRows(rows: readonly EventLogRow[], filter: EventLogFilter): EventLogRow[] {
  const query = filter.query.trim().toLowerCase()
  return rows.filter((row) => {
    if (!filter.kinds.has(row.entry.kind))
      return false
    if (filter.players.size > 0 && !filter.players.has(row.playerKey))
      return false
    if (!query)
      return true
    return row.entry.summary.toLowerCase().includes(query)
      || row.entry.label.toLowerCase().includes(query)
      || row.entry.record.toLowerCase().includes(query)
      || row.playerName.toLowerCase().includes(query)
      || String(row.entry.serverTick) === query
  })
}

/**
 * Source ranges are inclusive at both ends, so two halves of a cut share the cut tick. The tick
 * belongs to the right half; only the Play's last tick is shown at a clip's end.
 */
function clipShowsServerTick(placement: PlayPlacement, serverTick: number): boolean {
  return serverTick >= placement.sourceStartServerTick
    && (serverTick < placement.sourceEndServerTick || (serverTick === placement.sourceEndServerTick && serverTick === placement.playEndServerTick))
}

function connectionEntries(placement: PlayPlacement): CaptureLogEntry[] {
  const replay = placement.source.replay
  const terminal = replay?.terminalReason ? ` (${replay.terminalReason.replaceAll('_', ' ')})` : ''
  return [
    {
      id: 'join',
      kind: 'connection',
      label: 'join',
      record: 'connection',
      sequence: -1,
      serverTick: placement.playStartServerTick,
      summary: `${placement.playerName} joined ${placement.source.serverName}`,
    },
    {
      id: 'leave',
      kind: 'connection',
      label: 'leave',
      record: 'connection',
      sequence: Number.MAX_SAFE_INTEGER,
      serverTick: placement.playEndServerTick,
      summary: `${placement.playerName} left${terminal}`,
    },
  ]
}

function connectionRank(entry: CaptureLogEntry): number {
  if (entry.record !== 'connection')
    return 0
  return entry.label === 'join' ? -1 : 1
}

function toRow(placement: PlayPlacement, entry: CaptureLogEntry): EventLogRow {
  return {
    entry,
    episodeTick: serverTickToEpisodeTick(placement, entry.serverTick),
    key: `${placement.id}:${entry.id}`,
    laneId: placement.laneId,
    placementId: placement.id,
    playerKey: placement.playerKey,
    playerName: placement.playerName,
  }
}
