# Nexus Optimize v3 — Design System (LOCKED, Phase 1)

Aesthetic: **Obsidian / Neon Cyan / Amethyst**. Dark only, high contrast, glass HUD over near-black.
Every component built from Phase 2 on uses these tokens. Changing a token means editing this file first.

## 1. Color — RealtimeColors 5-token palette

Defined once in the first `:root` of `ui/css/style.css`. Never hardcode these hex values anywhere else.

| Token | Hex | Role | Contrast on `--background` | Allowed use |
|---|---|---|---|---|
| `--text` | `#FFFFFF` | Primary copy, values, headings | 20.5:1 | Any text |
| `--background` | `#030407` | Obsidian base surface | — | Page and window background |
| `--primary` | `#00F0FF` | Neon cyan. Brand color, primary actions, focus, live data | 14.5:1 | Text, icons, borders, fills |
| `--secondary` | `#B026FF` | Amethyst. Secondary data series, gradient partner | 4.4:1 (below AA for body) | Large text (≥18px or ≥14px bold), icons, fills, borders. **Not small body text.** |
| `--accent` | `#FF2A6D` | Hot magenta. Peak highlights, gradient end. Sparse: at most one per view | 5.7:1 | Highlights, badges, gradient tail |

**Derived, not tokens.** These exist in `style.css` and stay as they are:
- Gradients: `--grad` (primary→secondary) and `--grad-hot` (primary→secondary→accent).
- Text tiers: `--text-hi` (alias of `--text`), `--text-mid #A1AABB`, `--text-low #626B82`. Use `--text-low` only for decorative or disabled text.
- Surfaces: `--bg-0` (alias of `--background`), `--bg-1`, `--bg-2`, `--glass*`, `--hairline*`.
- Status: `--good #00FF9D`, `--warn #FFB800`, `--bad #FF0055`. These are for meaning, never decoration, and always paired with a label or icon (never color alone).

Tints: use `color-mix(in srgb, var(--primary) 20%, transparent)`, not new rgba literals.

## 2. Typography

The app is an offline Electron window: nothing is fetched at runtime. System fonts for UI, plus one **bundled** display face: Playfair Display (SIL OFL 1.1, `ui/fonts/OFL.txt`), latin subset woff2 at `ui/fonts/PlayfairDisplay-{Regular,Italic}.woff2`, declared by `@font-face` at the top of `style.css`.

| Role | Stack | Style |
|---|---|---|
| Headings / HUD (`font-sans`) | `var(--font-hud)`: Bahnschrift, Segoe UI Variable Display, Segoe UI | **Bold 700**, uppercase for labels, tracking +0.6–2.4px |
| Accent word (`font-serif`) | `var(--font-serif)`: 'Playfair Display', Georgia, Cambria, serif | *Italic 400*, `--grad` text fill via `.serif-accent` (or `--primary`). **One word or short phrase per heading.** |
| Editorial captions | `var(--font-serif)` | Regular 400 for empty states (`.empty`); Italic 400 for `.hero-kicker` and `.section-title span`. Only weight 400 is bundled, so never set it bold (Chromium would fake-bold it). |
| Body | Segoe UI Variable Text, Segoe UI, system-ui | Regular 400 / Semibold 600 |
| Numbers / paths | `var(--font-mono)`: Cascadia Mono, Consolas | `font-variant-numeric: tabular-nums` |

Pattern: `<h1>System <em class="serif-accent">resources</em></h1>`, with a bold sans headline (sentence case, not uppercase) and an italic serif accent word.

Scale (px, root 15px): 10.5 · 11.5 · 13 · 15 · 18 · 22 · 28 · 34. Line-height 1.15 for headings, 1.5 for body.

## 3. Rules carried over from v2 (still binding)

- Class names are a contract with `renderer/app.js`. Do not rename them.
- Animate `transform` and `opacity` only. Infinite ambient loops pause under `body.idle`.
- Respect `prefers-reduced-motion` (already global in `style.css`).
- Visible focus ring: 2px `--primary` outline, offset 2px.

## 4. Motion

- **GSAP 3.15.0**, bundled at `ui/lib/gsap.min.js` (GreenSock standard no-charge license), loaded before `renderer/app.js`. No CDN.
- **Ownership:** GSAP owns the `transform` property (entrances, card tilt). CSS hover and press use the individual `translate` / `scale` / `rotate` properties. They compose, so CSS transitions never re-smooth GSAP frames. Never put `transform` in a CSS `transition` on an element GSAP touches.
- **Load:** titlebar → sidebar → nav stagger → the active view's blocks (stagger 60ms, `expo.out`, y 30 + scale .97) → hero title clip-path wipe → hero badges.
- **Tab switch:** `revealView()` re-plays the block stagger for the new view.
- **Glass rule:** reveals fade the glass cards, never their `.gauge-grid` / `.panel-row` wrappers. Opacity < 1 on an ancestor breaks the children's `backdrop-filter`.
- **Cards:** `.gauge-card`, `.mod-card`, `.nx-card` tilt toward the pointer (±5°, `gsap.quickTo`, perspective 900px) on top of the CSS lift, spotlight and glow.
- **Reduced motion:** `prefers-reduced-motion` skips all GSAP motion in JS, and CSS collapses its own durations.

## 5. Not decided yet

Component specs and a spacing grid beyond the current radii (`16px` / `10px`, hero `24px`).
