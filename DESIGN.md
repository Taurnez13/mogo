# MOGO design direction

Reading this as: an internal catalogue tool for publishing staff (editors, production, finance),
in a paper-and-ink ledger language, dial ENERGY 1 / RHYTHM 1 / MOTION 1.

## Who it is for and what it has to do

Two or three people at a small press, several times a day, finding one title among a thousand and
fixing a field. Speed of scanning and confidence that an edit saved matter more than delight. The
design should feel like a well-kept card catalogue: quiet, legible, specific to books.

## Dials

- **ENERGY 1.** It says hello softly. One accent colour, no decoration, dense but airy tables.
- **RHYTHM 1.** Every page uses the same skeleton (sidebar, heading, ledger or form). Predictability
  is the point: staff learn it once.
- **MOTION 1.** Hover and focus states only. No entrance animations. Reduced-motion is respected.

## Palette (why these)

| Token | Value | Reason |
|---|---|---|
| paper | #fbfbf9 | Page background. Off-white rather than cream so it reads as paper, not as the warm-cream AI default. |
| paper-2 | #f2f1ec | Sidebar. One step darker, separates chrome from content without a border. |
| ink | #1d2730 | Text and primary buttons. Blue-black like printer's ink; softer than pure black on a light page. |
| ink-2 / ink-3 |  Secondary and tertiary text. Both pass WCAG AA (7.2:1 and 5.5:1 on paper).| Secondary and tertiary text. Both pass WCAG AA (7.2:1 and 5.5:1 on paper). |
| red (accent) | #8a1c2b | Oxblood, the colour of cloth bindings and library stamps. Used only for the primary action, the brand dot and the active-tab mark. |
| green / amber / blue | #2d6a4f / #8a5407 / #2b5c8a | Status semantics only (active / warning / forthcoming), never decoration. |

Neutrals do not count toward the palette; the active palette is ink + one accent.

## Typography (why these)

- **Book titles in a serif** (Iowan Old Style, Palatino, Georgia stack): the objects being
  catalogued are set in serif type, so titles look like titles rather than database strings.
- **Everything else in the system sans**: no web-font download, works on an intranet with no
  internet, and gets out of the way of the content.
- **Mono for identifiers** (ISBNs, codes): fixed width makes a 13-digit number scannable in a column.
- No uppercase tracked labels, no monospace headings.

## Layout

- Fixed 200 px sidebar, content column up to 1240 px, left-aligned throughout. Tables are the
  primary view ("the ledger"); forms are two to four columns at desktop and one on phones.
- Tables sit in a horizontal-scroll wrapper on narrow screens rather than reflowing, because
  staff compare columns.

## Identity motif

The **ledger rule**: a single 1 px ink line under every table header and every section heading,
the same line that separates the brand mark from the navigation. It is the one repeated gesture
and it does structural work (it says "a list begins here").

The brand mark `Taur·nez` carries the only decorative use of the accent, the middle dot.

## Components (purpose written down)

- **Status dot + word**, not a coloured badge: colour alone is not enough, and the dot keeps rows quiet.
- **Filter chips are pills** while all other controls have a 3 px radius: pill means "toggle a
  filter", square means "do an action". That contrast is the reason for the exception.
- **No cards for data.** Cards appear only where a form stands alone (add a description, link a
  related product); lists are tables.
- **Danger zone** is behind a `<details>` so a destructive action takes two deliberate steps.

## Copy

Sentence case. Plain verbs that name the outcome ("Save changes", "Generate ONIX 3.0", "Download
all remaining covers"). Errors say what happened and what to do. Empty states tell you how to fill
them. No marketing voice, no em dashes.
