# Reading Room — Design System & Build Spec

> Handoff for Claude Code. Build the macOS audiobook app's UI (Tauri + React + TypeScript) in this design language. Import `tokens.css` once at the app root and use only its `--rr-*` variables. Do not hard-code hex values in components. When this file and the tokens disagree, the tokens win.

---

## 1. What the app is

A Mac app that turns the user's own EPUB and Markdown books into audiobooks with text-to-speech that runs locally:

- It splits books into chapters and renders one MP3 per chapter.
- Users can clone narrator voices and cast different voices to characters, with automatic speaker detection they can review and correct.
- Users can fix mispronounced words ("Tricky words") with a short recorded sample.
- Finished books play in a built-in player.

**Audience:** readers and hobbyists, not audio engineers. It should feel like a lovely bookshelf first and a production tool second.

## 2. Design principles

1. **Books are objects.** Spines and covers have crisp corners (3px), soft warm shadows and an occasional slight tilt. Everything else is flat and quiet.
2. **The chrome recedes.** Use warm neutral surfaces, hairlines (1px `--rr-line`), small icons and generous space. Colour comes from book cloth plus one terracotta accent.
3. **One moment of play per screen.** A hanging tag, a tilted cover, a ribbon or a tilted index card. Never more than one or two per screen.
4. **Friendly over technical.** Say "Narrator", "Cast", "Tricky words", "Render" and "Didn't finish". Never say "TTS model", "diarization", "phoneme", "inference" or "sample rate". Technical detail goes behind an **Advanced** disclosure.
5. **Waiting should feel pleasant.** Use calm progress bars, a slow waveform, a rising fill on the spine, and an honest "about 11 hours left". No spinners for long work.

## 3. Foundations

### Colour
All values are in `tokens.css`. Rules:
- **Accent fills vs. accent text.** `--rr-accent` is for fills with `--rr-on-accent` text. Use `--rr-accent-text` when the accent is text on paper. `--rr-accent-deco` is for decoration only (ribbons, card top rules).
- **Selected and active states** use `--rr-accent-soft` behind `--rr-accent-text`. This covers active nav, the active chip, the selected list row, warning tags and the current step.
- **Success / finished** uses `--rr-sage` text.
- **The "Changed" badge** uses `--rr-periwinkle-soft` with `--rr-periwinkle-ink` text.
- **Spine colours** come from the eight `--rr-spine-*` tokens. Each has a fixed text colour (see the comments in the token file). When a book has a cover image, use its dominant colour snapped to the nearest spine token.
- **Character colours** use `--rr-voice-N-chip` and `--rr-voice-N-tint`. Each character keeps one index everywhere: quote tint, name chip, avatar and right-rail dot.
- **Dark mode** is a "lamplit study": walnut and cream, never cool grey. Books keep their light-mode cloth colours, and runtime tags stay cream paper.

### Typography
| Role | Font | Size / weight |
|---|---|---|
| Wordmark | Serif | 25 / 500, −0.01em |
| Page title | Serif | 34 / 500, −0.01em |
| Section / drawer title | Serif | 19–24 / 500 |
| Book, chapter and character names in lists | Serif | 16–18 / 400 |
| Spine titles | Serif | 12–17 / 500, `writing-mode: vertical-rl`, no rotation |
| Reading pane | Serif | 20 / 400, line-height 1.75, max 660px measure |
| UI body, nav | Rounded sans | 15 |
| Secondary, chips, meta | Rounded sans | 13 (chips 13/700 when active) |
| Eyebrow | Rounded sans | 11 / 700, uppercase, 0.12–0.14em tracking, `--rr-ink-muted` |
| Times, durations, counts | Rounded sans | `font-variant-numeric: tabular-nums` |

Serif means Newsreader with a Source Serif 4 / Georgia fallback. The UI font is SF Pro Rounded on macOS, with Nunito as the web fallback.

### Shape, space and elevation
- **Radii:** books 3px · inputs and nav items 10px · tiles 14px · panels and popovers 18px · hero cards 20px · buttons and chips are full pills.
- **Shadows:** only covers, spines, the ledge and floating panels get shadows (use the `--rr-shadow-*` tokens). Rows, tiles and inputs sit flat with a 1px border.
- **Spacing:** use multiples of 2 and 4. Typical gaps: 8 within controls, 14–18 between rows of controls, 22–36 between regions. The page padding is 26px top, 40px left and 44px right.

### Icons
Thin rounded line icons, 16–18px, stroke 1.7, `stroke-linecap: round`, `currentColor`. Use SF Symbols (regular) natively or Lucide on the web. The play glyph is a filled triangle. Pause is two filled rounded bars. Never use emoji.

## 4. App shell

The window is 1440 × 900 with a unified title bar.

```
┌──────────┬──────────────────────────────────────────────┐
│ ● ● ●    │  page header (title · chips · search · CTA)  │
│          │                                              │
│ Reading  │  page content                                │
│ Room     │                                              │
│          │                                              │
│ Library  │                                              │
│ Now Pl.. │                                              │
│ Voices   │                                              │
│ Tricky W │                                              │
│ Settings │                                              │
│          │                                              │
│ ──────── │ ┌──────────────────────────────────────────┐ │
│ IN THE   │ │ mini-player                              │ │
│ STUDIO … │ └──────────────────────────────────────────┘ │
└──────────┴──────────────────────────────────────────────┘
```

**Sidebar** (`--rr-sidebar-w` 232px, `--rr-oat`, padding 16px 14px 18px):
- Traffic lights are 12px dots with 8px gaps at the top left. They're integrated, so there's no separate title bar strip.
- The wordmark sits 26px below in serif 25/500.
- Nav starts 26px below that. Each item is 40px tall with a 10px radius, padded 0 12px, with an 18px icon, a 12px gap and 15px text. The active item uses the `--rr-accent-soft` background and `--rr-accent-text` text at weight 600.
- The footer shows **Studio status** (only while something is rendering): a top hairline, then a "IN THE STUDIO" eyebrow, the book title in serif 16, a 4px progress bar and "Chapter 17 of 117 · about 11 h left" in 12px muted. Clicking it opens the rendering view.

**Mini-player:** docks at the bottom of the content area on every screen except the Player and the empty library. It's 60px tall, `--rr-card`, a 1px line border, radius 16. Contents in order:
- tiny cover (30×42)
- title in serif 16, with "Chapter 5 · Harriet" in 12px muted below
- a 36px accent pause button
- elapsed time
- a 3px scrubber (ink fill on a line track)
- total time
- a speed pill

Clicking it opens the Player.

## 5. Components

### Buttons
| Variant | Spec |
|---|---|
| Primary | pill, height 38–40, padding 0 18–20, `--rr-accent` / `--rr-on-accent`, 14/700, optional 14–16px leading icon |
| Secondary | pill, same height, `--rr-card` background, 1px `--rr-line` border, ink 14/600 |
| Dark | pill 34, `--rr-ink` background with `--rr-card` text (Retry, Play sentence) |
| Quiet / link | no background, `--rr-ink-muted` 13/600, optional chevron (Advanced) |
| Icon (play) | 30px circle, card background, 1px line border, 10–11px filled triangle |
| Hero play | 76px accent circle with an accent-tinted shadow `0 12px 22px -10px` |

There should be exactly one primary button per screen region.

### Chips and toggles
- **Filter chip:** 32px pill, padding 0 14, 1px line border. Active chips have no border, an `accent-soft` background and accent text at 13/700.
- **Segmented view toggle:** a pill with a 1px line border and 3px padding. Each segment is 34×28. The active segment has a `--rr-card` background.
- **Voice chip:** a pill with a periwinkle-soft background (the narrator's uses `--rr-periwinkle`), ink 13/700, voice name plus a down chevron. It opens a voice picker.

### Search field
36px pill, `--rr-card`, 1px line border, a 16px search icon, placeholder "Search  ⌘F". There's a visually hidden label.

### Shelf (signature element)
A shelf is a row of spines standing on a ledge.
- **Layout:** a grid with a `150px | 1fr` split. On the left is the shelf label (serif 19 name plus a 13px muted count, e.g. "7 books · 84 hours"), bottom-aligned. On the right are the spines.
- **Spine row:** height 170, `align-items: flex-end`, 8px gap, padding 0 22px.
- **Ledge:** 7px tall, `--rr-ledge`, with a 2px `--rr-ledge-edge` bottom border, radius 2 and `--rr-shadow-ledge`. Never use wood grain.
- **Space below the ledge:** about 52px on shelves that show tags, so tags can hang.
- **Grouping:** by status. Use "In the studio", "Finished" and "Not started" (or user shelves).

### Spine
- **Size:** width 34–66px (thicker for longer books) and height 128–166px (vary them so the shelf looks real).
- **Surface:** a `--rr-spine-*` background with its paired text colour, radius `3px 3px 2px 2px` and `--rr-shadow-spine`.
- **Anatomy, top to bottom:**
  1. A double rule: 58% wide, a 2px gap between two 1px currentColor lines, at 45% opacity.
  2. The title: serif, vertical, centred, nowrap, overflow hidden. Shorten very long titles ("Monte Cristo", "Dorian Gray"), but put the full title in `aria-label`.
  3. A small 5px diamond ornament outlined in currentColor at 50% opacity.
- **Lean:** one or two spines per shelf lean with `rotate(4–5deg)` and `transform-origin: bottom left`.

**States** (several can apply to one spine):
| State | Treatment |
|---|---|
| Not started | Plain spine. |
| Rendering | A 4px `--rr-accent` strip along the left edge, rising from the bottom to the percentage done. An 8px accent dot centred 12px above the spine with a 4px `accent-soft` halo; it pulses slowly (opacity 1 → 0.5, 2.4s) unless Reduce Motion is on. |
| Finished | A runtime tag hangs from the spine's bottom centre over the ledge (see Runtime tag). |
| Needs attention | A folded corner: a 14px top-right dog-ear, `linear-gradient(225deg, var(--rr-paper) 50%, rgba(0,0,0,.28) 50%)`. Plus a note tag in `accent-soft` / `accent-text` with a short note ("Check speakers", "1 chapter failed"). |
| Importing ("binding") | A transparent spine with a 1.5px dashed `--rr-ink-muted` border and muted title. An `--rr-oat` fill rises from the bottom to the import percentage. Tag reads "Binding… 42%". |
| Hover | `translateY(-6px)`, `--rr-shadow-spine-lift` and a tooltip card. |

**Runtime / note tag:** an absolutely positioned element at `top: 100%`, centred.
- A 1px `--rr-ink-muted` string 6–30px long. Alternate short and long strings on neighbouring spines so tags never overlap.
- Then a tag 3px 7px, radius 3, `--rr-tag-bg`, 1px line border, `--rr-shadow-tag`, 11/700 tabular text, with a 4px hollow "hole" circle before the text.
- Rotate ±2–3° from the top centre.
- Runtime format: `11h 42m`.

**Spine tooltip (hover card):** 236px wide, 12px padding, radius 14, card background, 1px line border, `--rr-shadow-popover`. It sits above the spine with a 14px gap. Contents:
- a 48×70 mini cover
- the title in serif 17
- the author in 12px muted
- the status line in 12/700 (sage when finished: "Finished · 8 h 37 m")
- "Narrated by Harriet" in 12px muted

### Book cover (typographic, for books without art)
- **Size:** 256×372 in book views, 300×436 in the player.
- **Surface:** spine-colour background, radius 3. Add `inset 6px 0 0 rgba(0,0,0,.12–.25)` to suggest a hinge.
- **Contents, centred:**
  - a double rule at the top
  - the serif title 44–46/500 about 50–76px down
  - an italic serif subtitle 18 at 85% opacity
  - a ⟡ ornament (two 12px rules around a 10px outlined diamond)
  - the author pinned to the bottom in serif 15, uppercase, 0.14em tracking
- **In book views:** `rotate(-2deg)` with `--rr-shadow-cover`.
- **In the player:** upright with `--rr-shadow-cover-hero`, plus a ribbon bookmark (16×96, `--rr-accent-deco`, clipped with a V notch, hanging from the top edge 40px from the right).

### Library card (metadata block)
- **Container:** max 380px, card background, 1px line border, a 3px `--rr-accent-deco` top border, radius `4 4 14 14`, padding 12 20 8. It's a `<dl>`.
- **Header row:** the eyebrow "LIBRARY CARD" on the left and serif 13 muted "No. 0014" on the right.
- **Rows:** each 32px tall with a top hairline, as a grid of `96px label (muted) | value`. Rows are Narrator, Voices, Chapters, Runtime and Rendered.
- **Finished stamp:** when a book finishes, a rubber-stamp "FINISHED" mark (sage, rotated −8°, 2px border, uppercase serif) fades onto the card.

### Chapter row
- **Container:** a `<li>` 40–42px tall with a 1px bottom hairline.
- **Grid:** `34px number | 1fr serif-17 title | status | 64px duration | 32px play`.
- **Statuses:**

| Status | Treatment |
|---|---|
| Done | Duration shown, play button. |
| Queued | Title and status in muted text, "Queued", no play button. |
| Rendering | Status "Rendering · 43%" in accent-text 13/700. |
| Changed | A "Changed" badge (periwinkle-soft pill, 12/700) shown when a tricky word or cast edit affects the chapter. |
| Error | Replaces the row with an error panel (below). |

**Error panel:** sits in the list with a 10px vertical margin. Card background, 1px line border, radius 16, `--rr-shadow-panel`, padding 14 16.
- The header row is number · title · a "Didn't finish" badge (accent-soft / accent-text).
- Then a plain-language explanation in 14/1.5, indented 48px. Example: "The narrator stopped about two-thirds of the way through. This usually happens when your Mac is short on memory. Closing a few apps and trying again normally fixes it."
- Then the actions: **Retry chapter** (dark button) and **Advanced ›** (quiet button, which reveals the technical log).

### Progress
- **Bars:** 4–6px tall with a radius equal to half the height. `--rr-line` track with an `--rr-accent` fill. Always paired with words ("Chapter 17 of 117", "about 11 hours left").
- **Now reading card:** card background, 1px line border, radius 20, padding 18 22, `--rr-shadow-panel`. Contents:
  - the eyebrow "READING ALOUD NOW" in accent-text, with the pulsing dot
  - "paragraph 41 of 96" in muted text on the right
  - the chapter title in serif 24
  - a waveform of 72 bars, each 3px apart with radius 2. Already-read bars are `--rr-accent` and the rest `--rr-line`. It drifts slowly while live.
- **Local note:** an oat panel, radius 14, with a 16px computer icon and 13/1.5 muted text: "Running privately on this Mac. If it goes to sleep, we'll pause and pick up where we left off."

### Player scrubber
- **Track:** 4px `--rr-line`, with an ink fill up to the playhead.
- **Chapter markers:** 2×10px ticks at each chapter start. Ticks before the playhead are ink; ticks after are `--rr-line-strong`.
- **Thumb:** 16px, card fill with a 2px ink border. It's a `role="slider"` and supports the arrow keys.
- **Labels below:** elapsed · "Chapter 5 · 12:04 of 31:40" · remaining (negative).

### Speaker review pieces
- **Quote highlight:** an inline `<span>` with the character's tint background, padding 2px 4px, radius 6 and `box-decoration-break: clone`.
- **Name chip:** before the quote. Rounded sans 11/700, padding 2px 8px, radius 9, the character's chip colour, `vertical-align: 3px`.
- **Low confidence:** the quote gets a `1.5px dotted --rr-ink-muted` outline with a 2px offset. The chip becomes a `<button>` with a card background, a `1.5px dashed` border and a "?" suffix ("Meg?").
- **Speaker popover:** opens when the chip is clicked. It's a dialog 290px wide, radius 18, card background, `--rr-shadow-popover`. Contents:
  - the title "Who says this?" plus a **Hear line** mini button
  - option rows 40px tall with radius 12, each a 12px colour dot · name · muted note ("current guess", "named nearby"); the suggested option uses an `accent-soft` background
  - final options "Narrator" and "Someone else…"
- **Stepper** (top right): Find speakers ✓ (sage) — Review (current: accent-soft pill) — Correct — Render. Steps are joined by 20px `--rr-line-strong` connectors.
- **Right rail:** `--rr-rail-w`, card background, left hairline. Contents:
  - the title "In this chapter" with a count line
  - character rows 44px tall: a 28px avatar in the chip colour with an initial, a serif name and a count
  - an accent-soft callout "2 lines to check" with a **Next** button
  - a muted note and the primary button "Looks right, render chapter"

### Cast list (playbill)
- **Container:** max 600px, card background, a double frame (`border` + `outline` 1px `#D9CDBA` with `outline-offset: 4px`), radius 4.
- **Header, centred:** the eyebrow "LOUISA MAY ALCOTT'S", the book title in serif 34, then "The Cast" in serif italic between two 40px hairlines.
- **Narrator row:** an oat panel, radius 14, a 40px ink avatar with an "N" in serif, "The Narrator" in serif 19, the sub-line "Everything outside quotation marks", a voice chip and a play button.
- **List label:** the eyebrow "BY NUMBER OF LINES".
- **Rows:** 48px tall with dotted bottom borders. Each row is a 30px avatar · a serif 18 name · "1,412 lines" (82px, right-aligned, tabular) · a 112px voice chip · a play button.
- **Footer:** serif italic muted "and 31 others with a line or two, read by the narrator".

**Voice library drawer** (`--rr-drawer-w`, slides in from the right, card background):
- A 2-column grid of voice cards: a 1px line border, radius 14, a 32px coloured play circle, the name in serif 17 and a two-word descriptor ("warm, unhurried").
- **Clone a new voice card:** paper background, radius 18. Contents:
  - a dashed drop zone. After a file is added it shows the file name and a friendly quality check ("42 seconds · clear and quiet, good to use").
  - a Name input
  - **Warmth** and **Pace** sliders: a 4px track with an accent fill and an 18px card thumb
  - Preview · Advanced · **Save voice**

### Tricky-word index card
- **Card:** 160px tall, radius 6, card background with faint ruling (`repeating-linear-gradient(to bottom, transparent 0 25px, var(--rr-periwinkle-soft) 25px 26px)` starting under the header), a 1px line border and a small warm shadow.
- **Header:** 36px tall with a 1px salmon rule (`#DE9B7E`, the accent-deco tint) underneath. The word is in serif 25 on the left and the phonetic hint in italic 13 muted on the right ("KWEE-kweg").
- **Body:** a sample pill button (play triangle plus a tiny 54px accent waveform), then "Moby-Dick · 517 times" in 12px muted.
- **Grid:** 3 columns, 18px gap.
- **In chapter text,** listed words get `text-decoration: underline dotted var(--rr-accent); text-underline-offset: 4px; text-decoration-thickness: 2px`.

**Add-a-word card:** 380px wide, floating, rotated 0.8° (the screen's moment of play), with `--rr-shadow-popover`. Contents:
- a header saying where the word was found ("found 252 times in Moby-Dick")
- Word: a large serif 30 input with only a bottom border
- Sounds like
- Your recording: a 40px accent mic button, the waveform and its duration
- **Hear it in the book**: an oat panel showing a real sentence with the word underlined, plus a **Play sentence** button
- Cancel / **Save word**

When saved, chapters that use the word get marked "Changed".

### Empty shelf / drop zone
- Centred serif 34 "Your shelf is waiting." with a 15px muted line under it.
- An empty shelf with a dashed (`1.5px --rr-line-strong`) card-background drop zone 170px tall sitting on the ledge, plus a simple L-shaped bookend in `#CDBFA8`.
- The drop zone has an upload icon in accent, "Drop an EPUB or Markdown file here" in 16/700, and "or choose one from your Mac · ⌘O" with the link in accent-text.
- Footer note: "Books are read and narrated on this Mac. Nothing is uploaded."

## 6. Screens

| # | Screen | Layout | Moment of play |
|---|---|---|---|
| 1 | **Library** | Header: title · chips (All, Rendering, Finished, Needs review · n) · spacer · search · shelf/grid toggle · **Add a book**. Three shelves, vertically centred. Mini-player at the bottom. | Hanging runtime tags |
| 1b | Library, dark | Same screen with dark tokens. | — |
| 1c | Library, empty | See Empty shelf. | Bookend |
| 2 | **Book view** | "← Library" back link. Left 40%: tilted cover with the library card below. Right: serif 34 title and author · year, then actions (**primary** + Cast / Tricky words / Export MP3s), a one-line muted context note, and the chapter list with the error panel inline. | Tilted cover |
| 3 | **Rendering** | Same split. The left side has the progress block and local note under the cover. On the right, the primary button becomes **Pause rendering**, the Now reading card sits above the list, and the list heading reads "Listen to finished chapters while the rest render". | Waveform |
| 4 | **Speaker review** | Reading pane (max 660, serif 20/1.75) with the stepper at the top and the right rail. | — |
| 5 | **Cast** | Centred playbill with the voice library drawer on the right. | Playbill frame |
| 6 | **Tricky words** | Header + search, a 3-column card grid, and the floating add-word card on the right. | Tilted card |
| 7 | **Player** | Centre column: cover with ribbon, serif 30 chapter title, a meta line, the scrubber (max 620), controls (previous chapter · back 30 · **76px play** · forward 30 · next chapter), and pills (Speed 1.1×, Sleep: end of chapter). Right: a 380px card-background chapter list (✓ for finished, ▶ in accent for current with an accent-soft row). | Ribbon bookmark |

Primary action labels on the book view, in priority order: **Render audiobook** → **Resume** → **Re-render changed chapters · n** → **Retry chapter**.

## 7. Motion

All motion uses `--rr-ease-settle` (ease-out, no bounce, no flash).
- **Spine → book:** the spine lifts, rotates on Y to face forward and scales into the cover's position over `--rr-dur-move` (400ms). Use a shared-element / FLIP transition.
- **Hover:** spines lift 6px with a deeper shadow over 180ms. Covers tilt 1–2°.
- **Play:** the cover slides down into the mini-player's thumbnail.
- **Rendering:** the spine fill rises smoothly as each chapter completes, and the waveform drifts.
- **Finishing:** the runtime tag drops in and swings ±6° → ±2° → 0 over about 900ms. The "Finished" stamp fades in on the library card.
- **Reduce Motion:** replace all movement with 220ms opacity fades and turn off the pulse and the swing.

## 8. Interaction and platform

- Keyboard shortcuts: Space plays/pauses · ⌘F search · ⌘O add book · ⌘, settings · arrow keys on the scrubber and lists.
- Dropping files anywhere on the window shows a full-window accent-soft overlay saying "Drop to add to your shelf".
- **Preview everywhere:** any editable thing (a speaker correction, tricky word, voice setting or cast chip) has a small play button that plays just that sentence.
- Errors are written in plain language, always say what the user can do next, and keep technical detail behind **Advanced**.

## 9. Accessibility

- Text contrast must be ≥ 4.5:1 (≥ 3:1 at 24px+). The token pairs already pass. Don't put white text on `--rr-accent-deco`, and don't use `--rr-sage-deco` as text.
- Use real `<button>`, `<a>` and `<input>` + `<label>` elements. Icon-only buttons get an `aria-label`.
- Spines are links with full-title `aria-label`s that include their status ("Moby-Dick, needs attention: 1 chapter failed").
- Status is never conveyed by colour alone: always pair it with text (a tag, a badge or a "?" chip).
- Focus ring: 2px `--rr-accent` with a 2px offset.

## 10. Copy voice

| Say | Don't say |
|---|---|
| Narrator, Cast, Voices | TTS model, speaker profile |
| Tricky words, Sounds like | Phoneme override, IPA |
| Find speakers, Who says this? | Diarization, attribution confidence |
| Render, Rendering, In the studio | Inference, synthesis job |
| Didn't finish · Retry chapter | Error 137 / OOM |
| about 11 hours left | ETA 10:58:12 |
| Binding… | Parsing EPUB |

The tone is warm, brief and unhurried, and it speaks in second person.

## 11. Don't

- Use generic SaaS dashboard patterns (stat cards, charts, dense tables) or blue-and-grey palettes.
- Use pro-audio styling (dark racks, neon meters, multitrack timelines).
- Use frosted glass, heavy gradients, wood grain, leather, torn paper or more than one or two playful details per screen.
- Use emoji, spinners for long jobs, or technical jargon in the main UI.

## 12. Suggested React structure

```
src/
  styles/tokens.css            ← this package's tokens.css
  components/
    shell/      AppShell, Sidebar, TrafficLights, StudioStatus, MiniPlayer
    controls/   Button, IconButton, Chip, ChipGroup, SegmentedToggle, SearchField, Slider, Switch
    books/      Shelf, Spine, SpineTag, SpineTooltip, BookCover, LibraryCard, FinishedStamp
    chapters/   ChapterList, ChapterRow, ChapterErrorPanel, NowReadingCard, Waveform, ProgressBar
    speakers/   ReviewStepper, QuoteHighlight, SpeakerChip, SpeakerPopover, CharacterRail
    cast/       Playbill, CastRow, VoiceChip, VoiceLibraryDrawer, VoiceCard, CloneVoiceCard
    words/      WordCard, AddWordCard, TrickyWordUnderline
    player/     Player, Scrubber, TransportControls
    empty/      EmptyShelf, DropOverlay
  screens/      Library, BookView (with rendering state), SpeakerReview, Cast, TrickyWords, Player
```

Spine dimensions should come from data: map width to word count (34px at about 25k words up to 66px at 300k+), and pick height and colour deterministically from the book ID so the shelf looks the same every time.

## 13. Sample content (public domain)

| Shelf | Books |
|---|---|
| In the studio | *The Count of Monte Cristo* (rendering, chapter 17 of 117 "The Abbé's Chamber", 14%), *Little Women* (check speakers), *Anna Karenina*, *Moby-Dick* (chapter 7 "The Chapel" failed), *Treasure Island* |
| Finished | *Pride and Prejudice* 11h 42m, *Frankenstein* 8h 37m, *Jane Eyre* 18h 55m, *Persuasion* 8h 21m, *Emma* 16h 50m, *Wuthering Heights* 12h 3m, *The Secret Garden* 8h 7m |
| Not started | *Middlemarch*, *Dracula*, *War and Peace*, *Great Expectations*, *Walden*, *The Picture of Dorian Gray*, *A Tale of Two Cities*, *North and South* (importing) |

- **Voices:** Harriet (warm, unhurried) · June (bright, quick) · Arthur (low, gentle) · Clementine (soft, careful) · Theo (easy, playful) · Wren (quiet, young).
- **Tricky words:** Marmee (MAR-mee) · Dantès (dahn-TESS) · Mercédès (mair-SAY-dess) · Ahab (AY-hab) · Pequod (PEE-kwod) · Ingolstadt (ING-gol-shtaht) · Clerval (klair-VAL) · Meryton (MERR-ih-tun) · Haydée (eye-DAY) · Queequeg (KWEE-kweg).
