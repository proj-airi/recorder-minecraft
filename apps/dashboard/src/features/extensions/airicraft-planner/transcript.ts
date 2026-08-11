import type { PlannerCallRecord } from './module'

export interface PlannerContentBlock {
  detail?: string
  kind: 'image' | 'json' | 'text'
  text?: string
  value?: unknown
}

export type PlannerMessageRole = 'assistant' | 'system' | 'tool' | 'unknown' | 'user'

export interface PlannerToolCallView {
  arguments: string
  id?: string
  name: string
}

export interface PlannerTranscriptEntry {
  content: PlannerContentBlock[]
  id: string
  reasoning?: string
  role: PlannerMessageRole
  source: 'request' | 'response'
  toolCallId?: string
  toolCalls: PlannerToolCallView[]
}

export function plannerTranscript(call: PlannerCallRecord): PlannerTranscriptEntry[] {
  const request = (call.request?.messages ?? []).map((message, index) => requestEntry(message, index))
  const response = responseEntry(call)
  return response ? [...request, response] : request
}

function contentBlocks(content: unknown): PlannerContentBlock[] {
  if (typeof content === 'string')
    return content.length > 0 ? [{ kind: 'text', text: content }] : []
  if (content === null || content === undefined)
    return []
  if (!Array.isArray(content))
    return [{ kind: 'json', value: content }]

  return content.flatMap((part): PlannerContentBlock[] => {
    if (!isRecord(part))
      return [{ kind: 'json', value: part }]
    if (part.type === 'text' && typeof part.text === 'string')
      return [{ kind: 'text', text: part.text }]
    if (part.type === 'image_url') {
      const image = isRecord(part.image_url) ? part.image_url : undefined
      return [{ detail: text(image?.detail), kind: 'image' }]
    }
    return [{ kind: 'json', value: part }]
  })
}

function formatArguments(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    }
    catch {
      return value
    }
  }
  return JSON.stringify(value ?? {}, null, 2)
}

function isAssistantMessage(value: unknown): value is Record<string, unknown> {
  return isRecord(value)
    && ('content' in value || 'reasoning_content' in value || 'role' in value || value.__airicraft_openai_message_replay === true)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function messageRole(value: unknown): PlannerMessageRole {
  return value === 'assistant' || value === 'system' || value === 'tool' || value === 'user'
    ? value
    : 'unknown'
}

function requestEntry(message: Record<string, unknown>, index: number): PlannerTranscriptEntry {
  return {
    content: contentBlocks(message.content),
    id: `request-${index}`,
    reasoning: text(message.reasoning_content),
    role: messageRole(message.role),
    source: 'request',
    toolCallId: text(message.tool_call_id),
    toolCalls: toolCalls(message.tool_calls),
  }
}

function responseEntry(call: PlannerCallRecord): PlannerTranscriptEntry | undefined {
  const assistant = call.outcome?.assistantContent
  const message = isAssistantMessage(assistant) ? assistant : undefined
  const content = contentBlocks(message ? message.content : assistant)
  const calls = toolCalls(call.outcome?.toolCalls)
  const reasoning = text(message?.reasoning_content)
  if (content.length === 0 && calls.length === 0 && !reasoning)
    return undefined

  return {
    content,
    id: 'response',
    reasoning,
    role: 'assistant',
    source: 'response',
    toolCalls: calls,
  }
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function toolCalls(value: unknown): PlannerToolCallView[] {
  if (!Array.isArray(value))
    return []

  return value.flatMap((candidate): PlannerToolCallView[] => {
    if (!isRecord(candidate))
      return []
    const fn = isRecord(candidate.function) ? candidate.function : candidate
    const name = text(fn.name)
    if (!name)
      return []
    return [{
      arguments: formatArguments(fn.arguments),
      id: text(candidate.id),
      name,
    }]
  })
}
