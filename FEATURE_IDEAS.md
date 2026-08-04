# AKP05 Bridge — feature ideas

Brainstorm, not a roadmap — nothing here is scheduled or committed to.
Ranked loosely by how much would actually need to change vs. what's
already possible today with zero new code.

## One correction first

The linked-entity removal (0.7.0) took out the add-on *automatically
watching* an entity's state on its own (HAWatcher, the Core API
WebSocket connection). It did **not** touch the underlying rendering:
`akp05_icons.build_icon(icon, is_on)` still colors an icon green/red/gray
by state exactly as before — it's what `akp05/cmd`'s `set_icon` action
already calls. What's gone is the add-on deciding *when* to call it on
its own; now something else (an automation) has to trigger it. Every
idea below that involves state-based coloring builds on that same
function, not a rebuilt one.

## At a glance

| Idea                              | Category       | Needs                                            |
|------------------------------------|----------------|---------------------------------------------------|
| Spotify pause button               | Interaction    | Nothing — works today                              |
| Paging / profiles                  | Interaction    | Nothing new — automation composition               |
| Sensor value on a button           | Display        | Text-render function + new `cmd` action            |
| Weather tile                       | Display        | Same, + condition→icon mapping                     |
| Touch strip as a status bar        | Display        | Same, composited across the 800×112 strip          |
| Now-playing display                | Display        | Same, + `media_player` attributes                  |
| Momentary press feedback           | Interaction    | Small add-on addition                              |
| Alert takeover (flash all red)     | Interaction    | Small add-on addition                              |
| Ingress web UI for configuration   | Infrastructure | Bigger — a whole new ingress-served page           |
| Touch strip gestures               | Research       | Unsolved — see notes, not just "unexplored"        |

## Already possible today

**Spotify pause button.** HA's own Spotify integration (OAuth, official
core integration) already exposes a `media_player` entity reflecting
whatever's the *active* Spotify Connect device — phone, desktop app,
speaker, whichever the user is actually listening on. A button bound to
`media_player.media_pause` targeting that entity, via the same router
automation pattern already in use, pauses it exactly the way hitting
pause in the Spotify app itself would. No raw Spotify Web API call
needed, no new code — this is just another binding like `light.toggle`.

**Paging / profiles.** With 10 buttons + 4 encoders, a "swap the whole
button set" pattern (e.g. long-press an encoder to flip from a lighting
page to a media page) is pure automation composition — republish 10 new
`akp05/cmd` `set_icon`/`set_text` calls at once, no device-side changes.

## Display & dashboards

These four all lean on the same missing piece: a text-rendering
function alongside `build_icon` in `akp05_icons.py` (PIL's `ImageDraw`
+ an embedded font — same pattern as the already-embedded MDI font, no
new dependency), plus a new `akp05/cmd` action to call it, plus an
automation on the source entity's state change (the confirmed-reliable
pattern, not a rebuilt watcher). Suggested font: **Roboto** — the same
one Home Assistant's own frontend uses (free, Google/Apache-2.0
licensed), so a rendered value reads like it belongs in the same UI.

- **Sensor value on a button.** The idea that started this — a
  temperature (or humidity, power draw, battery %, anything numeric)
  rendered as text instead of a static icon, refreshed by automation on
  state change. Button presses do nothing, which is fine — it's a
  dashboard tile, not a control, the same way a lot of Stream Deck real
  estate ends up used.
- **Weather tile.** Pairs a weather-condition icon (`mdi:weather-sunny`
  etc., mapped from `weather.*`'s `condition` attribute) with the
  temperature as text on one button.
- **Touch strip as a status bar.** The 800×112 strip is mostly idle
  right now. Same rendering, composited wider — several values side by
  side, or a small sparkline of a sensor's trend rather than just its
  current value. Reuses the existing `set_strip`/strip-cache machinery
  as-is.
- **Now-playing display.** Track/artist text on a button or the strip
  from a `media_player`'s `media_title`/`media_artist` attributes,
  encoders already sitting right there for volume/skip.

## Interaction & automation

- **Momentary press feedback.** Flash a button (e.g. to green) to
  confirm a press registered, then revert to whatever it was showing.
  The add-on already tracks `button_icons` (what's currently on each
  button, restored on reconnect), so "revert to normal" is already
  known — this would live naturally as a small add-on-side primitive
  rather than something an automation has to remember itself.
- **Alert takeover.** All (or some) buttons flash red on a trigger —
  door left open, security event, laundry done — then auto-restore a
  few seconds later. Same reasoning as above: worth an add-on-side
  `akp05/cmd` action (e.g. `alert_all`) specifically because it can use
  `button_icons` to restore automatically, where an automation would
  have to manually snapshot and reset up to 10 buttons' worth of state.

## Infrastructure

**Ingress web UI for configuration.** HA add-ons can serve their own
page straight into the HA sidebar (`ingress: true`, no separate
integration needed — same spirit as `akp05_web.py` on the Windows side,
but native to the add-on). Worth it once the number of per-button
settings (icon, and whatever a sensor-display feature adds) makes
hand-typing into a dozen-plus MQTT text entities feel worse than a real
visual editor. Biggest lift on this list — a real second surface to
build and keep working, not a small addition.

## Open research

**Touch strip gestures.** Checked both reference implementations
directly rather than guessing: opendeck-akp05's `read_encoder_press`
actually *does* see touch input on the wire — bytes `0x38` and `0x39` —
but the author disabled decoding them:

```rust
let encoder: usize = match input {
    0x37 | 0x00 | 0x40 => 0, // Left most
    0x35 | 0x41 => 1,
    0x33 | 0x42 => 2,
    0x36 | 0x43 => 3, // Right most
    // Ignore swipe for now because they are unreliabe/detected incorrectly
    // 0x38 => 4,
    // 0x39 => 5,
    _ => return Err(MirajazzError::BadData),
};
```

So this isn't unexplored — it's a documented dead end nobody's pushed
past. Concrete starting point if anyone wants to try: run
`akp05_listener.py` and watch for `0x38`/`0x39` while touching the
strip, and try to correlate them with position/direction rather than
treating them as a fixed input like every other key. If it's crackable,
it'd turn the strip into an actual input surface, not just a display.
