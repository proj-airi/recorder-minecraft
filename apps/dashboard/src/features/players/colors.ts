import type { EpisodeDraft } from '../timeline/domain'

// NOTICE: Timeline segments of every Play share one clip color, so they cannot tell players apart.
// Identity colors are assigned per lane instead, which keeps every player in one view distinct (a
// UUID hash could collide). The event log should adopt it too. Hues are spaced for a dark
// background and avoid amber, which the dashboard uses for pressed keys and selection.
const PLAYER_COLORS = ['#f472b6', '#60a5fa', '#34d399', '#c084fc', '#f87171', '#22d3ee', '#a3e635', '#fb923c'] as const

/** Color at `index` of the identity palette. */
export function playerColor(index: number): string {
  return PLAYER_COLORS[index % PLAYER_COLORS.length]!
}

/**
 * Identity color of every player lane, keyed by lane id.
 *
 * Colors follow the order in which lanes first joined the episode (placement order), not the
 * current display order: reordering lanes must not swap two players' colors between panels or
 * between one moment and the next.
 */
export function playerLaneColors(episode: Pick<EpisodeDraft, 'placements'>): Map<string, string> {
  const colors = new Map<string, string>()
  for (const placement of episode.placements) {
    if (!colors.has(placement.laneId))
      colors.set(placement.laneId, playerColor(colors.size))
  }
  return colors
}
