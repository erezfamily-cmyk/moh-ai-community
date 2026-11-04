# Moh AI — Apple-style interface source review

Reviewed 2026-11-04. Scope: static inspection of `/workspace/sites/moh-ai-live/dist/index.html`, `style.css`, `apple-design.css`, `app.js`, and `core.js`. No browser, assistive-technology, device, zoom, or computed-layout checks were performed by this reviewer. Findings refer to the source snapshot inspected; later parent-agent fixes require verification.

Reference: the installed `apple-design` skill at `/root/.codex/skills/remote-skills/apple-design/SKILL.md`. This is community MIT guidance attributed to Emil Kowalski, drawing on Apple design talks; applying it does not imply Apple endorsement or certification. The Moh AI green identity is retained.

## What works in the inspected source

- System typography, stronger text hierarchy, restrained shadows, green primary actions, and grouped source/editor/review workflows support clarity without changing the brand.
- Buttons provide press feedback through `:active`; transitions are short and do not lock input. There are no gesture-driven animations that require a spring or velocity handoff.
- HTML declares Hebrew and RTL; form fields use wrapping labels or explicit labels; icon-only settings/close controls have accessible labels. Native buttons, links, selects, textareas, details, and modal dialogs preserve useful platform behavior.
- The four-item mobile bottom navigation has text labels, approximately 59.2px minimum target height at the default 16px root size, equal-width columns, and `aria-current="page"` on matching routes. A mobile topbar settings button preserves access to settings and backup after the desktop sidebar tools are hidden.
- Mobile footer padding reserves about 104px plus the bottom safe-area inset; the fixed navigation sits at least 12px above the bottom. This provides source-level allowance to scroll the last content clear of the navigation. Actual safe-area behavior, landscape, text scaling, and software-keyboard overlap still need device/browser checks. The viewport declaration does not include `viewport-fit=cover`.
- Inputs and textareas become 16px on mobile; main grids collapse to one column, source URLs truncate, and action rows wrap. These are sound source-level responsive provisions, not proof that all screens fit at 320px or high zoom.
- `prefers-reduced-motion: reduce` disables animation, transition, and smooth scrolling and removes pressed-scale transforms. This is valid CSS and yields static feedback; no JavaScript spring/animation loop bypasses it. The dialog arrival animation is disabled correctly.
- Editor title, text, source, and visual edits mark `unsaved`; in-app navigation requests confirmation, hash changes are blocked while dirty, and `beforeunload` requests the browser's native warning. Unsaved content is excluded from approval. Review/approval states stay attached to a version.

## Concrete findings and minimal fixes

| Priority | Finding in inspected source | Minimal correction |
| --- | --- | --- |
| High | `style.css` sets `.post-editor:focus{outline:0}`. The post textarea has no replacement focus ring. | Add a clear `.post-editor:focus-visible` outline or a visibly focused surrounding editor container. |
| High | `render()` replaces `#main` but never focuses its heading/main region; keyboard actions originating inside replaced content can lose their position. Main already has `tabindex="-1"`. | On route changes, focus main or the page heading after rendering. Do not force focus during initial paint or while the user types. Verify save/review rerenders have a sensible focus destination. |
| High | `saveEditor()` sets `unsaved=false` before `commit()` returns. If localStorage fails it rerenders the editor with “this version is saved,” while unload protection has been cleared. `commit()` reports the error separately, but the editor status contradicts it. | Track a persistence-failure state independently; keep a visible unsaved warning and unload protection until successful persistence or an explicit recovery. Do not render a saved message after a failed commit. |
| Medium | `#voice-examples` has no input dirty tracking. Navigating away loses edited examples without the protection used by the editor. Source/review dialog cancellation also discards form edits immediately. | Add dirty tracking for voice examples and a small discard guard for nonempty changed dialog forms when dismissal would lose meaningful work. Keep normal empty-form dismissal immediate. |
| Medium | All three native dialogs have headings but no `aria-labelledby` or `aria-label`. | Give each heading an ID and reference it with `aria-labelledby` on its dialog. Native `showModal()` already supplies modality; retain native Escape/focus behavior and verify focus return. |
| Medium | Editor section buttons change only CSS `.active`; their selected state is not programmatically exposed. Source/history disclosure buttons lack expanded state. | Either implement the complete tab pattern (tablist/tab/tabpanel, selection, IDs, keyboard navigation) or retain ordinary buttons and add `aria-pressed` plus `aria-controls`. Add `aria-expanded` and `aria-controls` to disclosure buttons and update them with hidden state. |
| Medium | Several targets remain below the requested 44px height: `.button.small` and `.link-button` default to 40px; desktop sidebar tools, tabs, and summaries are about 41.6px. Mobile overrides cover toolbar/source/heading controls, but not every link button, draft-card continuation, or small action. | Set shared interactive target minimum height to `2.75rem` (44px at the default root size), then verify actual dimensions and spacing. Check the mobile settings icon's actual width: only height is guaranteed. This is a 44px design-target finding, not an assertion that every smaller control fails WCAG. |
| Medium | `prefers-reduced-transparency` removes blur from dialog surfaces but misses `dialog::backdrop`, which retains blur. The high-contrast override similarly does not remove the backdrop blur and omits the WebKit-prefixed filter. | In both relevant media queries, explicitly set backdrop filters, including `-webkit-backdrop-filter`, to `none` on `dialog::backdrop`; use an opaque or suitably weighted static scrim. |
| Medium | Inactive editor tabs and muted breadcrumb text retain low-contrast inherited colors. See calculations below. | Darken inactive tab text (for example `#63705f`) and breadcrumb text (for example `#59675f`). Verify final composited colors on translucent chrome. |
| Low | Navigation icon glyphs are exposed alongside their visible labels; source/history arrows do not communicate expanded state. | Mark purely decorative icon spans `aria-hidden="true"`; use programmatic disclosure state as above. |

## Representative contrast calculations

Ratios use the WCAG relative-luminance formula and the declared foreground/background hex colors. They are static samples, not a full WCAG audit. Translucent surfaces depend on content underneath; their actual composited contrast must be checked in browser. The mobile navigation example below uses its nominal pale surface without alpha compositing.

| Use | Foreground | Background | Ratio | Interpretation for ordinary text |
| --- | --- | --- | --- | --- |
| Primary action | `#ffffff` | `#174d3b` | 9.72:1 | Strong |
| Main text | `#1e2c29` | `#f5f6f3` | 13.37:1 | Strong |
| Muted paragraph | `#63705f` | `#f5f6f3` | 4.82:1 | Above 4.5:1 |
| Inactive editor tab | `#828c7a` | `#ffffff` | 3.51:1 | Below 4.5:1 at its ordinary small text size |
| Muted breadcrumb | `#91998e` | `#fafcf8` | 2.84:1 | Below 4.5:1; actual translucent composition varies |
| Mobile nav nominal surface | `#3f5a36` | `#f0f6eb` | 7.00:1 | Strong nominal pairing |
| Yellow badge | `#7a682f` | `#f3ecd6` | 4.62:1 | Above 4.5:1 |
| Feed failure note | `#886e38` | `#ffffff` | 4.85:1 | Above 4.5:1 |

## Follow-up verification owned by the parent agent

Check desktop and narrow mobile routes; keyboard focus through route changes, save rerenders, and dialogs; 320px width and enlarged text; last-content visibility above fixed navigation; touch target bounds; dirty navigation and simulated localStorage failures; reduced motion, reduced transparency, and increased contrast. Inspect the PNG separately: canvas has a generic accessible label, while its full editable text is available in the adjacent native controls. This review does not certify the exported image's small-text legibility.
