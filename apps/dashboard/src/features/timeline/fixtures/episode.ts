import type { EpisodeDraft, EpisodeSegment, EpisodeTrack } from '../domain'

import { SERVER_TICK_RATE } from '../domain'

export interface EpisodeOptions {
  clipsPerTrack: number
  durationTicks: number
  id: string
  title: string
  trackCount: number
}

export const stressOptions = {
  clipsPerTrack: 25,
  durationTicks: SERVER_TICK_RATE * 60 * 20,
  id: 'timeline-stress',
  title: 'Timeline stress test · 200 tracks',
  trackCount: 200,
} satisfies EpisodeOptions

const clipColors = [
  '#31b899',
  '#3d8bd9',
  '#7d6ee7',
  '#9c6ade',
  '#df6b63',
  '#d89b45',
  '#bd718a',
  '#677fb8',
]

/** Creates deterministic timeline data without depending on Vue, Pinia, or browser globals. */
export function createEpisode(options: EpisodeOptions): EpisodeDraft {
  const tracks = Array.from({ length: options.trackCount }, (_, trackIndex): EpisodeTrack => ({
    id: `video-${trackIndex + 1}`,
    kind: 'video',
    label: `Video ${trackIndex + 1}`,
  }))
  const slotTicks = Math.floor(options.durationTicks / options.clipsPerTrack)
  const segments = tracks.flatMap((track, trackIndex) => Array.from(
    { length: options.clipsPerTrack },
    (_, clipIndex): EpisodeSegment => {
      // Each clip stays inside its time slot, while deterministic offsets and durations prevent
      // large fixtures from degenerating into visually identical rows.
      const offsetTicks = (trackIndex * 37 + clipIndex * 53) % 180
      const durationTicks = 240 + (trackIndex * 29 + clipIndex * 71) % 480
      const startTick = clipIndex * slotTicks + offsetTicks
      return {
        color: clipColors[(trackIndex + clipIndex) % clipColors.length] ?? clipColors[0]!,
        endTick: Math.min(options.durationTicks, startTick + durationTicks),
        id: `${track.id}-clip-${clipIndex + 1}`,
        label: `V${trackIndex + 1} · Clip ${clipIndex + 1}`,
        startTick,
        trackId: track.id,
      }
    },
  ))

  return {
    durationTicks: options.durationTicks,
    id: options.id,
    revision: 1,
    segments,
    title: options.title,
    tracks,
  }
}
