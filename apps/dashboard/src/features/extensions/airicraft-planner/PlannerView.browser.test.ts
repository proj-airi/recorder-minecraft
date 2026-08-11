import type { EditorWorkspaceContext, SelectedPlayExtension } from '../../editor/workspaceContext'

import { expect, it } from 'vitest'
import { render } from 'vitest-browser-vue'
import { defineComponent, h, provide, shallowRef } from 'vue'

import PlannerView from './PlannerView.vue'

import { editorWorkspaceContextKey } from '../../editor/workspaceContext'
import { plannerTranscript } from './transcript'

it('shows the selected planner call request, result, and timeline anchors', async () => {
  const call = {
    callId: 'call-1',
    model: { name: 'planner-model', provider: 'test-provider' },
    outcome: {
      assistantContent: {
        content: 'I will mine the nearby stone.',
        reasoning_content: 'The stone is within reach.',
        role: 'assistant',
      },
      status: 'completed',
      toolCalls: [{ function: { arguments: '{"block":"minecraft:stone"}', name: 'mine' }, id: 'tool-2', type: 'function' }],
      usage: { totalTokens: 42 },
    },
    plannerAttempt: { attempt: 2, generation: '7', phase: 'REPAIR' },
    request: {
      messages: [
        { content: 'You control a Minecraft agent.\nFollow the current goal.\nUse available tools safely.\nReport the result when the action is complete.', role: 'system' },
        { content: 'Continue the active goal.', role: 'user' },
        { content: null, role: 'assistant', tool_calls: [{ function: { arguments: '{"radius":4}', name: 'inspect_world' }, id: 'tool-1' }] },
        { content: 'Stone is at 10, 64, 12.', role: 'tool', tool_call_id: 'tool-1' },
      ],
      tools: [{ name: 'mine' }],
    },
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
    const systemMessage = screen.getByText(/You control a Minecraft agent/)
    await expect.element(systemMessage).toHaveClass(/line-clamp-3/)
    await expect.element(screen.getByRole('button', { name: 'Show more' })).toBeVisible()
    await screen.getByRole('button', { name: 'Show more' }).click()
    await expect.element(systemMessage).not.toHaveClass(/line-clamp-3/)
    await expect.element(screen.getByRole('button', { name: 'Show less' })).toBeVisible()
    await expect.element(screen.getByText('Continue the active goal.')).toBeVisible()
    await expect.element(screen.getByText('inspect_world')).toBeVisible()
    await expect.element(screen.getByText('Stone is at 10, 64, 12.')).toBeVisible()
    await expect.element(screen.getByText('I will mine the nearby stone.')).toBeVisible()
    await expect.element(screen.getByText('mine', { exact: true })).toBeVisible()
    await expect.element(screen.getByText(/minecraft:stone/)).toBeVisible()
    await expect.element(screen.getByText('Reasoning')).toBeVisible()
    await expect.element(screen.getByText(/totalTokens/)).not.toBeInTheDocument()
    await screen.getByRole('button', { name: 'Details' }).click()
    await expect.element(screen.getByRole('dialog', { name: 'Call details' })).toBeVisible()
    await expect.element(screen.getByText(/totalTokens/)).toBeVisible()
    await expect.element(screen.getByText('Request tools')).toBeVisible()
    await expect.element(screen.getByText('Timeline anchors')).toBeVisible()
    await screen.getByRole('button', { name: 'Close call details' }).click()
    await expect.element(screen.getByText(/totalTokens/)).not.toBeInTheDocument()
  }
  finally {
    await screen.unmount()
  }
})

it('keeps multimodal inputs and open-ended response objects visible', () => {
  const entries = plannerTranscript({
    callId: 'call-2',
    outcome: { assistantContent: { action: 'mine', target: 'minecraft:stone' } },
    request: {
      messages: [{
        content: [
          { text: 'What is ahead?', type: 'text' },
          { image_url: { detail: 'high', url: 'data:image/png;base64,hidden' }, type: 'image_url' },
        ],
        role: 'user',
      }],
    },
    sequence: '4',
    timeline: { submitted: { serverTick: '140' } },
  })

  expect(entries).toMatchObject([
    {
      content: [
        { kind: 'text', text: 'What is ahead?' },
        { detail: 'high', kind: 'image' },
      ],
      role: 'user',
    },
    {
      content: [{ kind: 'json', value: { action: 'mine', target: 'minecraft:stone' } }],
      role: 'assistant',
      source: 'response',
    },
  ])
})
