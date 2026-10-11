import type { EpisodeDraft, EpisodeSession, PlayPlacement } from './domain'

/**
 * Tick mapping helpers.
 *
 * Three coordinates exist:
 * - episode tick: the shared timeline axis (`session.playheadTick`);
 * - Server tick: `identity.serverTick` in captured data, only comparable inside one session;
 * - Play tick: Server tick minus the Play's `startServerTick` (offset into one Play).
 *
 * A placement maps episode ticks through its own trim and move: the clip starts at `startTick`
 * with Server tick `sourceStartServerTick`. A session maps through its anchor and ignores clip
 * edits, so session-scoped (world) data stays fixed when a Play clip is moved.
 */
export type TickMappingTarget = EpisodeSession | PlayPlacement

export function episodeTickToServerTick(target: TickMappingTarget, episodeTick: number): number {
  if (isPlacement(target))
    return playServerTickAt(target, episodeTick)
  return target.anchorServerTick + episodeTick - target.anchorTick
}

/** The placement on a player lane that covers an episode tick; the latest start wins on overlap. */
export function placementAt(episode: EpisodeDraft, laneId: string, episodeTick: number): null | PlayPlacement {
  let found: null | PlayPlacement = null
  for (const placement of episode.placements) {
    if (placement.laneId !== laneId || !placementContainsTick(placement, episodeTick))
      continue
    if (!found || placement.startTick >= found.startTick)
      found = placement
  }
  return found
}

/** True when the Server tick is inside the trimmed source range `[sourceStart, sourceEnd]`. */
export function placementContainsServerTick(placement: PlayPlacement, serverTick: number): boolean {
  return serverTick >= placement.sourceStartServerTick && serverTick <= placement.sourceEndServerTick
}

/** True when the episode tick is inside the clip, using a half-open `[startTick, endTick)` range. */
export function placementContainsTick(placement: PlayPlacement, episodeTick: number): boolean {
  return episodeTick >= placement.startTick && episodeTick < placement.endTick
}

/** Server tick shown by a placement at an episode tick. It does not check the clip range. */
export function playServerTickAt(placement: PlayPlacement, episodeTick: number): number {
  return placement.sourceStartServerTick + episodeTick - placement.startTick
}

/** Offset into the Play (Server tick minus Play start) at an episode tick, or null outside the clip. */
export function playTickAt(placement: PlayPlacement, episodeTick: number): null | number {
  if (!placementContainsTick(placement, episodeTick))
    return null
  return playServerTickAt(placement, episodeTick) - placement.playStartServerTick
}

export function serverTickToEpisodeTick(target: TickMappingTarget, serverTick: number): number {
  if (isPlacement(target))
    return target.startTick + serverTick - target.sourceStartServerTick
  return target.anchorTick + serverTick - target.anchorServerTick
}

export function sessionForPlacement(episode: EpisodeDraft, placement: PlayPlacement): EpisodeSession | undefined {
  return episode.sessions.find(session => session.key === placement.sessionKey)
}

function isPlacement(target: TickMappingTarget): target is PlayPlacement {
  return 'sourceStartServerTick' in target
}
