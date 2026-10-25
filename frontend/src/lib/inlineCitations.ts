import { canonicalSourceKey, sourceNumbers } from './citations'
import type { Citation } from './types'

/** Rewrites the long inline markdown links a source answer carries into
 * compact `[n]` markers that point at the answer's canonical source list.
 *
 * The model is asked to cite each non-obvious claim inline, as a markdown
 * link under the source's own title:
 *
 * ```
 * ...spread to five cities ([Le Monde](https://www.lemonde.fr/...)).
 * ```
 *
 * which is the right *information* and the wrong *shape* for a dense
 * answer -- a full publisher name mid-sentence, several times a
 * paragraph, is most of the clutter in a web answer. This turns each one
 * into `[2]`, numbered by position in the same de-duplicated list the
 * Sources section renders, so a marker and the list cannot disagree.
 *
 * **It can never invent a citation.** The number comes from matching the
 * link's own href against the answer's citations by canonical key; a link
 * to a page that is *not* cited is left exactly as the model wrote it,
 * rather than being assigned a number it has no right to. That is the
 * conservative direction: an un-numbered real link is a cosmetic miss,
 * whereas numbering an uncited URL would point the reader at a source
 * that never supported the claim.
 *
 * Two links to the same page get the same number, which is the whole
 * point of canonicalising first.
 *
 * Returns the text unchanged when there are no citations, so a
 * no-citation answer (the insufficient-information fallback, or a policy
 * answer whose material was withheld) is never touched.
 */

/** Inline markdown links: `[label](href)`, with an optional title part.
 *
 * Deliberately does not match `![alt](src)` images -- the preceding `!`
 * is excluded -- and does not attempt to handle a label containing
 * unbalanced brackets, which markdown itself does not support either.
 */
const MARKDOWN_LINK_RE = /(!?)\[([^\]]*)\]\((\s*<?)([^)\s<>]+)(>?[^)]*)\)/g

/** True when the text already uses bare `[n]` markers of its own.
 *
 * A model that wrote its own numbered markers has its own numbering,
 * which this function cannot verify against the source list -- renumbering
 * around it would risk two different meanings for `[1]` in one answer.
 * Detected so the caller can leave such an answer alone.
 */
export function hasOwnNumericMarkers(text: string): boolean {
  // `[12]` standing on its own: not preceded by `!` (an image), by `]`,
  // or by `[`, and not followed by `(`. The `[` exclusion is what keeps
  // `[[1]](url)` -- a link whose label is a marker this module already
  // produced -- from reading as the model's own numbering, which would
  // make the rewrite non-idempotent.
  return /(^|[^!\][])\[\d{1,2}\](?!\()/.test(text)
}

export interface InlineCitationResult {
  /** The answer text with matched links rewritten to `[n]`. */
  text: string
  /** 1-based numbers actually referenced by the text, in ascending order
   * -- lets a caller show which sources the prose really leans on. */
  citedNumbers: number[]
}

export function withNumberedCitations(
  answer: string,
  citations: readonly Citation[],
): InlineCitationResult {
  if (!answer || citations.length === 0) {
    return { text: answer, citedNumbers: [] }
  }

  const numbers = sourceNumbers(citations)
  const used = new Set<number>()

  const text = answer.replace(
    MARKDOWN_LINK_RE,
    (match, bang: string, label: string, open: string, href: string, tail: string) => {
      // An image, not a citation.
      if (bang) return match
      // Already a compact marker (idempotence: this function may run on
      // text it has produced before, e.g. a re-render of a saved turn).
      if (/^\[\d{1,2}\]$/.test(label.trim())) return match

      const number = numbers.get(canonicalSourceKey({ filename: href } as Citation))
      // Not one of this answer's cited sources -- leave the author's own
      // link untouched rather than numbering something uncited.
      if (number === undefined) return match

      used.add(number)
      return `[[${number}]](${open}${href}${tail})`
    },
  )

  return { text, citedNumbers: [...used].sort((a, b) => a - b) }
}
