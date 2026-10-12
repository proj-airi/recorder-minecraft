// NOTICE: Timeline segments of every Play share one clip color, so they cannot tell players apart.
// Identity colors are assigned by lane order instead, which keeps every player in one view
// distinct (a UUID hash could collide). Inputs uses this today; the event log and timeline should
// adopt it too. Hues are spaced for a dark background and avoid amber, which the dashboard uses for
// pressed keys and selection.
const PLAYER_COLORS = ['#f472b6', '#60a5fa', '#34d399', '#c084fc', '#f87171', '#22d3ee', '#a3e635', '#fb923c'] as const

/** Identity color of the player lane at `index` in timeline order. */
export function playerColor(index: number): string {
  return PLAYER_COLORS[index % PLAYER_COLORS.length]!
}
