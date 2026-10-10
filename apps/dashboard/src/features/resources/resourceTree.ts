import type {
  RecorderMinecraftApiV1Replay,
  RecorderMinecraftApiV1ServerInstance,
  RecorderMinecraftApiV1WorldSession,
} from '@proj-airi/recorder-minecraft-api'

export interface ResourceCaptureSessionNode extends NodeBase {
  kind: 'capture-session'
  plays: RecorderMinecraftApiV1Replay[]
  serverInstanceId: string
  sessionId: string
}

export interface ResourcePlayNode extends NodeBase {
  kind: 'play'
  replay: RecorderMinecraftApiV1Replay
  /** True when the Play is listed under its world session (the row can omit the date). */
  underSession: boolean
}

export interface ResourceServerNode extends NodeBase {
  kind: 'server'
  playCount: number
  server: RecorderMinecraftApiV1ServerInstance
  sessionCount: number
}

export type ResourceSort = 'name' | 'newest' | 'oldest'

export interface ResourceTreeFilter {
  /** Expands every branch (used to find the ancestors of a node). */
  expandAll?: boolean
  playerUuid?: string
  query: string
  serverInstanceId?: string
  sort: ResourceSort
  /** Half-open `[start, end)` in Unix ms, or null. */
  timeRange: null | readonly number[]
}

/**
 * Resource tree of the sidebar:
 *
 * - server instance
 *   - world session → its Plays (one per player connection)
 *   - "Plays without a world session" (older captures)
 *     - capture session (two or more Plays share a `session_id`) → its Plays
 *     - single Plays
 */
export type ResourceTreeNode
  = | ResourceCaptureSessionNode
    | ResourcePlayNode
    | ResourceServerNode
    | ResourceUnlinkedGroupNode
    | ResourceWorldSessionNode

export interface ResourceTreeView {
  /** Visible rows in display order, with collapsed subtrees left out. */
  nodes: ResourceTreeNode[]
  /** Number of Plays that pass the filter. */
  playCount: number
  /** Number of world sessions that pass the filter. */
  sessionCount: number
}

export interface ResourceUnlinkedGroupNode extends NodeBase {
  kind: 'unlinked'
  playCount: number
  serverInstanceId: string
}

export interface ResourceWorldSessionNode extends NodeBase {
  kind: 'session'
  /** Plays of the session that pass the filter. */
  plays: RecorderMinecraftApiV1Replay[]
  session: RecorderMinecraftApiV1WorldSession
  /** Every Play of the session in the catalog, for "add session". */
  sessionPlays: RecorderMinecraftApiV1Replay[]
}

interface NodeBase {
  /** Child node ids, for keyboard navigation. Empty for leaves. */
  childIds: string[]
  depth: number
  expanded: boolean
  id: string
  parentId?: string
}

/** Ancestors of a node, outermost first, or null when the node is filtered out. */
export function ancestorsOf(
  servers: readonly RecorderMinecraftApiV1ServerInstance[],
  filter: ResourceTreeFilter,
  nodeId: string,
): null | ResourceTreeNode[] {
  const nodes = buildResourceTree(servers, { ...filter, expandAll: true }, new Set()).nodes
  const byId = new Map(nodes.map(node => [node.id, node]))
  let node = byId.get(nodeId)
  if (!node)
    return null
  const ancestors: ResourceTreeNode[] = []
  while (node.parentId) {
    node = byId.get(node.parentId)
    if (!node)
      break
    ancestors.unshift(node)
  }
  return ancestors
}

/**
 * Builds the visible tree. `expandedIds` holds the expanded nodes; with a search query every
 * branch with a match is expanded so matches are visible.
 */
export function buildResourceTree(
  servers: readonly RecorderMinecraftApiV1ServerInstance[],
  filter: ResourceTreeFilter,
  expandedIds: ReadonlySet<string>,
): ResourceTreeView {
  const query = filter.query.trim().toLowerCase()
  const forceExpanded = filter.expandAll === true || query.length > 0
  const nodes: ResourceTreeNode[] = []
  let playCount = 0
  let sessionCount = 0

  const orderedServers = [...servers].sort((left, right) => (left.name ?? '').localeCompare(right.name ?? '') || (left.instanceId ?? '').localeCompare(right.instanceId ?? ''))
  for (const server of orderedServers) {
    const serverInstanceId = server.instanceId ?? ''
    if (filter.serverInstanceId && filter.serverInstanceId !== serverInstanceId)
      continue

    const replays = (server.players ?? []).flatMap(player => player.replays ?? []).map(replay => ({
      ...replay,
      serverInstanceId: replay.serverInstanceId ?? serverInstanceId,
      serverName: replay.serverName ?? server.name,
    }))
    const serverMatches = matchesQuery(query, [server.name, serverInstanceId])
    const passesBase = (replay: RecorderMinecraftApiV1Replay) => passesPlayer(filter, replay) && inTimeRange(filter.timeRange, replay.startedAt)

    // World sessions with their Plays.
    const sessionIds = new Set((server.worldSessions ?? []).map(session => session.id))
    const sessionBranches: { node: ResourceWorldSessionNode, plays: RecorderMinecraftApiV1Replay[] }[] = []
    for (const session of server.worldSessions ?? []) {
      const sessionPlays = replays.filter(replay => replay.worldSessionId === session.id)
      const sessionMatches = serverMatches || matchesQuery(query, [session.id, session.sessionId, session.serverName, session.terminalReason])
      const plays = sortPlays(sessionPlays.filter(replay => passesBase(replay) && (sessionMatches || replayMatches(query, replay))), 'name')
      const sessionVisible = plays.length > 0
        || (!filter.playerUuid && sessionMatches && inTimeRange(filter.timeRange, session.startedAt))
      if (!sessionVisible)
        continue
      const id = sessionNodeId(serverInstanceId, session.id ?? '')
      sessionBranches.push({
        node: {
          childIds: plays.map(replay => playNodeId(replay.connectionId ?? '')),
          depth: 1,
          expanded: forceExpanded || expandedIds.has(id),
          id,
          kind: 'session',
          parentId: serverNodeId(serverInstanceId),
          plays,
          session,
          sessionPlays,
        },
        plays,
      })
    }
    sessionBranches.sort((left, right) => compareStart(left.node.session.startedAt, right.node.session.startedAt, filter.sort))

    // Plays without a world session.
    const unlinked = sortPlays(replays.filter(replay => (!replay.worldSessionId || !sessionIds.has(replay.worldSessionId))
      && passesBase(replay)
      && (serverMatches || replayMatches(query, replay))), filter.sort)
    const bySession = new Map<string, RecorderMinecraftApiV1Replay[]>()
    for (const replay of unlinked) {
      if (replay.sessionId)
        bySession.set(replay.sessionId, [...(bySession.get(replay.sessionId) ?? []), replay])
    }
    const unlinkedId = unlinkedNodeId(serverInstanceId)
    const unlinkedChildren: (ResourceCaptureSessionNode | ResourcePlayNode)[] = []
    const emittedCaptureSessions = new Set<string>()
    for (const replay of unlinked) {
      const group = replay.sessionId ? bySession.get(replay.sessionId) : undefined
      if (group && group.length > 1) {
        if (emittedCaptureSessions.has(replay.sessionId!))
          continue
        emittedCaptureSessions.add(replay.sessionId!)
        const id = captureSessionNodeId(serverInstanceId, replay.sessionId!)
        const plays = sortPlays(group, 'name')
        unlinkedChildren.push({
          childIds: plays.map(play => playNodeId(play.connectionId ?? '')),
          depth: 2,
          expanded: forceExpanded || expandedIds.has(id),
          id,
          kind: 'capture-session',
          parentId: unlinkedId,
          plays,
          serverInstanceId,
          sessionId: replay.sessionId!,
        })
        continue
      }
      unlinkedChildren.push(playNode(replay, 2, unlinkedId, false))
    }

    const visiblePlays = sessionBranches.reduce((total, branch) => total + branch.plays.length, 0) + unlinked.length
    if (sessionBranches.length === 0 && unlinked.length === 0)
      continue
    playCount += visiblePlays
    sessionCount += sessionBranches.length

    const serverId = serverNodeId(serverInstanceId)
    // Servers start expanded: they are collapsed only when the id is in the set with a `!` prefix.
    const serverExpanded = forceExpanded || !expandedIds.has(`!${serverId}`)
    const unlinkedExpanded = forceExpanded || !expandedIds.has(`!${unlinkedId}`)
    nodes.push({
      childIds: [...sessionBranches.map(branch => branch.node.id), ...(unlinked.length ? [unlinkedId] : [])],
      depth: 0,
      expanded: serverExpanded,
      id: serverId,
      kind: 'server',
      playCount: visiblePlays,
      server,
      sessionCount: sessionBranches.length,
    })
    if (!serverExpanded)
      continue
    for (const branch of sessionBranches) {
      nodes.push(branch.node)
      if (branch.node.expanded)
        nodes.push(...branch.plays.map(replay => playNode(replay, 2, branch.node.id, true)))
    }
    if (unlinked.length === 0)
      continue
    nodes.push({
      childIds: unlinkedChildren.map(child => child.id),
      depth: 1,
      expanded: unlinkedExpanded,
      id: unlinkedId,
      kind: 'unlinked',
      parentId: serverId,
      playCount: unlinked.length,
      serverInstanceId,
    })
    if (!unlinkedExpanded)
      continue
    for (const child of unlinkedChildren) {
      nodes.push(child)
      if (child.kind === 'capture-session' && child.expanded)
        nodes.push(...child.plays.map(replay => playNode(replay, 3, child.id, true)))
    }
  }

  return { nodes, playCount, sessionCount }
}

export function captureSessionNodeId(serverInstanceId: string, sessionId: string): string {
  return `capture:${serverInstanceId}:${sessionId}`
}

export function playNodeId(connectionId: string): string {
  return `play:${connectionId}`
}

/** Searchable text of a Play: player, server, and every id. */
export function replayMatches(query: string, replay: RecorderMinecraftApiV1Replay): boolean {
  return matchesQuery(query, [
    replay.playerName,
    replay.playerUuid,
    replay.serverName,
    replay.serverInstanceId,
    replay.sessionId,
    replay.connectionId,
    replay.worldSessionId,
  ])
}

export function serverNodeId(serverInstanceId: string): string {
  return `server:${serverInstanceId}`
}

export function sessionNodeId(serverInstanceId: string, worldSessionId: string): string {
  return `session:${serverInstanceId}:${worldSessionId}`
}

/**
 * Toggles a node in the expanded set. Servers and the unlinked group are expanded by default, so
 * their collapsed state is stored as `!<id>`.
 */
export function toggleExpanded(expandedIds: ReadonlySet<string>, node: ResourceTreeNode, expanded = !node.expanded): Set<string> {
  const next = new Set(expandedIds)
  if (node.kind === 'server' || node.kind === 'unlinked') {
    if (expanded)
      next.delete(`!${node.id}`)
    else
      next.add(`!${node.id}`)
  }
  else if (expanded) {
    next.add(node.id)
  }
  else {
    next.delete(node.id)
  }
  return next
}

export function unlinkedNodeId(serverInstanceId: string): string {
  return `unlinked:${serverInstanceId}`
}

function compareStart(left: string | undefined, right: string | undefined, sort: ResourceSort): number {
  const leftTime = left ? Date.parse(left) : Number.NaN
  const rightTime = right ? Date.parse(right) : Number.NaN
  const leftValue = Number.isFinite(leftTime) ? leftTime : 0
  const rightValue = Number.isFinite(rightTime) ? rightTime : 0
  return sort === 'oldest' ? leftValue - rightValue : rightValue - leftValue
}

function inTimeRange(range: null | readonly number[], startedAt: string | undefined): boolean {
  if (range?.length !== 2)
    return true
  const time = startedAt ? Date.parse(startedAt) : Number.NaN
  return Number.isFinite(time) && time >= range[0]! && time < range[1]!
}

function matchesQuery(query: string, values: readonly (string | undefined)[]): boolean {
  if (!query)
    return true
  return values.some(value => value?.toLowerCase().includes(query))
}

function passesPlayer(filter: ResourceTreeFilter, replay: RecorderMinecraftApiV1Replay): boolean {
  return !filter.playerUuid || replay.playerUuid === filter.playerUuid
}

function playNode(replay: RecorderMinecraftApiV1Replay, depth: number, parentId: string, underSession: boolean): ResourcePlayNode {
  return {
    childIds: [],
    depth,
    expanded: false,
    id: playNodeId(replay.connectionId ?? ''),
    kind: 'play',
    parentId,
    replay,
    underSession,
  }
}

function sortPlays(replays: RecorderMinecraftApiV1Replay[], sort: ResourceSort): RecorderMinecraftApiV1Replay[] {
  return [...replays].sort((left, right) => {
    if (sort === 'name') {
      return (left.playerName ?? '').localeCompare(right.playerName ?? '')
        || compareStart(left.startedAt, right.startedAt, 'oldest')
    }
    return compareStart(left.startedAt, right.startedAt, sort)
  })
}
