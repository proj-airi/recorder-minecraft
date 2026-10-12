/**
 * Thrown by a data track `load` when the file it reads does not exist. The track then reports
 * `missing` instead of `empty`, so the lane says which source is absent rather than "No items",
 * which would read as "nothing happened".
 */
export class DataSourceMissingError extends Error {
  constructor(readonly source: string) {
    super(`${source} missing`)
    this.name = 'DataSourceMissingError'
  }
}
