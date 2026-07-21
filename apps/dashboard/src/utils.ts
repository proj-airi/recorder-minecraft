export function badgeClass(value: unknown): string {
  if (['complete', 'disconnected', 'ok', 'online', 'recording', 'running', 'stopped'].includes(String(value)))
    return 'ok'
  if (['attaching', 'busy', 'downloading', 'generating', 'partial', 'queued', 'rendering', 'sealing', 'stale', 'starting', 'stopping', 'uploading', 'verifying', 'waiting_for_seal', 'warning'].includes(String(value)))
    return 'warning'
  if (['docker_unavailable', 'failed', 'full', 'interrupted', 'offline', 'unhealthy'].includes(String(value)))
    return 'error'

  return 'neutral'
}

export function finiteCoordinate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function fmtBytes(value: number): string {
  if (!Number.isFinite(value))
    return '-'

  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let index = 0
  let size = value
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }

  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`
}

export function formatControlNumber(value: unknown, suffix = ''): string {
  return finiteCoordinate(value) ? `${value.toFixed(1)}${suffix}` : '-'
}

export function normalizedPacketValue(value: unknown): string {
  return typeof value === 'string' ? value.toLowerCase().replaceAll('-', '_') : ''
}

export function shortDimension(value: unknown): string {
  return String(value || 'unknown').replace(/^minecraft:/, '')
}

export function stateDifference(before: any, after: any, prefix = '', result: Record<string, { from: any, to: any }> = {}) {
  const beforeObject = before && typeof before === 'object' && !Array.isArray(before)
  const afterObject = after && typeof after === 'object' && !Array.isArray(after)
  if (beforeObject && afterObject) {
    new Set([...Object.keys(before), ...Object.keys(after)]).forEach((key) => {
      stateDifference(before[key], after[key], prefix ? `${prefix}.${key}` : key, result)
    })
  }
  else if (JSON.stringify(before) !== JSON.stringify(after)) {
    result[prefix || 'value'] = { from: before, to: after }
  }

  return result
}
