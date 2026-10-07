/** Short, single-line navigator labels for a full question (Question
 * Navigator). A pure, display-only transform -- the original question text
 * stored in chat history is never modified; this only ever runs at render
 * time to decide what the compact label shows. */

/** Soft cap: a label is truncated at the last whole word that still fits
 * within this many characters, then given a trailing ellipsis. Short enough
 * to stay single-line in a ~260px-wide compact panel at the app's default
 * text size, long enough to usually still read as a real phrase rather than
 * a fragment. */
export const QUESTION_LABEL_MAX_CHARS = 42

/** Collapses a question to one line of plain text and shortens it to a
 * single short label, breaking at a word boundary (never mid-word) and
 * appending "…". Returns the question unchanged if it already fits.
 *
 * Collapsing whitespace first means a pasted multi-line question becomes
 * one readable line instead of stacking its line breaks into the label. */
export function shortenQuestion(question: string, maxChars: number = QUESTION_LABEL_MAX_CHARS): string {
  const collapsed = question.replace(/\s+/g, ' ').trim()
  if (collapsed.length <= maxChars) return collapsed

  const slice = collapsed.slice(0, maxChars)
  const lastSpace = slice.lastIndexOf(' ')
  // No space at all within the limit (one very long unbroken token) -- cut
  // at the character limit itself rather than producing an empty label.
  const cut = lastSpace > 0 ? slice.slice(0, lastSpace) : slice
  return `${cut.trimEnd()}…`
}
