/* SPDX-License-Identifier: MPL-2.0 */
// Recorder addition: shared label fitting for canvas text.

const ELLIPSIS = '…';

/**
 * Returns the longest prefix of `text` (plus an ellipsis when cut) that fits `maxWidth` at the
 * context's current font, or `null` when fewer than `minChars` characters would remain.
 *
 * NOTICE: `fillText(text, x, y, maxWidth)` does not truncate; it scales glyphs horizontally until
 * the string fits, which turned long lane labels such as "observed: Bread x4, Oak Sapling x2 ·
 * world: …" into an unreadable squeezed strip. Truncating keeps the font metrics honest, and the
 * full text stays one hover or click away in the inspector. A label shorter than `minChars` reads
 * as noise, so it is dropped rather than drawn as "o…".
 */
export function fitText(ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D, text: string, maxWidth: number, minChars = 3): null | string {
  if (maxWidth <= 0 || !text) return null;
  if (ctx.measureText(text).width <= maxWidth) return text;
  // Binary search on prefix length; each probe is one measureText of a short string.
  let low = 0;
  let high = text.length - 1;
  while (low < high) {
    const middle = (low + high + 1) >>> 1;
    if (ctx.measureText(text.slice(0, middle) + ELLIPSIS).width <= maxWidth) low = middle;
    else high = middle - 1;
  }
  if (low < Math.min(minChars, text.length)) return null;
  return text.slice(0, low).trimEnd() + ELLIPSIS;
}
