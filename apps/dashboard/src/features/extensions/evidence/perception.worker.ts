/// <reference lib="webworker" />

import type { PerceptionWorkerRequest, PerceptionWorkerResponse } from './perceptionWorkerProtocol'

import { errorText, isMissingFile, readJsonl } from './jsonl'
import { PerceptionSummarizer } from './perception'

// Streams and folds one perception.jsonl off the main thread. A 1-tick file of a long Play has
// tens of thousands of lines; only the compact run summary crosses back to the page.
globalThis.addEventListener('message', (event: MessageEvent<PerceptionWorkerRequest>) => {
  const { id, url } = event.data
  const summarizer = new PerceptionSummarizer()
  readJsonl(url, record => summarizer.push(record))
    .then(() => {
      const response: PerceptionWorkerResponse = { id, summary: summarizer.finish(), type: 'done' }
      globalThis.postMessage(response)
    })
    .catch((caught: unknown) => {
      const response: PerceptionWorkerResponse = {
        id,
        message: errorText(caught),
        missing: isMissingFile(caught),
        type: 'error',
      }
      globalThis.postMessage(response)
    })
})
