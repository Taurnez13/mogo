# antislop audit 001, follow-up, 2026-11-10

All 16 findings were approved ("fix all"). What changed, by finding:

1. R-02: all 41 em dashes removed. Page titles use `|`; empty data cells say "not set"; select
   options use `code: label`; prose uses commas, colons or parentheses.
2. R-03: every table sits in a `.scroll` wrapper; below 900 px the table keeps a 760 px minimum
   width and scrolls inside it. Measured on a 390 px viewport: `scrollWidth > clientWidth` is false
   on the titles and title-detail pages.
3. R-03: controls have a 36 px minimum height on desktop and 44 px under `(pointer: coarse)`.
   Measured smallest `.btn/.chip/.nav a` height on the touch viewport: 44 px.
4. R-25: `--ink-3` is now #5c6770 (5.58:1 on paper, 5.12:1 on the sidebar, 5.79:1 on white);
   amber is #8a5407 (6.0:1). Placeholder text uses the same token.
5. R-27: a 7-line script in `base.html` marks the pressed button `aria-busy`, swaps its label for a
   `data-working` message ("Generating and validating…", "Downloading covers, this can take a few
   minutes…") and blocks a second submit of the same form.
6. R-37/R-31: `DESIGN.md` written: audience, dials (ENERGY 1 / RHYTHM 1 / MOTION 1), palette
   with reasons, typography with reasons, layout, identity motif, component purposes, copy rules.
7. R-39: `cursor: pointer` on `a[href], button, summary, select, label[for], checkbox, file, .chip`;
   `button:disabled` gets `cursor: not-allowed` and 45% opacity; busy buttons show `progress`.
8. R-39: cover upload has a visible label ("Replace cover (JPEG or PNG)"); every `<h3>` inside a
   tab or card is now `<h2 class="h3">`, so no page skips from h1 to h3.
9. R-39: sidebar brand is a `<header>`, nav has `aria-label="Main"`, each page ends with a `<footer>`.
10. R-40: list thumbnails carry `width="34" height="48"`; the cover image `width="260" height="369"`
    plus the existing CSS `aspect-ratio`.
11. R-06: typography reasons recorded in `DESIGN.md`.
12. R-09/R-11: pill-vs-square reason recorded in `DESIGN.md` ("pill = toggle, square = action").
13. Liveliness: the ledger rule (1 px ink line) now runs under every `h2` as well as every table
    header, so one gesture repeats from the brand block through every page. The brand dot stays
    as the only decorative accent. Recorded as the identity motif in `DESIGN.md`.
14. R-24: navigation renders only when signed in; login and setup show the brand block alone.
15. R-27: empty states added to the contributors list (with and without a query) and the series list.
16. Copy: "Only these formats (ONIX codes separated by spaces; leave blank for all)" and "Mark
    these records as sent, so the next 'changed since last export' starts from now".

Also found while fixing: a footer link to a repository URL that does not exist yet was removed
before it shipped (R-24/R-38). On phones the sidebar nav now wraps into a compact row so the
content starts within the first screen.

## Delivery Gate

Block 1, Hard Gate (all no):
- R-02 PASS: `grep -c "—"` over templates and `web.py` returns 0.
- R-03 PASS: no horizontal overflow at 390 px on titles, detail, settings; smallest tap target 44 px.
- R-17/R-18/R-36/R-38 PASS: every number on screen is a live database count; no testimonials, no claims; the only placeholder content is the clearly fictional `examples/sample_onix30.xml`.
- R-23 PASS: no logos, avatars or images were generated; the brand is set as text.
- R-24 PASS: 10 nav links, each opened: Overview, Titles, Contributors, Series, ONIX export, ONIX import, Cin7 & Shopify, Settings, API (/api/docs), Sign out.
- R-25 PASS: lowest text contrast is 5.12:1 (sidebar helper text); large serif headings well above 3:1.
- R-26 PASS: every button is inside a form that posts to an existing route; the 4 disabled sync buttons are disabled because no credentials are set, with the reason shown beside them.
- R-27 PASS: empty states on 7 lists, busy state on every submit, error state via the flash banner (shown in red on failure).
- R-28 PASS: no FAQ.
- R-32 PASS: Tab twice from the overview lands on "Titles" and Enter opens it; `:focus-visible` outline present; no `outline: none`.
- R-33 PASS: all changes made in source.
- R-34 PASS: single theme.
- R-35 PASS: ran the app and clicked through: setup -> /, ONIX import -> "4 created", edit RRP -> "Saved 1 change", link related -> 2 rows, export -> "Schema valid", add user -> "Added user editor1", sign out -> /login; console errors: none.
- R-37 PASS: `DESIGN.md` exists with dials declared.
- R-39 PASS: cursors set in the base layer, disabled state distinct, every field labelled, every image has `alt`, `<html lang="en">`, unique `<title>` per page, header/nav/main/footer landmarks, one h1, no skipped levels, viewport allows zoom.
- R-40 PASS: images have dimensions; no third-party scripts; the only script is inline at the end of body; no performance number reported (not measured).

Block 2, Purpose-Gate (all no): no gradients, glow, blur, background pattern, shadows, arrows,
badges, animation or illustration are used. Typeface reasons written (R-06). Pill chips have a
written reason (R-09/R-11).

Block 3, Liveliness (all yes): dials declared in `DESIGN.md`; output matches ENERGY 1 / RHYTHM 1 /
MOTION 1; one focal point per screen (the page heading and primary action); whitespace is
structural; one accent (oxblood) used only for primary action, brand dot and active tab; identity
motif is the ledger rule; Design Read declared at the top of `DESIGN.md`.

Block 4, Craftsmanship and Quality Locks (all no): no AI-default decisions remain unexplained;
no dead controls; no template sections; no state, breakpoint or keyboard failure found; no
fabricated claims; not a landing-page template; radius varies on purpose; CTAs are specific;
no buzzwords; palette is ink + one accent + status colours; not a clone; every major decision
has a one-line reason in `DESIGN.md`.

Result: PASS.
