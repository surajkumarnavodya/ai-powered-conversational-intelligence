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
 * **Three rules cannot produce a trustworthy result here, not one.**
 * jsdom has no layout or paint, and every rule that needs geometry or
 * rendered text is therefore unreliable. `color-contrast` was always
 * disabled below for that reason. Phase 9 measured the other two
 * (2026-10-29), by running axe in real Chrome against the live app and
 * then re-running the same markup here:
 *
 * - `color-contrast`: cannot resolve a computed colour at all.
 * - `target-size` (WCAG 2.2 AA 2.5.8): every element measures 0x0 under
 *   jsdom. Chrome reported a real 16x16 control on both auth screens
 *   (`UI-005`) that no jsdom run could have seen.
 * - `label-content-name-mismatch` (WCAG 2.5.3): this one is the
 *   dangerous shape, because it did not stay silent -- it reported a
 *   **false pass**. Chrome flagged three header controls (`UI-006`);
 *   the identical markup rendered here lands in axe's `passes` list,
 *   because the rule needs *rendered* visible text. A test that asked
 *   axe about it under jsdom would have read as evidence of the
 *   opposite of the truth, which is why it is disabled rather than left
 *   enabled and trusted.
 *
 * All three remain gaps only a real browser can close. The Phase 9
 * driver that closed them for one run is described in
 * `docs/uat-readiness/phase-9-ui.md`; this helper must not be cited as
 * evidence that any of the three was verified.
 */

/** Rules that cannot produce a trustworthy result under jsdom.
 *
 * Disabling beats leaving them on: a disabled rule is visibly absent from
 * the results, whereas an enabled one that cannot evaluate its input
 * reports a pass that means nothing. See this module's docstring for the
 * measurement behind each entry. */
const JSDOM_UNSUPPORTED_RULES = {
  // No layout engine: computed colours are unavailable, so this would
  // report neither a real pass nor a real failure.
  'color-contrast': { enabled: false },
  // No layout engine: every element measures 0x0, so a 16x16 control and a
  // 64x64 one are indistinguishable here.
  'target-size': { enabled: false },
  // Needs *rendered* visible text. Measured 2026-10-29: markup Chrome
  // flagged comes back as a PASS under jsdom, so this rule is actively
  // misleading here rather than merely blind. Check the computed
  // accessible name instead -- `accessibility.browserFindings.test.tsx`
  // shows the pattern.
  'label-content-name-mismatch': { enabled: false },
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
