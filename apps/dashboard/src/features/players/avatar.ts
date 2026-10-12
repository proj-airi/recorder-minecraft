/**
 * Local avatar drawn from the player identity: an 8x8 mirrored pixel face in two colors. It is
 * deterministic, so one player keeps one face in every panel, and it needs no network.
 */
export interface GeneratedAvatar {
  background: string
  foreground: string
  /** Row-major 8x8 cells; true cells use `foreground`. */
  pixels: readonly boolean[]
}

export interface PlayerAvatar {
  /** Always present: shown when there is no image or the image fails to load. */
  generated: GeneratedAvatar
  /** Remote face image, when the provider can resolve one for this player. */
  imageUrl?: string
  /** Why the avatar looks the way it does, for tooltips. */
  note: string
}

/** Resolves avatars for one game. Other games add a provider; the components stay the same. */
export interface PlayerAvatarProvider {
  game: string
  resolve: (player: PlayerIdentity, sizePx: number) => PlayerAvatar
}

/** Who an avatar stands for. `game` selects the provider; Plays in this dashboard are Minecraft. */
export interface PlayerIdentity {
  game?: string
  name?: string
  uuid?: string
}

// NOTICE: Face renders come from a public skin render service so an `<img>` can load them without
// CORS. Mojang's own session server has no CORS headers, so the browser cannot read the skin URL
// from it directly. Trade-off: every online-mode player UUID shown here is sent to this third
// party. Move this behind the recorder API (fetch the profile and skin server-side, cache, serve
// the face) when that is not acceptable.
export const MINECRAFT_FACE_URL = (uuid: string, sizePx: number) => `https://mc-heads.net/avatar/${uuid}/${sizePx}`

/**
 * UUID version from the hyphenated or compact form, or null.
 *
 * Minecraft online-mode UUIDs are random (version 4) and have a Mojang profile with a skin.
 * Offline-mode servers derive version 3 UUIDs from `OfflinePlayer:<name>`; those players have no
 * profile, so a render service would only return a default skin.
 */
export function uuidVersion(uuid: string | undefined): null | number {
  const compact = uuid?.replaceAll('-', '').toLowerCase()
  if (!compact || !/^[0-9a-f]{32}$/.test(compact))
    return null
  return Number.parseInt(compact[12]!, 16)
}

export const minecraftAvatarProvider: PlayerAvatarProvider = {
  game: 'minecraft',
  resolve(player, sizePx) {
    const generated = generateAvatar(player.uuid ?? player.name ?? '?')
    const version = uuidVersion(player.uuid)
    if (version === 4)
      return { generated, imageUrl: MINECRAFT_FACE_URL(player.uuid!.replaceAll('-', ''), sizePx), note: 'Minecraft skin' }
    if (version === 3)
      return { generated, note: 'Offline-mode player: no Mojang skin, generated avatar' }
    return { generated, note: 'No player UUID: generated avatar' }
  },
}

const providers: PlayerAvatarProvider[] = [minecraftAvatarProvider]

export function generateAvatar(seed: string): GeneratedAvatar {
  const hash = fnv1a(seed)
  // The face hue sits 150° from a dark background hue, so the pattern never blends into it.
  const hue = hash % 360
  const background = `hsl(${hue} 32% 22%)`
  const foreground = `hsl(${(hue + 150) % 360} 70% 68%)`
  const pixels: boolean[] = []
  let bits = fnv1a(`${seed}:pixels`)
  for (let row = 0; row < 8; row += 1) {
    const half: boolean[] = []
    for (let column = 0; column < 4; column += 1) {
      half.push((bits & 1) === 1)
      bits = bits >>> 1 || fnv1a(`${seed}:${row}:${column}`)
    }
    // Mirror the left half so the pattern reads as a face rather than noise.
    pixels.push(...half, ...[...half].reverse())
  }
  return { background, foreground, pixels }
}

export function resolvePlayerAvatar(player: PlayerIdentity, sizePx: number): PlayerAvatar {
  const provider = providers.find(candidate => candidate.game === (player.game ?? 'minecraft'))
  return provider?.resolve(player, sizePx) ?? { generated: generateAvatar(player.uuid ?? player.name ?? '?'), note: 'Generated avatar' }
}

function fnv1a(value: string): number {
  let hash = 0x811C9DC5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash
}
