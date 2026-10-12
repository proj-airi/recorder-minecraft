// NOTICE: Timeline segments of every Play share one clip color, so cards keyed on it showed the
// same dot for every player. Until the timeline and event log share a player palette, the Inputs
// panel assigns colors by lane order. Hues are spaced for a dark background and avoid amber,
// which this panel uses for pressed keys and the selected slot.
const PLAYER_COLORS = ['#f472b6', '#60a5fa', '#34d399', '#c084fc', '#f87171', '#22d3ee', '#a3e635', '#fb923c'] as const

/** Identity color of the player lane at `index` in timeline order. */
export function playerColor(index: number): string {
  return PLAYER_COLORS[index % PLAYER_COLORS.length]!
}
