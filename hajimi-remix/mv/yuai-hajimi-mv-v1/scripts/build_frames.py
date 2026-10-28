#!/usr/bin/env python3
"""Generate four consistent, deterministic portrait Rain Loop sub-compositions."""

from __future__ import annotations

import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_DIR / "compositions/frames"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


LYRIC_DATA = load_json(PROJECT_DIR / "data/timed_lyrics.json")
AUDIO_DATA = load_json(PROJECT_DIR / "data/audio-bands.json")
TOTAL_DURATION = float(LYRIC_DATA["audio"]["duration_seconds"])
TOTAL_TIME_LABEL = f"{int(TOTAL_DURATION) // 60:02d}:{int(TOTAL_DURATION) % 60:02d}"
SECTION_BY_ID = {section["id"]: section for section in LYRIC_DATA["sections"]}


FRAME_CONFIGS = (
    {
        "id": "01-chorus-a",
        "start": SECTION_BY_ID["chorus-a"]["start_seconds"],
        "end": SECTION_BY_ID["chorus-a"]["end_seconds"],
        "section": "CHORUS A",
        "section_no": "01",
        "descriptor": "RAIN WINDOW / FIRST PHRASE",
        "ground": "#1A1A1A",
        "left": "#E85D5D",
        "left_ink": "#1A1A1A",
        "wallpaper": "rgba(26,26,26,.12)",
        "right": "#F5F0E8",
        "lyric": "#1A1A1A",
        "muted": "#6B6B6B",
        "accent": "#E85D5D",
        "active_bg": "transparent",
        "footer": "#1A1A1A",
        "footer_ink": "#F5F0E8",
        "progress": "#E85D5D",
        "groups": [
            [0.0, "OPEN / 雨窗"],
            [9.149, "RELAY / CURRENT"],
            [15.534, "LAND / PHRASE 01"],
        ],
    },
    {
        "id": "02-build",
        "start": SECTION_BY_ID["build"]["start_seconds"],
        "end": SECTION_BY_ID["build"]["end_seconds"],
        "section": "BUILD",
        "section_no": "02",
        "descriptor": "RAIN ACCUMULATION / SECOND PHRASE",
        "ground": "#E8E0D4",
        "left": "#F5F0E8",
        "left_ink": "#1A1A1A",
        "wallpaper": "rgba(26,26,26,.12)",
        "right": "#1A1A1A",
        "lyric": "#F5F0E8",
        "muted": "#B0B0B0",
        "accent": "#E85D5D",
        "active_bg": "transparent",
        "footer": "#E85D5D",
        "footer_ink": "#1A1A1A",
        "progress": "#1A1A1A",
        "groups": [
            [18.28, "RESTART / PHRASE 02"],
            [25.287, "PACK / NEXT CLIMAX"],
        ],
    },
    {
        "id": "03-climax",
        "start": SECTION_BY_ID["climax"]["start_seconds"],
        "end": SECTION_BY_ID["climax"]["end_seconds"],
        "section": "CLIMAX",
        "section_no": "03",
        "descriptor": "PEAK / FOREGROUND VOCAL",
        "ground": "#E85D5D",
        "left": "#1A1A1A",
        "left_ink": "#F5F0E8",
        "wallpaper": "rgba(245,240,232,.10)",
        "right": "#E85D5D",
        "lyric": "#1A1A1A",
        "muted": "#6B3636",
        "accent": "#E85D5D",
        "active_bg": "#1A1A1A",
        "footer": "#F5F0E8",
        "footer_ink": "#1A1A1A",
        "progress": "#E85D5D",
        "groups": [
            [32.58, "SYSTEM / CLIMAX"],
            [37.314, "WIDE / PHRASE 03"],
        ],
    },
    {
        "id": "04-outro",
        "start": SECTION_BY_ID["outro"]["start_seconds"],
        "end": SECTION_BY_ID["outro"]["end_seconds"],
        "section": "OUTRO",
        "section_no": "04",
        "descriptor": "LAST RAIN RING / FINAL PHRASE",
        "ground": "#F5F0E8",
        "left": "#E8E0D4",
        "left_ink": "#1A1A1A",
        "wallpaper": "rgba(26,26,26,.12)",
        "right": "#F5F0E8",
        "lyric": "#1A1A1A",
        "muted": "#6B6B6B",
        "accent": "#E85D5D",
        "active_bg": "transparent",
        "footer": "#E85D5D",
        "footer_ink": "#1A1A1A",
        "progress": "#1A1A1A",
        "groups": [
            [42.295, "RESET / PHRASE 04"],
            [45.465, "RESOLVE / LAST NOTE"],
        ],
    },
)


TEMPLATE = r'''<!doctype html>
<html lang="zh-CN">
  <head><meta charset="UTF-8" /></head>
  <body>
    <template>
      <style>
        @font-face {
          font-family: "Inter";
          src: url("assets/fonts/inter-400-latin.woff2") format("woff2");
          font-weight: 400;
        }
        @font-face {
          font-family: "Inter";
          src: url("assets/fonts/inter-700-latin.woff2") format("woff2");
          font-weight: 700;
        }
        #root {
          position: absolute;
          inset: 0;
          width: 1080px;
          height: 1920px;
          overflow: hidden;
          container-type: size;
          background: __GROUND__;
          color: __LYRIC__;
          font-family: Inter, "PingFang SC", sans-serif;
          --coral: #E85D5D;
          --coral-dark: #D44A4A;
          --cream: #F5F0E8;
          --cream-dark: #E8E0D4;
          --ink: #1A1A1A;
          --lyric: __LYRIC__;
          --muted: __MUTED__;
          --accent: __ACCENT__;
          --active-bg: __ACTIVE_BG__;
        }
        * { box-sizing: border-box; }
        #stage { position: absolute; inset: 0; overflow: hidden; }
        .left-panel {
          position: absolute; left: 0; top: 0; width: 1080px; height: 870px;
          background: __LEFT__; overflow: hidden;
        }
        .left-panel::before {
          content: ""; position: absolute; inset: 0;
          background: repeating-linear-gradient(45deg, transparent 0 20px, rgba(0,0,0,.06) 20px 40px);
        }
        .wallpaper-number {
          position: absolute; left: 32px; top: 42px;
          font-family: Inter, sans-serif;
          font-size: 218px; line-height: 1; letter-spacing: 10px;
          color: __WALLPAPER__; font-weight: 700;
        }
        .left-meta {
          position: absolute; left: 72px; right: 72px; top: 78px;
          display: flex; justify-content: space-between; align-items: center;
          color: __LEFT_INK__; font: 700 20px/1 Inter, sans-serif;
          letter-spacing: 4px; text-transform: uppercase;
        }
        .rain-window {
          position: absolute; left: 72px; top: 160px; width: 936px; height: 650px;
          overflow: hidden; background: var(--ink); border: 4px solid var(--ink);
        }
        .window-fallback {
          position: absolute; inset: 0; display: grid; place-items: center;
          color: var(--cream); opacity: .28;
          font: 700 20px/1 Inter, sans-serif; letter-spacing: 5px;
        }
        .window-caption {
          position: absolute; left: 34px; right: 34px; bottom: 26px;
          display: flex; justify-content: space-between; color: var(--cream);
          font: 700 16px/1 Inter, sans-serif; letter-spacing: 3px;
        }
        .right-panel {
          position: absolute; left: 0; top: 870px; width: 1080px; height: 910px;
          background: __RIGHT__; padding: 80px 72px 52px 72px; overflow: hidden;
        }
        .right-panel::after {
          content: ""; position: absolute; left: 0; top: 0; width: 100%; height: 5px;
          background: var(--coral);
        }
        .right-header { position: relative; display: flex; justify-content: space-between; align-items: flex-start; }
        .eyebrow {
          color: var(--accent); font: 700 18px/1 Inter, sans-serif;
          letter-spacing: 4px; text-transform: uppercase;
        }
        .group-tag {
          min-width: 250px; text-align: right; color: var(--muted);
          font: 700 15px/1 Inter, sans-serif; letter-spacing: 2px;
        }
        .title-block { position: relative; margin-top: 30px; height: 182px; }
        .title-cn {
          font: 700 142px/.94 "Songti SC", STSong, serif;
          letter-spacing: -5px; color: var(--lyric);
        }
        .title-en {
          position: absolute; right: 0; bottom: 14px;
          font: 700 36px/1 Inter, sans-serif;
          letter-spacing: 6px; color: var(--accent);
        }
        .title-rule { width: 144px; height: 7px; background: var(--accent); margin-top: 16px; }
        .lyric-area { position: relative; height: 410px; margin-top: 10px; }
        .adjacent-line {
          position: absolute; left: 0; right: 0; color: var(--muted);
          font: 400 30px/1.35 "PingFang SC", sans-serif; letter-spacing: 10px;
        }
        .previous-line { top: 8px; }
        .next-line { bottom: 16px; }
        .current-lines { position: absolute; left: 0; right: 0; top: 90px; height: 180px; }
        .lyric-row {
          position: absolute; inset: 0; display: flex; align-items: center;
          gap: 12px; opacity: 0; color: var(--lyric);
          font: 600 80px/1 "PingFang SC", sans-serif;
        }
        .lyric-char {
          display: inline-grid; place-items: center; min-width: 108px; height: 122px;
          border-bottom: 4px solid transparent; color: var(--lyric);
        }
        .current-token-ghost {
          position: absolute; right: -10px; top: 30px; opacity: .08;
          font: 700 230px/1 "Songti SC", STSong, serif; color: var(--lyric);
        }
        .hold-line {
          position: absolute; left: 0; top: 274px; width: 220px; height: 8px;
          background: var(--accent); transform: scaleX(0); transform-origin: left center;
        }
        .identity-block { position: absolute; left: 72px; right: 72px; bottom: 38px; }
        .identity-label {
          color: var(--muted); font: 700 15px/1 Inter, sans-serif;
          letter-spacing: 4px; margin-bottom: 18px;
        }
        .identity-rail { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; }
        .rail-token {
          height: 70px; border-top: 3px solid var(--muted); display: grid; place-items: center;
          color: var(--muted); font: 600 30px/1 "PingFang SC", sans-serif;
        }
        .rail-token.active { border-top-color: var(--accent); color: var(--accent); }
        .footer {
          position: absolute; left: 0; right: 0; bottom: 0; height: 140px;
          background: __FOOTER__; color: __FOOTER_INK__; padding: 30px 72px 24px;
        }
        .footer-line { height: 5px; background: rgba(128,128,128,.28); overflow: hidden; }
        .footer-progress { width: 100%; height: 100%; background: __PROGRESS__; transform: scaleX(0); transform-origin: left; }
        .footer-meta {
          display: grid; grid-template-columns: 1fr auto; gap: 12px 24px; margin-top: 18px;
          font: 700 15px/1 Inter, sans-serif; letter-spacing: 2px;
        }
        .footer-meta span:nth-child(3) {
          grid-column: 1 / -1; color: currentColor; opacity: .72;
        }
        .section-mark {
          position: absolute; right: 72px; bottom: 152px; color: var(--muted);
          font: 700 15px/1 Inter, sans-serif; letter-spacing: 4px;
        }
      </style>

      <div id="root" data-frame-root="__COMP_ID__" data-composition-id="__COMP_ID__" data-duration="__DURATION__" data-width="1080" data-height="1920">
        <div id="stage">
          <section class="left-panel">
            <div class="wallpaper-number">__SECTION_NO__</div>
            <div class="left-meta"><span>MV WINDOW</span><span>__SECTION__</span></div>
            <div class="rain-window">
              <div class="window-fallback">MUTED VISUAL / AUDIO MASTER BELOW</div>
              <div class="window-caption"><span>YU AI / HAJIMI</span><span>152 BPM</span></div>
            </div>
          </section>

          <section class="right-panel">
            <div class="right-header">
              <div class="eyebrow">HAJIMI NOTE / EVENT V1</div>
              <div class="group-tag"></div>
            </div>
            <div class="title-block">
              <div class="title-cn">雨爱</div><div class="title-en">RAIN LOVE</div><div class="title-rule"></div>
            </div>
            <div class="lyric-area">
              <div class="adjacent-line previous-line"></div>
              <div class="current-token-ghost">哈</div>
              <div class="current-lines"></div>
              <div class="hold-line"></div>
              <div class="adjacent-line next-line"></div>
            </div>
            <div class="identity-block">
              <div class="identity-label">CURRENT SYLLABLE / SEVEN-TOKEN IDENTITY RAIL</div>
              <div class="identity-rail"></div>
            </div>
            <div class="section-mark">__DESCRIPTOR__</div>
          </section>

          <footer class="footer">
            <div class="footer-line"><div class="footer-progress"></div></div>
            <div class="footer-meta"><span>HAJIMI AUDIO STUDY 01</span><span class="timecode">00:00 / __TOTAL_TIME_LABEL__</span><span>INTERNAL EXPERIENCE DEMO</span></div>
          </footer>
        </div>
      </div>

      <script>
        (() => {
          const ID = __COMP_ID_JSON__;
          const FRAME_START = __FRAME_START__;
          const FRAME_END = __FRAME_END__;
          const DURATION = __DURATION__;
          const GROUPS = __GROUPS_JSON__;
          // HyperFrames mounts a sub-composition inside the host clip that owns
          // the same composition id. Render compilation may drop helper data
          // attributes from the cloned inner root, so scope through the host
          // composition instead of relying on data-frame-root.
          const root = document.querySelector('[data-composition-id="' + ID + '"]');
          if (!root) throw new Error(ID + ': subcomposition host missing');
          const q = (selector) => root.querySelector(selector);
          const qa = (selector) => Array.from(root.querySelectorAll(selector));
          const lyricData = __LYRICS_JSON__;
          const audioData = __AUDIO_JSON__;

          const cycle = ['哈', '基', '米', '南', '北', '绿', '豆'];
          const rail = q('.identity-rail');
          cycle.forEach((token) => {
            const item = document.createElement('span');
            item.className = 'rail-token';
            item.dataset.token = token;
            item.textContent = token;
            rail.appendChild(item);
          });

          const frameLines = lyricData.lines.filter((line) => line.start_seconds < FRAME_END && line.end_seconds > FRAME_START);
          const frameSyllables = lyricData.syllables.filter((item) => item.start_seconds < FRAME_END && item.end_seconds > FRAME_START);
          const currentLines = q('.current-lines');
          frameLines.forEach((line) => {
            const row = document.createElement('div');
            row.className = 'lyric-row';
            row.dataset.lineId = line.id;
            line.group_ids.forEach((groupId) => {
              const syllable = lyricData.syllables.find((item) => item.group_id === groupId);
              const char = document.createElement('span');
              char.className = 'lyric-char';
              char.dataset.groupId = String(groupId);
              char.textContent = syllable.text;
              row.appendChild(char);
            });
            currentLines.appendChild(row);
          });

          const allLines = lyricData.lines;
          const tl = gsap.timeline({ paused: true });
          const local = (seconds) => Math.max(0, Math.min(DURATION, seconds - FRAME_START));
          // Section boundaries are also phrase resets. Keep the lyric panel and
          // footer visible from the first frame so the reset syllable is never
          // hidden by a repeated scene-entry transition.
          tl.set(q('.left-panel'), { clipPath: 'inset(0 0 0% 0)' }, 0);
          tl.set(q('.right-panel'), { opacity: 1, y: 0 }, 0);
          tl.set(q('.footer'), { y: 0 }, 0);
          tl.fromTo(q('.footer-progress'), { scaleX: FRAME_START / __TOTAL_DURATION__ }, { scaleX: FRAME_END / __TOTAL_DURATION__, duration: DURATION, ease: 'none' }, 0);

          GROUPS.forEach((group, index) => {
            tl.set(q('.group-tag'), { textContent: group[1] }, local(group[0]));
            tl.set(q('.wallpaper-number'), { opacity: index % 2 === 0 ? 1 : 0.58 }, local(group[0]));
          });

          frameLines.forEach((line) => {
            const lineIndex = allLines.findIndex((item) => item.id === line.id);
            const row = q('[data-line-id="' + line.id + '"]');
            const at = local(Math.max(line.start_seconds, FRAME_START));
            qa('.lyric-row').forEach((other) => tl.set(other, { opacity: other === row ? 1 : 0 }, at));
            tl.set(q('.previous-line'), { textContent: lineIndex > 0 ? allLines[lineIndex - 1].text : '雨落在看不见的窗外' }, at);
            tl.set(q('.next-line'), { textContent: lineIndex + 1 < allLines.length ? allLines[lineIndex + 1].text : 'RAIN LOVE / END' }, at);
          });

          frameSyllables.forEach((syllable) => {
            const start = local(Math.max(syllable.start_seconds, FRAME_START));
            const end = local(Math.min(syllable.end_seconds, FRAME_END));
            const char = q('[data-group-id="' + syllable.group_id + '"]');
            const railItems = qa('.rail-token');
            railItems.forEach((item) => tl.set(item, { color: 'var(--muted)', borderTopColor: 'var(--muted)', scale: 1 }, start));
            const activeRail = railItems.find((item) => item.dataset.token === syllable.text);
            tl.set(char, { color: 'var(--accent)', backgroundColor: 'var(--active-bg)', borderBottomColor: 'var(--accent)', scale: 1.06, y: -4 }, start);
            tl.set(activeRail, { color: 'var(--accent)', borderTopColor: 'var(--accent)', scale: 1.05 }, start);
            tl.set(q('.current-token-ghost'), { textContent: syllable.text }, start);
            tl.set(q('.hold-line'), { scaleX: 0 }, start);
            if (end > start) tl.to(q('.hold-line'), { scaleX: 1, duration: end - start, ease: 'none' }, start);
            tl.set(char, { color: 'var(--lyric)', backgroundColor: 'transparent', borderBottomColor: 'transparent', scale: 1, y: 0 }, end);
            tl.set(activeRail, { color: 'var(--muted)', borderTopColor: 'var(--muted)', scale: 1 }, end);
          });

          const lyricArea = q('.lyric-area');
          audioData.frames.forEach((frame) => {
            if (frame.time < FRAME_START || frame.time > FRAME_END) return;
            const at = local(frame.time);
            const mid = (frame.bands[4] + frame.bands[5] + frame.bands[6] + frame.bands[7]) / 4;
            tl.set(lyricArea, { y: -mid * 3 }, at);
          });

          const formatTime = (seconds) => {
            const whole = Math.max(0, Math.floor(seconds));
            return String(Math.floor(whole / 60)).padStart(2, '0') + ':' + String(whole % 60).padStart(2, '0');
          };
          tl.set(q('.timecode'), { textContent: formatTime(FRAME_START) + ' / __TOTAL_TIME_LABEL__' }, 0);
          for (let seconds = Math.ceil(FRAME_START); seconds <= FRAME_END; seconds += 1) {
            tl.set(q('.timecode'), { textContent: formatTime(seconds) + ' / __TOTAL_TIME_LABEL__' }, local(seconds));
          }
          tl.set(root, { opacity: 1 }, DURATION);
          window.__timelines = window.__timelines || {};
          window.__timelines[ID] = tl;
          tl.seek(0);
        })();
      </script>
    </template>
  </body>
</html>
'''


def render_frame(config: dict) -> str:
    duration = round(float(config["end"]) - float(config["start"]), 6)
    audio_slice = {
        "fps": AUDIO_DATA["fps"],
        "duration": duration,
        "totalFrames": sum(
            1
            for frame in AUDIO_DATA["frames"]
            if config["start"] <= frame["time"] <= config["end"]
        ),
        "frames": [
            frame
            for frame in AUDIO_DATA["frames"]
            if config["start"] <= frame["time"] <= config["end"]
        ],
    }
    replacements = {
        "__COMP_ID__": config["id"],
        "__COMP_ID_JSON__": json.dumps(config["id"]),
        "__FRAME_START__": str(config["start"]),
        "__FRAME_END__": str(config["end"]),
        "__DURATION__": str(duration),
        "__TOTAL_DURATION__": str(TOTAL_DURATION),
        "__TOTAL_TIME_LABEL__": TOTAL_TIME_LABEL,
        "__GROUPS_JSON__": json.dumps(config["groups"], ensure_ascii=False),
        "__LYRICS_JSON__": json.dumps(
            LYRIC_DATA, ensure_ascii=False, separators=(",", ":")
        ),
        "__AUDIO_JSON__": json.dumps(
            audio_slice, ensure_ascii=False, separators=(",", ":")
        ),
        "__GROUND__": config["ground"],
        "__LEFT__": config["left"],
        "__LEFT_INK__": config["left_ink"],
        "__WALLPAPER__": config["wallpaper"],
        "__RIGHT__": config["right"],
        "__LYRIC__": config["lyric"],
        "__MUTED__": config["muted"],
        "__ACCENT__": config["accent"],
        "__ACTIVE_BG__": config["active_bg"],
        "__FOOTER__": config["footer"],
        "__FOOTER_INK__": config["footer_ink"],
        "__PROGRESS__": config["progress"],
        "__SECTION__": config["section"],
        "__SECTION_NO__": config["section_no"],
        "__DESCRIPTOR__": config["descriptor"],
    }
    rendered = TEMPLATE
    for needle, value in replacements.items():
        rendered = rendered.replace(needle, value)
    unresolved = [needle for needle in replacements if needle in rendered]
    if unresolved:
        raise ValueError(f"Unresolved placeholders in {config['id']}: {unresolved}")
    return rendered


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary = []
    for config in FRAME_CONFIGS:
        output = OUTPUT_DIR / f"{config['id']}.html"
        content = render_frame(config)
        output.write_text(content, encoding="utf-8")
        summary.append(
            {
                "id": config["id"],
                "path": str(output.relative_to(PROJECT_DIR)),
                "duration": round(config["end"] - config["start"], 6),
                "bytes": len(content.encode("utf-8")),
            }
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
