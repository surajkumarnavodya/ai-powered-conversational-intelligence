import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from '@/components/ui/markdown'

/** Injection regressions for the one component that renders text this app
 * did not write.
 *
 * ## Why this file exists
 *
 * `Markdown` is what displays web-search answers, document and policy RAG
 * answers, multi-source synthesized answers and insight text. Every one of
 * those is untrusted data by CLAUDE.md 5.5, and its own docstring says the
 * HTML-stripping default is "the correct one here, not an oversight".
 *
 * Phase 9 found that claim had **no test behind it anywhere in the
 * repository** -- no `markdown.test.tsx`, and no test in `frontend/src`
 * exercising a `javascript:` URL. The safety rested entirely on
 * `react-markdown`'s defaults, which are a dependency's behaviour, not
 * this project's. A minor-version bump that enabled raw HTML, or a later
 * edit adding `rehype-raw` "to fix a rendering nit", would have been
 * caught by nothing.
 *
 * So these assert the three properties the component relies on, as
 * attacks rather than happy paths:
 *
 *  1. raw HTML in the source renders as inert text, not markup;
 *  2. an event-handler attribute never reaches the DOM;
 *  3. a `javascript:`/`data:` URL never becomes a navigable `href`.
 *
 * Each is checked against the rendered DOM, not against the markdown
 * string, because the string is not what a reader clicks.
 */
describe('Markdown renders untrusted text as data, never as markup', () => {
  it('renders a script tag as text rather than injecting it', () => {
    const { container } = render(
      <Markdown>{'Findings: <script>window.__pwned = true</script> done.'}</Markdown>,
    )
    expect(container.querySelector('script')).toBeNull()
    // And it is not silently dropped either -- a reader should be able to
    // see that the source contained something odd.
    expect(container.textContent).toContain('window.__pwned')
  })

  it('does not let an inline handler attribute reach the DOM', () => {
    const { container } = render(
      <Markdown>{'<img src="x" onerror="window.__pwned = true">'}</Markdown>,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
  })

  it('renders an iframe as text, so an embedded frame cannot load', () => {
    const { container } = render(
      <Markdown>{'<iframe src="https://example.invalid/evil"></iframe>'}</Markdown>,
    )
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('refuses a javascript: link target', () => {
    const { container } = render(
      <Markdown>{'[Click for your report](javascript:window.__pwned=true)'}</Markdown>,
    )
    // Measured 2026-10-30: react-markdown's default `urlTransform` empties
    // the href rather than removing the anchor. Queried by element and not
    // by role on purpose -- an `<a>` with an empty href has no `link` role
    // at all, which is itself the property worth asserting: there is
    // nothing left for a reader to activate.
    const anchor = container.querySelector('a')
    expect(anchor).not.toBeNull()
    expect(anchor?.getAttribute('href')).toBe('')
    expect(anchor?.getAttribute('href')).not.toMatch(/^javascript:/i)
    expect(screen.queryByRole('link', { name: 'Click for your report' })).toBeNull()
    // The text survives, so the reader still sees what the source claimed.
    expect(container.textContent).toContain('Click for your report')
  })

  it('refuses a data: link target', () => {
    const { container } = render(
      <Markdown>
        {'[Download](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)'}
      </Markdown>,
    )
    const anchor = container.querySelector('a')
    expect(anchor?.getAttribute('href')).toBe('')
    expect(anchor?.getAttribute('href')).not.toMatch(/^data:/i)
  })

  it('keeps an ordinary https link working, so the guard is not a blanket block', () => {
    // Without this, every assertion above would also pass if links were
    // broken outright -- which would be its own defect, since a web-search
    // answer's citations are the point of showing it.
    render(<Markdown>{'[Reuters](https://example.com/article)'}</Markdown>)
    const link = screen.getByRole('link', { name: 'Reuters' })
    expect(link).toHaveAttribute('href', 'https://example.com/article')
    // Opened in a new tab without handing the opener over.
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('still renders the markdown it is there to render', () => {
    render(<Markdown>{'**bold** and a list:\n\n- one\n- two'}</Markdown>)
    expect(screen.getByText('bold').tagName).toBe('STRONG')
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })
})
