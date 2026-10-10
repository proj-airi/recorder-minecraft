export type EvidenceFetch = (url: string, init?: { signal?: AbortSignal }) => Promise<Response>

/** Raised when an evidence file does not exist. Callers show an empty state, not an error. */
export class EvidenceFileMissingError extends Error {
  constructor(readonly url: string) {
    super(`Evidence file not found: ${url}`)
    this.name = 'EvidenceFileMissingError'
  }
}

/** Message of a thrown value, for status text. */
export function errorText(caught: unknown): string {
  if (typeof caught === 'object' && caught !== null && 'message' in caught && typeof caught.message === 'string')
    return caught.message
  return String(caught)
}

export function isMissingFile(error: unknown): error is EvidenceFileMissingError {
  return error instanceof EvidenceFileMissingError
}

/**
 * Streams a JSONL file and calls `onRecord` with every parsed line in order. The body is decoded
 * chunk by chunk, so memory holds one chunk plus one partial line, never the whole file.
 *
 * Every `yieldEvery` lines the loop yields to the event loop so long files do not block input.
 */
export async function readJsonl(
  url: string,
  onRecord: (record: unknown, lineNumber: number) => void,
  options: { fetch?: EvidenceFetch, signal?: AbortSignal, yieldEvery?: number } = {},
): Promise<void> {
  const fetcher = options.fetch ?? ((input, init) => fetch(input, init))
  const response = await fetcher(url, { signal: options.signal })
  if (response.status === 404 || response.status === 410)
    throw new EvidenceFileMissingError(url)
  if (!response.ok)
    throw new Error(`Request for ${url} failed with status ${response.status}`)

  const yieldEvery = options.yieldEvery ?? 0
  let lineNumber = 0
  let pending = ''

  async function emit(line: string): Promise<void> {
    lineNumber += 1
    const trimmed = line.trim()
    if (!trimmed)
      return
    let parsed: unknown
    try {
      parsed = JSON.parse(trimmed)
    }
    catch {
      throw new Error(`${url}: line ${lineNumber} is not valid JSON`)
    }
    onRecord(parsed, lineNumber)
    if (yieldEvery > 0 && lineNumber % yieldEvery === 0)
      await new Promise<void>(resolve => setTimeout(resolve, 0))
  }

  if (!response.body) {
    for (const line of (await response.text()).split('\n'))
      await emit(line)
    return
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  try {
    for (;;) {
      options.signal?.throwIfAborted()
      const { done, value } = await reader.read()
      if (done)
        break
      pending += value
      let start = 0
      let newline = pending.indexOf('\n', start)
      while (newline >= 0) {
        await emit(pending.slice(start, newline))
        start = newline + 1
        newline = pending.indexOf('\n', start)
      }
      pending = pending.slice(start)
    }
    if (pending)
      await emit(pending)
  }
  finally {
    reader.releaseLock()
  }
}
