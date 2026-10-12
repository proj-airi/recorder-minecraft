/**
 * URL of a game texture served from the operator's client jar, e.g. `gui/sprites/hud/heart/full`.
 * The server only serves HUD sprites, slot placeholders, and status effect icons.
 */
export function gameTextureUrl(path: string, namespace = 'minecraft'): string {
  return `/game-textures/${namespace}/${path}.png`
}

/**
 * URL of an inventory icon for a namespaced item id, served by `recorder-minecraft serve` from the
 * operator's own client jar (`[assets] minecraft_client_jar`). The server answers 404 when no jar is
 * configured or the item has no icon, so callers must keep a text fallback.
 */
export function itemIconUrl(itemId: string): null | string {
  const separator = itemId.indexOf(':')
  const namespace = separator >= 0 ? itemId.slice(0, separator) : 'minecraft'
  const path = separator >= 0 ? itemId.slice(separator + 1) : itemId
  if (!/^[a-z0-9_.-]+$/.test(namespace) || !/^[a-z0-9_.-]+$/.test(path))
    return null
  return `/item-icons/${namespace}/${path}.png`
}
