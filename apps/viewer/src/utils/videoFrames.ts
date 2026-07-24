export interface VideoFrameMetadataLike {
  mediaTime: number
}

export interface VideoFrameSource {
  cancelVideoFrameCallback?: (handle: number) => void
  readonly ended: boolean
  readonly paused: boolean
  requestVideoFrameCallback?: (
    callback: (now: number, metadata: VideoFrameMetadataLike) => void,
  ) => number
}

export function startVideoFrameLoop(
  source: VideoFrameSource,
  onFrame: (mediaTime: number) => void,
): () => void {
  const requestFrame = source.requestVideoFrameCallback?.bind(source)
  if (!requestFrame) {
    return () => {}
  }

  let callbackHandle: null | number = null
  let stopped = false
  const requestNext = () => {
    callbackHandle = requestFrame((_now, metadata) => {
      callbackHandle = null
      if (stopped) {
        return
      }
      onFrame(metadata.mediaTime)
      if (!source.paused && !source.ended) {
        requestNext()
      }
    })
  }
  requestNext()

  return () => {
    stopped = true
    if (callbackHandle !== null) {
      source.cancelVideoFrameCallback?.(callbackHandle)
      callbackHandle = null
    }
  }
}

export function supportsVideoFrameCallbacks(source: VideoFrameSource): boolean {
  return typeof source.requestVideoFrameCallback === 'function'
}
