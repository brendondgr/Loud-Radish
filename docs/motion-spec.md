# Motion Specification — The Aperture Microphone

*Created: 2026-08-15. Source: "The Aperture microphone", voice interface / motion specification,
Rev A.03, supplied 2026-08-15 as a design-doc canvas (`Mic Modes.dc.html`).*

The state indicator for this application: a ribbon-grille microphone whose **thirteen elements are
both the grille and the meter**. This file is the canonical extraction of that specification, so the
design survives without the original file — which was a design-doc canvas depending on a generated
runtime harness, and lived outside this repository.

**The governing rule of the whole design, stated once:** the instrument is *colourless at rest*. It
only takes on a hue when it is doing work on the user's behalf. The one exception is the fault
state, which is warm while doing nothing — because doing nothing is the problem.

---

## 1. Geometry

Thirteen rounded bars, symmetric about the centre, inside a `0 0 200 224` viewBox. Each bar is
`4.4` wide with `rx="2.2"`, centred vertically on `y = 88`, and scales on `scaleY` about its own
centre (`transform-origin: center`).

| # | `dx` from centre | Height | | # | `dx` | Height |
|---|---|---|---|---|---|---|
| 1 | −42 | 37.6 | | 8 | +7 | 91 |
| 2 | −35 | 59.8 | | 9 | +14 | 87.6 |
| 3 | −28 | 73 | | 10 | +21 | 81.8 |
| 4 | −21 | 81.8 | | 11 | +28 | 73 |
| 5 | −14 | 87.6 | | 12 | +35 | 59.8 |
| 6 | −7 | 91 | | 13 | +42 | 37.6 |
| 7 | 0 | 92 | | | | |

A bar's rect is therefore `x = 100 + dx − 2.2`, `y = 88 − h/2`, `width = 4.4`, `height = h`.

The capsule around it, stroked at `2.4` with round caps:

```
circle cx=100 cy=88 r=54                          the capsule
path   M40 78 V94 A60 60 0 0 0 160 94 V78         the yoke
path   M100 150 V194                              the stem
path   M68 200 H132                               the base bar
path   M74 200 C74 196 78 194 84 194 H116 C122 194 126 196 126 200   the foot
```

Three status LEDs sit on the base at `y = 211`, `r = 2.4`, at `x = 86, 100, 114`. Unlit is
`#CFD3D6`. Which are lit is part of each state below — they are a second, redundant signal for the
state's hue, which is what keeps the design compliant with "colour is never the only signal".

---

## 2. The five states, plus fault

| State | Hue | Frame | Grille motion | Period | Lit LEDs |
|---|---|---|---|---|---|
| **Idle** | `#8A8F95` (grey) | `#8A8F95` | `breathe`, 13–23% travel | 5.2 s | none |
| **Live transcription** | `oklch(0.68 0.09 195)` teal | `#5D6469` | `wave`, travelling sine | 1.5 s | 1st |
| **Recording** | `oklch(0.58 0.14 25)` red | `#5D6469` | `hold`, pulses as one body | 1.05 s | 2nd |
| **Transcribing** | `oklch(0.7 0.11 75)` amber | `#5D6469` | `settle`, falls quiet | 2 s | 3rd |
| **Rewriting** | `oklch(0.6 0.1 300)` violet | `#5D6469` | `ripple` | 2.8 s | 2nd + 3rd |
| **Fault — no audio** | `oklch(0.55 0.1 40)` warm | `#8A8F95` | four flat stubs, `shudder` | 3.6 s | none |

### Idle

Grey grille breathing on a five-second cycle at 13–23% travel. **Fully desaturated: no hue is
permitted at rest.** Bars stay `#8A8F95`. Status dot blips at 3.4 s.

### Live transcription

A travelling sine across the grille — each bar delayed ≈ `−0.14 s` per element, so the wave moves
along the capsule rather than pulsing together. Two halo rings expand outward on every phrase
(`aura`, 2.6 s, the second offset by `−1.3 s`), growing `r` 56 → 96 while fading to nothing.

### Recording

The grille **holds high and pulses as one body — capturing, not reading.** Delays are symmetric
about the centre rather than sequential, so the whole capsule moves together. A soft halo circle at
`r = 66` breathes between `0.14` and `0.5` opacity (`halo`, 2.2 s). Beneath it, a waveform scrolls
right to left into storage (`trail`, 2.4 s linear, translating `−84px` over a doubled bar set so it
loops seamlessly).

### Transcribing

**The grille falls quiet and a read head sweeps the capsule left to right. The mic is no longer
listening — it is looking.** Bars drop to 11–20% (`settle`, 2 s) and go neutral `#9BA0A5`; the hue
belongs to the read head, not the grille. The head is two stacked rects — an 8-wide amber glow at
`0.55` opacity and a 2-wide core — sweeping ±46 px (`scan`, 2.4 s, `cubic-bezier(.4,0,.6,1)`).

### Rewriting

**Two arcs counter-rotate around the capsule while the grille ripples — thinking, not hearing.** An
outer arc `M100 22 A66 66 0 0 1 157 55` rotates forward over 5.5 s; an inner arc
`M100 154 A66 66 0 0 1 43 121` counter-rotates over 7.5 s at `0.7` opacity. Both spin about
`100px 88px`.

### Fault — no audio

The grille collapses to **four flat stubs** (6 tall, at `dx` −21, −7, +7, +21) and the capsule
shudders once every few seconds. A diagonal slash `M58 46 L142 130` crosses the capsule. This is the
**only state permitted a warm hue while doing nothing**.

---

## 3. Keyframes

Verbatim from the specification. `scaleY` values are fractions of each bar's full height.

```css
@keyframes breathe  { 0%,100%{transform:scaleY(.13)} 50%{transform:scaleY(.23)} }
@keyframes wave     { 0%,100%{transform:scaleY(.16)} 35%{transform:scaleY(.86)} 62%{transform:scaleY(.34)} }
@keyframes hold     { 0%,100%{transform:scaleY(.52)} 50%{transform:scaleY(.88)} }
@keyframes settle   { 0%,100%{transform:scaleY(.11)} 20%{transform:scaleY(.20)} }
@keyframes ripple   { 0%{transform:scaleY(.18)} 30%{transform:scaleY(.62)} 70%{transform:scaleY(.28)} 100%{transform:scaleY(.18)} }
@keyframes aura     { 0%{r:56;opacity:.5} 100%{r:96;opacity:0} }
@keyframes halo     { 0%,100%{opacity:.14} 50%{opacity:.5} }
@keyframes scan     { 0%{transform:translateX(-46px)} 100%{transform:translateX(46px)} }
@keyframes orbit    { 0%{transform:rotate(0deg)} 100%{transform:rotate(360deg)} }
@keyframes orbitBack{ 0%{transform:rotate(360deg)} 100%{transform:rotate(0deg)} }
@keyframes blip     { 0%,100%{opacity:.22} 50%{opacity:1} }
@keyframes shudder  { 0%,100%{transform:translateX(0)} 20%{transform:translateX(-2px)} 40%{transform:translateX(2px)} 60%{transform:translateX(-1px)} }
@keyframes trail    { 0%{transform:translateX(0)} 100%{transform:translateX(-84px)} }
@keyframes drift    { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-4px)} }
```

### Amplitude, when driven by real audio rather than by CSS

The specification also gives closed-form amplitudes, for a renderer driving the bars from a clock or
a live level instead of from CSS animation — which is what the tray helper will do, since it draws
frames rather than running a stylesheet. `i` is the bar index 0–12, `t` is seconds, `k` an intensity
multiplier:

| State | Amplitude |
|---|---|
| Idle | `0.145 + sin(1.15t − 0.30i) × 0.045` |
| Live | `0.16 + (｜sin(2.4t − 0.52i)｜ × 0.52 + ｜sin(1.1t + 0.21i)｜ × 0.26) × k` |
| Recording | `0.5 + ｜sin(3.1t − 0.34i)｜ × 0.4k` |
| Transcribing | `0.1 + max(0, 1 − ｜((5.2t mod 26) − 6 − i)｜ × 0.55) × 0.34k` |
| Rewriting | `0.22 + (sin(1.6t − 0.46i) × 0.5 + 0.5) × 0.34k` |
| Fault | `0.07` where `i mod 3 == 1`, else `0.05` |

---

## 4. Transitions

The specification is explicit that the transition itself animates, and names three:

| Transition | What happens |
|---|---|
| **Idle → Listening** | Grey grille inflates outward from centre over **320 ms**; teal bleeds in from the middle element to the edges. |
| **Recording → Transcribing** | Red drains out through the stem; the grille collapses to a flat line; then the amber read head enters from the left. |
| **Transcribing → Rewriting** | Amber hands to violet at the moment the last token lands; arcs spin up as the raw text strikes through. |

Bar fill colour crossfades over **420 ms** (`transition: fill 420ms ease`) whenever the hue changes.

The flow returns from Rewriting to Idle along a dashed return path — **commit · return to idle** —
so the cycle closes rather than ending.

---

## 5. Mapping onto this application's run states

The specification's vocabulary and the run-state vocabulary fixed in **D-020** are close but not
identical, and the differences are informative rather than accidental.

| Spec state | This application | Notes |
|---|---|---|
| Idle | `idle` | Direct. |
| Live transcription | `recording` **in `live` mode**, or in `window` mode with live transcription on | The spec distinguishes *listening and writing* from *capturing silently*; D-020 distinguishes them by **mode**, not by run state. So the visual state is a function of (mode, run state), not of run state alone. |
| Recording | `recording` **in `recorded` mode**, or in `window` mode with live transcription off | "Nothing is being written yet — the buffer is held locally" is exactly what `recorded` mode does. |
| Transcribing | `processing` | The post-capture batch pass of Plan 3. |
| Rewriting | *(no run state)* | This is the **polish pass (D-018)**, which runs in the background *during* a live session rather than as a phase of one. See below. |
| Fault — no audio | `error` | The spec's fault is specifically *no audio*; `error` is broader. The stub-and-shudder treatment fits a dead input exactly and is the right default for any failure. |
| *(none)* | `arming` | Not in the spec. Use idle's grille with the target mode's hue bleeding in — the front half of the Idle → Listening transition, held. |
| *(none)* | `stopping` | Not in the spec. Use the front half of the Recording → Transcribing transition: the hue draining through the stem. |

**Two consequences worth stating before anything is built:**

1. **The indicator is driven by (mode, run state), not by run state alone.** `recording` in `live`
   mode and `recording` in `recorded` mode are the same run state and *different pictures* — which
   is right, because they are doing genuinely different things. Any manifest keyed on run state
   alone would collapse them and lose the distinction the specification is built around.

2. **Rewriting is concurrent, not sequential.** The specification draws a linear flow
   (idle → live → recording → transcribing → rewriting → idle), but in this application the polish
   pass runs *while* a live session continues. It is a state the instrument can be in while also
   recording. Whether the indicator shows rewriting at all during a live session, or reserves it for
   the post-capture pass, is a decision for whoever builds it — recorded here so it is made
   deliberately rather than discovered.

---

## 6. Where this is used

- **Plan 5, the tray companion** ([plans/system-integration.md](plans/system-integration.md)). This
  file is what step 5 of that plan was blocked on. The tray draws frames rather than running a
  stylesheet, which is why §3's closed-form amplitudes matter as much as the keyframes.
- **The header's record indicator**, potentially. The current indicator is a dot and a word, and
  D-020's motion rule deliberately forbids anything that loops in an interface on screen for two
  hours. Replacing it with a breathing thirteen-bar capsule would contradict that rule, so it is
  **not** adopted there by default. Recorded as a considered decision rather than an oversight; if
  it is adopted later, the reduced-motion path must hold every bar at its resting amplitude.

## 7. Type and palette from the source document

Not required by anything yet, and recorded only so the specification is complete: the design doc is
set in **Space Grotesk** (UI), **IBM Plex Mono** (labels, uppercase, wide tracking), and
**Newsreader** (prose), on a warm paper ground `#EFEDE8` with panels at `#F7F6F3`, hairlines at
`#DCD8D0`, and ink at `#16181B`. This application's own palette is dark by deliberate decision — a
bright screen in a darkened lecture hall is conspicuous — so the document's *ground* is not adopted.
Only the six state hues are.
