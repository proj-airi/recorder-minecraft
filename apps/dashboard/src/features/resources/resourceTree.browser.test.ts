import type { ResourceTreeFilter, ResourceTreeNode } from './resourceTree'

import { describe, expect, it } from 'vitest'

import { catalogFixture } from './fixtures'
import { ancestorsOf, buildResourceTree, toggleExpanded } from './resourceTree'
import { toTimelineWorldSession } from './worldSession'

const filter: ResourceTreeFilter = { query: '', sort: 'newest', timeRange: null }

function outline(nodes: readonly ResourceTreeNode[]): string[] {
  return nodes.map((node) => {
    const indent = '  '.repeat(node.depth)
    switch (node.kind) {
      case 'capture-session':
        return `${indent}capture ${node.sessionId} (${node.plays.length})`
      case 'play':
        return `${indent}play ${node.replay.playerName}`
      case 'server':
        return `${indent}server ${node.server.name}`
      case 'session':
        return `${indent}session ${node.session.sessionId} (${node.plays.length})`
      case 'unlinked':
        return `${indent}unlinked (${node.playCount})`
      default:
        return ''
    }
  })
}

describe('resource tree', () => {
  it('groups servers, world sessions newest first, and Plays without a world session', () => {
    const view = buildResourceTree(catalogFixture(), filter, new Set())
    expect(outline(view.nodes)).toEqual([
      'server Other server',
      '  unlinked (1)',
      '    play frank',
      'server Test server',
      '  session run2-session (1)',
      '  session run1-session (2)',
      '  unlinked (3)',
      '    play eve',
      '    capture cap1 (2)',
    ])
    expect(view).toMatchObject({ playCount: 7, sessionCount: 2 })
  })

  it('expands sessions, collapses servers, and sorts oldest first', () => {
    let expanded: ReadonlySet<string> = new Set()
    const initial = buildResourceTree(catalogFixture(), filter, expanded).nodes
    const run1 = initial.find(node => node.kind === 'session' && node.session.sessionId === 'run1-session')!
    const other = initial.find(node => node.kind === 'server' && node.server.name === 'Other server')!
    expanded = toggleExpanded(toggleExpanded(expanded, run1), other)
    const view = buildResourceTree(catalogFixture(), { ...filter, sort: 'oldest' }, expanded)
    expect(outline(view.nodes)).toEqual([
      'server Other server',
      'server Test server',
      '  session run1-session (2)',
      '    play alice',
      '    play bob',
      '  session run2-session (1)',
      '  unlinked (3)',
      '    capture cap1 (2)',
      '    play eve',
    ])
  })

  it('searches players, servers, and ids and expands the matching branches', () => {
    expect(outline(buildResourceTree(catalogFixture(), { ...filter, query: 'BOB' }, new Set()).nodes)).toEqual([
      'server Test server',
      '  session run1-session (1)',
      '    play bob',
    ])
    // A session id matches the session and keeps all of its Plays.
    expect(outline(buildResourceTree(catalogFixture(), { ...filter, query: 'run1-sess' }, new Set()).nodes)).toEqual([
      'server Test server',
      '  session run1-session (2)',
      '    play alice',
      '    play bob',
    ])
    expect(outline(buildResourceTree(catalogFixture(), { ...filter, query: 'cap1' }, new Set()).nodes)).toEqual([
      'server Test server',
      '  unlinked (2)',
      '    capture cap1 (2)',
      '      play carol',
      '      play dave',
    ])
    expect(buildResourceTree(catalogFixture(), { ...filter, query: 'nobody' }, new Set()).nodes).toEqual([])
    // Player and date filters apply to Plays; sessions without a matching Play disappear.
    expect(outline(buildResourceTree(catalogFixture(), { ...filter, playerUuid: 'uuid-alice' }, new Set()).nodes)).toEqual([
      'server Test server',
      '  session run2-session (1)',
      '  session run1-session (1)',
    ])
    expect(outline(buildResourceTree(catalogFixture(), { ...filter, timeRange: [Date.UTC(2026, 7, 2), Date.UTC(2026, 7, 3)] }, new Set()).nodes)).toEqual([
      'server Test server',
      '  unlinked (2)',
      '    capture cap1 (2)',
    ])
  })

  it('finds the ancestors of a Play to reveal it', () => {
    expect(ancestorsOf(catalogFixture(), filter, 'play:bob-1')?.map(node => node.id)).toEqual([
      'server:server-a',
      'session:server-a:20261010T080003Z--run1',
    ])
    expect(ancestorsOf(catalogFixture(), { ...filter, query: 'alice' }, 'play:bob-1')).toBeNull()
  })

  it('maps a world session to the timeline source, keeping the world start tick', () => {
    const run1 = catalogFixture()[0]!.worldSessions![0]!
    expect(toTimelineWorldSession(run1)).toEqual({
      alignments: [{ name: 'session-alignment', url: '/assets/world/run1/alignments/session-alignment.jsonl' }],
      endedAt: '2026-10-10T08:00:58Z',
      endServerTick: 1106,
      eventsUrl: '/assets/world/20261010T080003Z--run1/world-events.jsonl',
      id: '20261010T080003Z--run1',
      label: 'Oct 10, 08:00:03 · run1-ses',
      metadataUrl: '/assets/world/20261010T080003Z--run1/metadata.json',
      sessionId: 'run1-session',
      startedAt: '2026-10-10T08:00:03Z',
      startServerTick: 0,
    })
  })
})
