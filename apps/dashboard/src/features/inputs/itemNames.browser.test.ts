import { describe, expect, it } from 'vitest'

import { effectDisplayName, itemDisplayName, romanLevel, yawHeading } from './itemNames'

describe('item names', () => {
  it('title-cases vanilla ids and keeps a modded namespace', () => {
    expect(itemDisplayName('minecraft:oak_sapling')).toBe('Oak Sapling')
    expect(itemDisplayName('bread')).toBe('Bread')
    expect(itemDisplayName('create:brass_ingot')).toBe('Brass Ingot (create)')
  })
})

describe('yaw heading', () => {
  it('maps Minecraft yaw (0 = south, clockwise) to compass headings', () => {
    expect(yawHeading(0)).toBe('S')
    expect(yawHeading(90)).toBe('W')
    expect(yawHeading(180)).toBe('N')
    expect(yawHeading(-90)).toBe('E')
    expect(yawHeading(36.9)).toBe('SW')
    expect(yawHeading(-135)).toBe('NE')
  })
})

describe('status effects', () => {
  it('names effects and prints levels as the game does', () => {
    expect(effectDisplayName('minecraft:fire_resistance')).toBe('Fire Resistance')
    expect([1, 2, 4, 5, 9, 10, 14].map(romanLevel)).toEqual(['I', 'II', 'IV', 'V', 'IX', 'X', 'XIV'])
    expect(romanLevel(0)).toBe('0')
    expect(romanLevel(256)).toBe('256')
  })
})
