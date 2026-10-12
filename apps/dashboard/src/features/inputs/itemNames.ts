/**
 * Human item name from a namespaced id: `minecraft:oak_sapling` → "Oak Sapling". Ids from other
 * namespaces keep their namespace as a suffix so modded items stay distinguishable.
 */
export function itemDisplayName(itemId: string): string {
  const separator = itemId.indexOf(':')
  const namespace = separator >= 0 ? itemId.slice(0, separator) : 'minecraft'
  const path = separator >= 0 ? itemId.slice(separator + 1) : itemId
  const words = path.split(/[_/]+/).filter(Boolean).map(word => word[0]!.toUpperCase() + word.slice(1))
  const name = words.join(' ') || itemId
  return namespace === 'minecraft' ? name : `${name} (${namespace})`
}

// Minecraft yaw is 0° facing south (+Z) and grows clockwise seen from above: 90° west, 180° north,
// -90° (= 270°) east. Normalizing to [0, 360) makes the 45° sector lookup below a single division.
const HEADINGS = ['S', 'SW', 'W', 'NW', 'N', 'NE', 'E', 'SE'] as const

/** Status effect name from its id: `minecraft:fire_resistance` → "Fire Resistance". */
export function effectDisplayName(effectId: string): string {
  return itemDisplayName(effectId)
}

/** Compass heading the player faces for a Minecraft yaw in degrees. */
export function yawHeading(yaw: number): (typeof HEADINGS)[number] {
  const normalized = ((yaw % 360) + 360) % 360
  return HEADINGS[Math.round(normalized / 45) % 8]!
}

const ROMAN = [[10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']] as const

/** Effect level as the game prints it: 2 → "II". Levels above 39 fall back to digits. */
export function romanLevel(level: number): string {
  if (!Number.isInteger(level) || level < 1 || level > 39)
    return String(level)
  let rest = level
  let text = ''
  for (const [value, numeral] of ROMAN) {
    while (rest >= value) {
      text += numeral
      rest -= value
    }
  }
  return text
}
