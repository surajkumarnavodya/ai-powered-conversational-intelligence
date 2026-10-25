import type { Citation } from './types'

/** Canonicalising, de-duplicating and labelling cited sources.
 *
 * One canonical collection per answer: every surface that shows sources
 * (the answer's own Sources section, the evidence disclosure, the
 * provenance panel) derives from `uniqueSources` rather than filtering
 * `citations` its own way, so two surfaces can never report different
 * counts for the same answer.
 *
 * Kept out of the components so the rules below are directly testable --
 * URL canonicalisation is exactly the kind of logic that looks obvious
 * and is not.
 */

/** Query parameters that identify a *campaign*, not a document.
 *
 * Only ever an explicit list. The tempting rule -- "drop every query
 * parameter" -- silently merges genuinely different articles, because a
 * query string is load-bearing on a great many real sites
 * (`?p=1234`, `?storyId=`, `?v=` on YouTube, `?id=` on wire services).
 * Merging two distinct articles under one citation would attribute a
 * claim to a page that never supported it, so the bias here is firmly
 * toward keeping two entries that might be one, never toward collapsing
 * two that are not.
 */
const TRACKING_PARAMS = new Set([
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_term',
  'utm_content',
  'utm_id',
  'utm_name',
  'utm_reader',
  'gclid',
  'dclid',
  'fbclid',
  'msclkid',
  'mc_cid',
  'mc_eid',
  'igshid',
  'ref_src',
  'ref_url',
  's_cid',
  'cmpid',
  'spm',
  'scid',
  'yclid',
  'twclid',
  'at_medium',
  'at_campaign',
])

export function isUrl(value: string): boolean {
  return /^https?:\/\//i.test(value)
}

/** A stable identity for one cited page.
 *
 * For a URL: scheme-insensitive, host lower-cased with a leading `www.`
 * dropped, tracking parameters removed, remaining parameters **sorted but
 * kept**, and the fragment dropped -- `#section-2` is a position within a
 * document, not a different document.
 *
 * `http` and `https` are deliberately folded together: the same page
 * served over both is one source, and citing it twice because one result
 * used the insecure scheme helps nobody.
 *
 * A trailing slash is normalised away only on a path that is otherwise
 * non-empty, so `example.com/` and `example.com` agree without `/a/b/`
 * and `/a/b` being treated as possibly-distinct.
 *
 * Anything that is not a parseable URL (a stored document's filename, or
 * a malformed string) falls back to its `document_id` where there is one,
 * and otherwise to the raw text. A document id is the stronger identity
 * when present, because two chunks of the same PDF share it.
 */
export function canonicalSourceKey(citation: Citation): string {
  const raw = citation.filename ?? ''
  if (!isUrl(raw)) {
    return citation.document_id || raw
  }
  try {
    const url = new URL(raw)
    const host = url.hostname.toLowerCase().replace(/^www\./, '')
    const params = new URLSearchParams(url.search)
    for (const name of [...params.keys()]) {
      // Case-insensitive: real URLs carry `UTM_Source` too.
      if (TRACKING_PARAMS.has(name.toLowerCase())) params.delete(name)
    }
    params.sort()
    const query = params.toString()
    let path = url.pathname
    if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1)
    // Port kept: a different port is a different service.
    const port = url.port ? `:${url.port}` : ''
    return `${host}${port}${path}${query ? `?${query}` : ''}`
  } catch {
    // An unparseable but http-prefixed string: compare it literally
    // rather than guessing at its structure.
    return raw
  }
}

/** The display domain for a cited URL, or `null` when there is none.
 *
 * Parsed from the URL, never invented. A non-URL citation (a stored
 * document) has no domain and returns `null` rather than a placeholder.
 */
export function sourceDomain(citation: Citation): string | null {
  const raw = citation.filename ?? ''
  if (!isUrl(raw)) return null
  try {
    return new URL(raw).hostname.toLowerCase().replace(/^www\./, '')
  } catch {
    return null
  }
}

/** What to show as the source's name.
 *
 * The producer's own `title` when it sent one, otherwise the domain, and
 * otherwise the raw filename. **Never a title derived from the URL slug**
 * -- turning `/2026/10/25/clashes-break-out-in-paris` into "Clashes Break
 * Out In Paris" would be fabricating a headline that no source actually
 * carries, which is the one thing the brief rules out. Falling back to
 * the domain says only what is verifiably true.
 */
export function sourceLabel(citation: Citation): string {
  const title = (citation.title ?? '').trim()
  if (title) return title
  return sourceDomain(citation) ?? citation.filename
}

/** The canonical, de-duplicated source list for one answer, in first-seen
 * order -- which is the order the answer's own inline citation numbers
 * follow, so `[1]` is always the first source listed.
 *
 * When two citations collapse to one key, the one carrying a real title
 * wins, so a duplicate stripped of its title cannot hide a good label.
 */
export function uniqueSources(citations: readonly Citation[]): Citation[] {
  const byKey = new Map<string, Citation>()
  for (const citation of citations) {
    const key = canonicalSourceKey(citation)
    const existing = byKey.get(key)
    if (!existing) {
      byKey.set(key, citation)
      continue
    }
    const existingTitle = (existing.title ?? '').trim()
    const incomingTitle = (citation.title ?? '').trim()
    if (!existingTitle && incomingTitle) byKey.set(key, citation)
  }
  return [...byKey.values()]
}

/** Index (1-based) of each canonical source, for inline `[n]` markers. */
export function sourceNumbers(citations: readonly Citation[]): Map<string, number> {
  const numbers = new Map<string, number>()
  uniqueSources(citations).forEach((citation, index) => {
    numbers.set(canonicalSourceKey(citation), index + 1)
  })
  return numbers
}
