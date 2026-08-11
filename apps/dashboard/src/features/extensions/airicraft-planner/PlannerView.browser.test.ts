import type { EditorWorkspaceContext, SelectedPlayExtension } from '../../editor/workspaceContext'

import { expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef } from 'vue'

import PlannerView from './PlannerView.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'

it('shows the selected planner call request, result, and timeline anchors', async () => {
  const call = {
    callId: 'call-1',
    model: { name: 'planner-model', provider: 'test-provider' },
    outcome: {
      assistantContent: { text: 'Mine stone' },
      status: 'completed',
      toolCalls: [{ name: 'mine' }],
      usage: { totalTokens: 42 },
    },
    plannerAttempt: { attempt: 2, generation: '7', phase: 'REPAIR' },
    request: { messages: [{ content: 'Continue', role: 'user' }], tools: [{ name: 'mine' }] },
    sequence: '3',
    timeline: {
      applied: { serverTick: '130' },
      completed: { serverTick: '129' },
      submitted: { serverTick: '120' },
    },
    timing: { latencyMs: '450' },
  }
  const selectedExtension = shallowRef<null | SelectedPlayExtension>({
    descriptor: { extensionType: 'airicraft.planner' },
    item: { color: '#8b5cf6', data: call, endServerTick: 129, id: 'call-1', kind: 'interval', label: 'Call 3', startServerTick: 120 },
    placement: {
      connectionId: 'connection',
      endTick: 200,
      id: 'play:connection',
      playEndServerTick: 300,
      playStartServerTick: 100,
      sourceEndServerTick: 300,
      sourceStartServerTick: 100,
      startTick: 0,
    },
    playServerTick: 125,
  })
  const context = { selectedExtension } as unknown as EditorWorkspaceContext
  const Host = defineComponent({
    setup() {
      provide(editorWorkspaceContextKey, context)
      return () => h(PlannerView)
    },
  })
  const screen = await render(Host)

  try {
    await expect.element(screen.getByText('Airicraft planner call 3')).toBeVisible()
    await expect.element(screen.getByText('test-provider / planner-model · generation 7 · attempt 2 · REPAIR')).toBeVisible()
    await expect.element(screen.getByText('125', { exact: true })).toBeVisible()
    await expect.element(screen.getByText('450 ms')).toBeVisible()
    await expect.element(screen.getByText(/Mine stone/)).toBeVisible()
    await expect.element(screen.getByText(/totalTokens/)).toBeVisible()
  }
  finally {
    await screen.unmount()
  }
})
