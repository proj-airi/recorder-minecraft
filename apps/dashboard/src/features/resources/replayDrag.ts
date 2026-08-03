export const REPLAY_DRAG_MIME = 'application/x-recorder-minecraft-replay'

export function readDraggedReplayId(dataTransfer: DataTransfer): null | string {
  const connectionId = dataTransfer.getData(REPLAY_DRAG_MIME).trim()
  return connectionId || null
}

export function writeDraggedReplayId(dataTransfer: DataTransfer, connectionId: string): void {
  dataTransfer.effectAllowed = 'copy'
  dataTransfer.setData(REPLAY_DRAG_MIME, connectionId)
  dataTransfer.setData('text/plain', connectionId)
}
