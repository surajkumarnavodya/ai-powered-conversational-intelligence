/** Saving an answer as a markdown file.
 *
 * The app could already get an answer *out* two ways -- `CopyAnswerButton`
 * puts the markdown on the clipboard, and `DownloadAnswerButton` rasterises
 * it to a PDF via `lib/pdf.tsx`. Neither produces a re-editable file: the
 * clipboard loses the question and holds nothing after the next copy, and
 * a PDF is an image of prose rather than prose.
 *
 * This is deliberately plain. No new dependency, no rendering pipeline, no
 * network call -- the string being written is the same `answerMarkdown`
 * already on screen and already copyable, so there is no disclosure here
 * that the Copy button did not already make.
 */

/** The answer as a standalone markdown document, question included.
 *
 * The question becomes an H1 so the file stands on its own: a bare answer
 * saved to disk loses what was asked, which is most of what makes it
 * worth keeping. Matches what `downloadMarkdownAsPdf` already puts at the
 * top of the PDF, so the two exports are the same document in two formats
 * rather than two different documents.
 *
 * The question is written as plain text on purpose. It is the person's own
 * input and may contain markdown characters; escaping it would be wrong
 * (they would see backslashes) and interpreting it is harmless in a file
 * they asked for, containing only text they already have.
 */
export function buildMarkdownDocument(question: string, answer: string): string {
  const heading = question.trim()
  const body = answer.trim()
  // A trailing newline: POSIX text convention, and it stops the last line
  // being flagged as incomplete by diff tools and editors.
  return heading ? `# ${heading}\n\n${body}\n` : `${body}\n`
}

/** A filesystem-safe stem for the download, derived from the question.
 *
 * Shared with `DownloadAnswerButton`'s PDF path so the two exports of one
 * answer land next to each other under the same name -- previously this
 * logic was inline there and would have been copied, giving two owners for
 * one filename (`CLAUDE.md` 4.8).
 */
export function answerFilenameStem(question: string): string {
  return (
    question
      .trim()
      .slice(0, 60)
      .replace(/[^\w\- ]+/g, '')
      .replace(/\s+/g, '-') || 'answer'
  )
}

/** Saves `content` as `filename` via an object URL.
 *
 * The URL is revoked in a `finally`, so an exception thrown by the click
 * (or by a browser that blocks programmatic downloads) cannot leak the
 * blob for the lifetime of the document.
 */
export function downloadTextFile(filename: string, content: string, mimeType: string): void {
  const url = URL.createObjectURL(new Blob([content], { type: mimeType }))
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
  } finally {
    URL.revokeObjectURL(url)
  }
}
