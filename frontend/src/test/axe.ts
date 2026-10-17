import axe, { type AxeResults, type RunOptions } from 'axe-core'

/** Runtime accessibility checking for component tests.
 *
 * **Why `axe-core` directly rather than the `vitest-axe` wrapper.**
 * `vitest-axe` is a thin matcher around this same engine, published at
 * `0.1.0` and last modified in January 2025; `axe-core` is Deque's own
 * engine and ships releases continuously. For a repository that
 * digest-pins its container base images and justifies every CVE ignore
 * individually, depending on the maintained first-party engine and
 * writing the fifteen lines of matcher ourselves is the cheaper
 * long-term position. Dev dependency only -- nothing here reaches the
 * production bundle.
 *
 * **What this does and does not catch, stated plainly.** axe-core
 * evaluates the *rendered* tree, so it finds problems that only appear
 * once components compose: an invalid ARIA attribute, a control that
 * ends up with no accessible name, a broken `aria-labelledby`
 * reference, a duplicated id, required parent/child role relationships.
 * That is genuinely different from the static `jsx-a11y` lint rules,
 * which read one file's JSX and cannot see composition.
 *
 * **It cannot check colour contrast here.** jsdom has no layout or
 * paint, so axe's `color-contrast` rule cannot resolve computed colours
 * and is disabled below rather than left to report a misleading
 * "passed". Contrast remains a gap that only a real browser can close,
 * and `docs/certification/` records it as such -- this helper must not
 * be cited as evidence that contrast was verified.
 */

/** Rules that cannot produce a trustworthy result under jsdom. */
const JSDOM_UNSUPPORTED_RULES = {
  // No layout engine: computed colours are unavailable, so this would
  // report neither a real pass nor a real failure.
  'color-contrast': { enabled: false },
} satisfies RunOptions['rules']

/** Runs axe against `container` and returns the violations.
 *
 * Returns the violations rather than asserting, so a caller can scope
 * what it treats as a failure -- a page-level test may legitimately
 * accept a known, documented issue while still guarding everything
 * else.
 */
export async function findAccessibilityViolations(
  container: Element,
  options: RunOptions = {},
): Promise<AxeResults['violations']> {
  const results = await axe.run(container, {
    ...options,
    rules: { ...JSDOM_UNSUPPORTED_RULES, ...(options.rules ?? {}) },
  })
  return results.violations
}

/** A readable failure message: axe's own default output is verbose and
 * buries the selector that actually needs fixing. */
export function describeViolations(violations: AxeResults['violations']): string {
  return violations
    .map((violation) => {
      const targets = violation.nodes
        .map((node) => node.target.join(' '))
        .slice(0, 5)
        .join(', ')
      return `${violation.id} (${violation.impact ?? 'unknown'}): ${violation.help}\n    at: ${targets}`
    })
    .join('\n  ')
}

/** Asserts `container` has no accessibility violations.
 *
 * Throws with the rule id, impact and offending selector, so a failure
 * names what to fix rather than only that something is wrong.
 */
export async function expectNoAccessibilityViolations(
  container: Element,
  options: RunOptions = {},
): Promise<void> {
  const violations = await findAccessibilityViolations(container, options)
  if (violations.length > 0) {
    throw new Error(
      `Expected no accessibility violations, found ${violations.length}:\n  ` +
        describeViolations(violations),
    )
  }
}
