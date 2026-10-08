# React UI guidelines: keep pages short

Rules for every new or reworked React page; section 2 is the house standard and code and agents cite the rules by number. Kit: `frontend/src/components/{Toggle,Button,Badge,Card,Section,Field}.tsx`, `components/labels.ts` and the design-kit block in `index.css`. Rules 4 and 8 and rules 16-22 come from `docs/design/ui-refresh-spec.md` §2.2 and §2.4.

## 2. Rules

Apply to every new or reworked React page. A reviewer can check each with a yes or no.

1. **Primary action first, and only one.** Each panel has at most one filled/primary button; it is the
   first thing visible, above the options. Secondary actions (Estimate cost, Detect speakers only, Upload
   only) are plain or link buttons, or sit in the options area.
2. **Explain a disabled primary button.** One line under it: "Still needed: an audio or video file."
   Never a silently disabled button.
3. **Sensible defaults, pre-filled.** Every field arrives filled from the drama, the Settings defaults, or
   the API's `defaults` object. A user who changes nothing can press the primary button. Blank means
   "default", never "required".
4. **Advanced, rare and destructive options are collapsed. Common actions and frequently used content
   are visible (rule 16).** Use `<details>` (the shared `Section` component) for what most visits don't
   need. The summary shows the current values in one line
   (e.g. "Advanced: batch 20, context 6/3, no cost cap") so people see the state without opening it.
5. **One line per setting.** Label left (or above on phones), control right, help as tooltip. No setting
   uses two lines except a textarea. Group related controls in a row (Max speed-up and Max slow-down on
   one row).
6. **Short labels, help in a tooltip.** Labels are 1-4 words plus a unit. Everything else goes in a
   `title`/`(?)` help control or inside `<details>`. No inline explanatory paragraphs in the default
   view. Error and blocking messages are the exception and stay inline.
7. **Numbers and units in labels.** "Batch (lines)", "Cost cap ($)", "Start (s)", "Wrap at (chars)",
   "Speed-up (x)". Put the range in the input (`min`/`max`/placeholder), not in the label.
8. **No empty panels.** If a list or table has no rows, render nothing. Panels for Series, Presets, Voice
   bank, Reading history, Notes, TM suggestions, Versions, Line history appear only when non-empty.
   Content used on most visits (Recently active, Reading history) is visible and clickable, not folded
   into a "More" section; rare lists stay folded.
9. **Summaries are one line.** Counts and status go in a single header line ("412 lines, 380 translated,
   12 flagged"), not a list or a paragraph per figure. Show a warning only when something is wrong.
10. **Consistent spacing and controls.** One `Field` component (label + control + optional help) and one
    `Section` component (title, optional summary, optional collapsed body). Same button sizes, number
    input width and gap. No page-local inline `style={{...}}` layouts (Settings has one now).
11. **Keyboard shortcuts on the common path.** Ctrl/Cmd+S saves the current line edit; Alt+Up /
    Alt+Down jump to the previous or next flagged line; Alt+Space plays or pauses (once media playback
    exists). Enter submits single-field forms (search, new drama). Show the shortcut in the tooltip.
12. **Remember last-used choices.** Persist per-viewer conveniences (last engine/model/style/locale, last
    export format and language, last dub engine, last Library filters, open/closed `Advanced`) in
    `localStorage` inside try/catch, falling back to API defaults. Server-owned facts (drama fields) still
    go through the API.
13. **Stage opens where the work is.** Opening a drama lands on its current stage, not always Source.
14. **Few clicks on the common path.** Targets **(inferred, not measured)**: start a translation in 1 click
    from a drama that has transcript lines; export SRT in 2 clicks (Export stage, Export); create a drama
    with 1 field + Enter (title), everything else defaulted.
15. **Do not hide risk to gain brevity.** Destructive actions (force re-translate, delete, reset) keep
    their explicit confirm, but compact (one checkbox on the same line, or a confirm popover), not a
    paragraph. Cost estimates and cap warnings still show before a paid run.
16. **Card or Section.** Use a `Card` (always open) for what people use on most visits: the primary run
    form of a stage, the drama list, Continue reading, API key status, Setup checks, Support report. Use a
    `Section` (`<details>`) for rare, advanced or destructive content: Advanced options, Series, Cost by
    drama, Presets, Voice bank, Backup & storage, Log, Danger zone. A closed Section's summary shows the
    current state in one line, and a Section with nothing in it is not rendered.
17. **Button hierarchy.** Each Card has at most one `.btn-primary`, and it is its main action.
    Alternatives use `.btn-secondary`; quiet or tertiary actions (Estimate cost, Detect speakers only,
    Details, Clear) use `.btn-ghost`; destructive actions use `.btn-danger`, placed last and set apart.
    `.btn-sm` is only for dense rows (list rows, card footers, the Review toolbar) and still gets 44px on
    touch. Legacy `button.primary` and `.badge` are styled like the kit until their screens migrate.
18. **Navigation that acts like a button is a `ButtonLink`.** "Open workspace", "Read", "Go to Source"
    and "Resume" are `a.btn`, not underlined text. Underlined links are only for links inside a sentence;
    `button.link` is retired except inside prose.
19. **A `Toggle` for every boolean setting or option**, inside `Field` (label + optional help); inside
    forms and settings lists use the `.setting-list` rows. Keep `<input type="checkbox">` only for
    selecting items in a list (Library selection, glossary rows) and for acknowledgements ("I understand
    this replaces existing English").
20. **Never render a raw API value.** Status, media type, language code and engine id go through
    `humanize(kind, raw)` (`components/labels.ts`) or `<Badge kind=… value=…/>`, including `<option>`
    labels (the option `value` stays raw). Free-form codes with no label map use `tidy()` /
    `humanizeValue()` from the same file. No ad-hoc `.replace(/_/g, ' ')` in display code.
21. **Badges carry status, type and language.** Status badges use `statusTone`. Colour is never the only
    signal: the pill always has text. Don't put meaning in `Badge title=`, because hover-only text doesn't
    exist on touch.
22. **A disabled primary names the fix, and the fix is one tap away.** This extends rule 2: the "Still
    needed: …" item is a link or ButtonLink to the missing field or stage. No help lives only in
    `title=`; use `Field help` (tap to open) or visible text. Keyboard-shortcut hints may stay in `title`
    because `review/ShortcutSheet.tsx` lists them.

## 6. Limits

- Do not add UI for endpoints that do not exist; keep every existing function reachable and only reorder and collapse.
- Defaults held outside the settings API cannot be shared, so per-browser memory (rule 12) is the fallback.
