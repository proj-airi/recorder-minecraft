import { describe, expect, it } from 'vitest'

import { generateAvatar, resolvePlayerAvatar, uuidVersion } from './avatar'

describe('player avatars', () => {
  it('reads the UUID version from hyphenated and compact forms', () => {
    expect(uuidVersion('25ec515b-aea2-4d35-a305-873d5cfe849d')).toBe(4)
    expect(uuidVersion('2575798e2b633ebea39e5e3a3eba2b3f')).toBe(3)
    expect(uuidVersion('uuid-alice')).toBeNull()
    expect(uuidVersion(undefined)).toBeNull()
  })

  it('loads a skin face only for online-mode (version 4) Minecraft players', () => {
    const online = resolvePlayerAvatar({ name: 'NekoMeowww', uuid: '25ec515b-aea2-4d35-a305-873d5cfe849d' }, 40)
    expect(online.imageUrl).toBe('https://mc-heads.net/avatar/25ec515baea24d35a305873d5cfe849d/40')

    // Offline-mode UUIDs have no Mojang profile; a render service would return a default skin.
    const offline = resolvePlayerAvatar({ name: 'Airi', uuid: '2575798e-2b63-3ebe-a39e-5e3a3eba2b3f' }, 40)
    expect(offline.imageUrl).toBeUndefined()
    expect(offline.note).toMatch(/Offline-mode/)
  })

  it('draws the same mirrored face for the same player and different faces for others', () => {
    const airi = generateAvatar('2575798e-2b63-3ebe-a39e-5e3a3eba2b3f')
    expect(generateAvatar('2575798e-2b63-3ebe-a39e-5e3a3eba2b3f')).toEqual(airi)
    expect(generateAvatar('f3d28cb0-7225-3cb1-baeb-2dadd2be89ae')).not.toEqual(airi)
    expect(airi.pixels).toHaveLength(64)
    for (let row = 0; row < 8; row += 1) {
      const cells = airi.pixels.slice(row * 8, row * 8 + 8)
      expect(cells).toEqual([...cells].reverse())
    }
  })
})
