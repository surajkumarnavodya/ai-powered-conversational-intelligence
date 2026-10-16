# Responsive layout and the conversation reading column

Authoritative for: how wide the conversation is, what shares that width,
and how the shell behaves at each viewport size. Other UI documents
reference this rather than restating it —
[`docs/ui-design-system.md`](ui-design-system.md) owns the token set,
[`docs/navigation-and-actions.md`](navigation-and-actions.md) owns which
control does what, and this file owns the measure and the breakpoints.

Nothing here changes a backend contract, a request path, or a permission
check. It is presentation only, with the same invariant the 2026 UI pass
carried: **a UI-level restriction is never a boundary** — hiding a control
at a viewport size is UX, and the server re-checks every request anyway.

## The reading column

One token, `--chat-column` (`frontend/src/index.css`, **48rem / 768px**),
mapped through Tailwind v4's `--container-*` namespace to the
`max-w-chat` utility. Every conversation surface uses that utility and no
surface hardcodes a measure:

| Surface | File |
|---|---|
| The scrolling turn list and the composer (one shared container) | `src/pages/Chat.tsx` |
| The read-only shared conversation, its composer and its footer | `src/pages/SharedConversation.tsx` |

48rem is the line length prose stays comfortable at, and what the
mainstream AI chat UIs settle on. The previous `max-w-4xl` (56rem) read
as a stretched document on a 1440px-and-wider laptop.

**Data-dense screens deliberately do not use it.** Admin, review,
lineage and dashboard pages keep their own wider containers
(`max-w-5xl`/`max-w-6xl`) — they are tables, not prose, and a 48rem cap
would force horizontal scrolling on content that reads better wide.

### One column, one pair of edges

Inside the chat column, the question bubble, the answer, and the composer
share the same left and right edges. Two inset rules were removed to get
there, and neither should come back:

- The composer's `pl-10`, which pushed it 40px right of centre. On an
  empty conversation — where the box is the only thing on screen — the
  asymmetry was unmissable.
- `TurnCard`/`PendingTurn`'s `pl-10` on the answer, which reserved the
  width of a left-hand avatar. The only avatar in a turn sits to the
  *right* of the question bubble, so the inset lined the answer up with
  nothing.

`src/pages/Chat.test.tsx` pins this: the composer and the turns resolve
to the same `max-w-chat` element and neither carries `pl-10`.

Content genuinely wider than the column scrolls inside its own box
(`.scroll-x` in `ResultsTable`/`ChartDataTable`, `overflow-x-auto`
elsewhere) rather than widening the column or the page.

### The empty conversation

With no turns, the composer centres vertically (`my-auto`) instead of
sitting at the bottom under an empty screen; the first turn switches it
back to `mt-auto`. Also pinned in `Chat.test.tsx`.

## Breakpoints

Tailwind defaults, used as follows. The chat column itself is fluid — it
takes the full width minus its gutter until it reaches 48rem — so these
are about the shell around it, not the measure.

| Width | Behaviour |
|---|---|
| `< sm` (640px) | 12px gutters. The composer's four controls drop to their own right-aligned row beneath the textarea — beside it they left the text ~150px wide, narrow enough that the placeholder itself wrapped. Icon buttons are 36px (`size="icon"` is `h-9 w-9 sm:h-8 sm:w-8`). A message bubble may take 85% of the line. Toasts inset from both edges. Header: tighter padding, the product title truncates. |
| `sm` – `lg` | 24px gutters, 32px icon buttons, bubbles back to 75%, composer controls back on the textarea's row. Sidebar still hidden; `MobileNav`'s drawer is the way into it. |
| `lg` (1024px) | The persistent left sidebar appears (`lg:w-72`, `lg:w-14` collapsed) and `SidebarToggle` becomes available. |
| `xl` (1280px) | `QuestionNavigator`'s right-edge rail appears. It is an overlay in the margin beside the column, and that margin only exists once the viewport exceeds the column plus the rail's own ~52px lane — below `xl` with the sidebar open it would sit on top of the answer text. Below `xl` the questions stay reachable by scrolling and from the conversation list. |

## How this was verified

Measured, not eyeballed. The built bundle was served with a stand-in API
(canned `/navigation`, `/health`, `/models`, `/sources`,
`/attachments/capabilities` and one `/ask/stream` answer) so a real turn —
question bubble, source panels, SQL card, schema context — renders without
Ollama or a database. A script then drove one question at each width and
measured the live DOM.

At 1920, 1440, 1280, 1024, 820, 768, 430, 390, 360 and 320 CSS px:

- `document.documentElement.scrollWidth - clientWidth === 0` — no
  horizontal page overflow at any width.
- No element's box extended past the viewport on either side.
- The composer and the turn reported **identical** width and identical
  left/right gaps at every width — the shared-column rule holding, not
  two containers that happen to agree.

Light and dark were both checked at 1440. Screenshots were reviewed at
1440 (empty and answered), 820 (iPad portrait) and 390 (phone).

**Not covered:** no real device was used, only CSS-pixel viewports in
headless Chrome; touch gestures, on-screen-keyboard resize behaviour,
Safari/iOS-specific viewport units and landscape phone sizes were not
exercised. The harness lives outside the repository — it is a test
fixture, not shipped code, and a future pass would need to rebuild it.
