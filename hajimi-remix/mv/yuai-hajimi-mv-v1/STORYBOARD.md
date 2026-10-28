---
compositionId: yuai-hajimi-mv-v1
duration_s: 50.15
canvas: {"w":1080,"h":1920,"fps":30}
style:
  font: "Bebas Neue / Inter"
  palette: ["#1A1A1A", "#F5F0E8", "#E8E0D4", "#E85D5D", "#D44A4A", "#6B6B6B"]
assets: true
build_notes:
  - "Daily One-style information hierarchy with one muted, pre-trimmed source-MV window"
  - "All 97 sung syllables come from data/timed_lyrics.json; only the current syllable is coral"
  - "One paused deterministic GSAP timeline per frame; host owns the sole audio track"
  - "Audio response comes from precomputed data/audio-bands.json; never analyze audio at runtime"
  - "Native 9:16 reflow: rain window above, title and karaoke below; never crop a landscape composition"
  - "The source MV is pre-cropped to 928x642 and 50.15s; a single host video spans all four frames"
avoid:
  - "generic equalizer bars, waveform displays, particle fields, or multiple concurrent video streams"
  - "tiny text, rounded UI cards, soft shadows, remote fonts, and full-screen flashes"
---

## Frame 1 — 01-chorus-a

- src: compositions/frames/01-chorus-a.html
- duration: 18.28s
- span_sec: [0.0, 18.28]
- pacing: beat_cut
- mood: [rainy, editorial, intimate]
- feel: an immediate vocal entrance establishes the rain-window player, then the first phrase gains pulse without changing layout

### Groups

- **g1** — free_design
  - span_sec: [0.0, 9.149]
  - free_design: { dominant_system: "hard-edged rain-window player with a stable lyric column", primitives: ["mask-reveal", "bg-flow-field", "content-swap"], density_topology: "establish then accumulate" }
  - anchors: [0.046, 1.254, 2.81, 5.782, 8.034]
  - copy: ["雨爱", "RAIN LOVE", "HAJIMI NOTE / EVENT V1", "哈基米南北绿豆"]
- **g2** — free_design
  - span_sec: [9.149, 15.534]
  - free_design: { dominant_system: "same player, larger rain pool and coral syllable relay", primitives: ["palette-flip", "directional-fill", "content-swap"], density_topology: "drop to one line, then rebuild" }
  - anchors: [9.149, 9.543, 10.286, 11.029, 12.539, 13.328, 14.814, 15.534]
  - copy: ["CHORUS A", "97 SYLLABLES", "CURRENT / NEXT"]
- **g3** — free_design
  - span_sec: [15.534, 18.28]
  - free_design: { dominant_system: "phrase-one landing with identity nodes collecting at the pool", primitives: ["outline-to-fill", "freeze-hold", "staggered-exit"], density_topology: "compress then clear" }
  - anchors: [15.534, 16.324, 17.043, 17.856, 18.158]
  - copy: ["HAJIMI", "PHRASE 01", "雨落成句"]

## Frame 2 — 02-build

- src: compositions/frames/02-build.html
- duration: 14.3s
- span_sec: [18.28, 32.58]
- pacing: beat_cut
- mood: [driving, structured, warm]
- feel: the second phrase keeps the same readable player while the rain window becomes a rhythmic editorial rail

### Groups

- **g1** — free_design
  - span_sec: [18.28, 25.287]
  - free_design: { dominant_system: "seven identity nodes travel down a measured rain rail", primitives: ["directional-fill", "content-swap", "overlay-pop"], density_topology: "seven-step relay" }
  - anchors: [18.297, 18.53, 20.016, 21.548, 21.896, 22.338, 23.034, 23.847, 24.311, 25.287]
  - copy: ["BUILD", "PHRASE 02", "哈 / 基 / 米 / 南 / 北 / 绿 / 豆"]
- **g2** — free_design
  - span_sec: [25.287, 32.58]
  - free_design: { dominant_system: "rain rings tighten toward the next section while lyric hierarchy stays fixed", primitives: ["mosaic-pack", "outline-to-fill", "negative-space-hold"], density_topology: "pack, hold, open" }
  - anchors: [25.287, 26.796, 27.91, 31.254, 32.044, 32.369]
  - copy: ["RAIN LOOP", "32.58", "NEXT / CLIMAX"]

## Frame 3 — 03-climax

- src: compositions/frames/03-climax.html
- duration: 9.715s
- span_sec: [32.58, 42.295]
- pacing: beat_cut
- mood: [climactic, bold, foreground]
- feel: the climax flips coral into the environment, expands the active syllable, and widens the rain field without sacrificing lyric legibility

### Groups

- **g1** — free_design
  - span_sec: [32.58, 37.314]
  - free_design: { dominant_system: "coral environment with oversized current syllable and converging rain lines", primitives: ["system-replace", "braam-punch", "directional-fill"], density_topology: "instant regime change then build" }
  - anchors: [32.601, 32.903, 33.181, 34.249, 35.016, 35.782, 36.502, 37.314]
  - copy: ["CLIMAX", "HAJIMI", "雨爱"]
- **g2** — free_design
  - span_sec: [37.314, 42.295]
  - free_design: { dominant_system: "wide stereo-like rain panels and an editorial current-line lockup", primitives: ["palette-flip", "outline-to-fill", "freeze-hold"], density_topology: "drop, recover, lock" }
  - anchors: [37.314, 38.011, 38.847, 39.102, 39.497, 41.749, 42.214]
  - copy: ["PHRASE 03", "CURRENT LINE", "雨落成光"]

## Frame 4 — 04-outro

- src: compositions/frames/04-outro.html
- duration: 7.855s
- span_sec: [42.295, 50.15]
- pacing: beat_cut
- mood: [resolved, spacious, reflective]
- feel: the final phrase returns to cream and ink, lets the last long syllable breathe, then leaves a clean internal-demo closing plate

### Groups

- **g1** — free_design
  - span_sec: [42.295, 45.465]
  - free_design: { dominant_system: "cream closing field with the rain window reduced to one calm pool", primitives: ["palette-flip", "blur-resolve", "content-swap"], density_topology: "clear and restate" }
  - anchors: [42.307, 42.539, 43.328, 44.118, 44.42, 45.465]
  - copy: ["OUTRO", "PHRASE 04", "哈基米南北绿豆"]
- **g2** — free_design
  - span_sec: [45.465, 50.15]
  - free_design: { dominant_system: "last lyric holds over a quiet rain ring and final metadata band", primitives: ["outline-to-fill", "negative-space-hold", "staggered-exit"], density_topology: "resolve then hold" }
  - anchors: [45.465, 46.277, 47.02, 47.113, 47.972, 48.506, 49.064, 49.551, 49.993]
  - copy: ["雨爱", "RAIN LOVE", "HAJIMI AUDIO STUDY 01", "INTERNAL EXPERIENCE DEMO"]
