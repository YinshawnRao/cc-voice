#!/usr/bin/env python3
"""Localize and enrich the generated HyperFrames host composition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
VIDEO_PATH = "assets/yuai-official-mv-window.mp4"
VIDEO_DURATION = 50.15

HOST_MEDIA_STYLE = """
      #el-yuai-mv {
        position: absolute; left: 76px; top: 164px; width: 928px; height: 642px;
        object-fit: fill; background: #1A1A1A; z-index: 2;
      }
      #el-yuai-mv-overlay {
        position: absolute; left: 76px; top: 164px; width: 928px; height: 642px;
        z-index: 3; pointer-events: none; color: #F5F0E8;
        font-family: Inter, Arial, sans-serif;
      }
      #el-yuai-mv-overlay .mv-caption {
        position: absolute; left: 0; right: 0; bottom: 0; height: 72px;
        display: flex; align-items: center; justify-content: space-between;
        padding: 0 30px; background: rgba(26,26,26,.82);
        border-top: 3px solid rgba(232,93,93,.82);
        font-size: 16px; font-weight: 700; letter-spacing: 3px;
      }
"""

HOST_MEDIA_MARKUP = f"""      <video
        id="el-yuai-mv"
        class="clip"
        src="{VIDEO_PATH}"
        data-start="0"
        data-duration="{VIDEO_DURATION}"
        data-media-start="0"
        data-track-index="2"
        muted
        playsinline
        preload="auto"
      ></video>
      <div
        id="el-yuai-mv-overlay"
        class="clip"
        data-start="0"
        data-duration="{VIDEO_DURATION}"
        data-track-index="3"
      >
        <div class="mv-caption"><span>YU AI / OFFICIAL MV</span><span>HAJIMI AUDIO / 152 BPM</span></div>
      </div>

"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=PROJECT_DIR / "index.html")
    args = parser.parse_args()

    html = args.index.read_text(encoding="utf-8")
    remote_script_start = '<script src="https://cdn.jsdelivr.net/npm/gsap@'
    if remote_script_start in html:
        start = html.index(remote_script_start)
        end = html.index("</script>", start) + len("</script>")
        html = html[:start] + '<script src="assets/gsap.min.js"></script>' + html[end:]
    elif '<script src="assets/gsap.min.js"></script>' not in html:
        raise ValueError("Could not find the GSAP script slot")

    if VIDEO_PATH not in html:
        html = html.replace("    </style>", HOST_MEDIA_STYLE + "    </style>", 1)
        first_frame = '      <div\n        id="el-01-chorus-a"'
        if first_frame not in html:
            raise ValueError("Could not find the first frame mount")
        html = html.replace(first_frame, HOST_MEDIA_MARKUP + first_frame, 1)

    html = html.replace('class="frame"', 'class="frame clip"')
    html = html.replace(
        '        data-track-index="1"\n      ></div>',
        '        data-track-index="1"\n'
        f'        data-width="{CANVAS_WIDTH}"\n'
        f'        data-height="{CANVAS_HEIGHT}"\n'
        '      ></div>',
    )
    html = html.replace(
        f'data-height="{CANVAS_HEIGHT}"\n    >',
        f'data-height="{CANVAS_HEIGHT}"\n      data-fps="30"\n    >',
        1,
    )
    html = html.replace('data-track-index="11" data-volume="0.9"',
                        'data-track-index="11" data-volume="1"')

    if "https://" in html or "http://" in html:
        raise ValueError("Final host still contains a remote URL")
    if html.count('data-composition-src="compositions/frames/') != 4:
        raise ValueError("Expected four frame mounts")
    if html.count(f'src="{VIDEO_PATH}"') != 1:
        raise ValueError("Expected exactly one source-MV video")
    if 'id="el-yuai-mv"' not in html or 'data-media-start="0"' not in html:
        raise ValueError("Source-MV video contract is incomplete")

    args.index.write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "index": str(args.index),
                "bytes": args.index.stat().st_size,
                "remote_urls": 0,
                "frame_mounts": 4,
                "source_mv_videos": 1,
                "source_mv_duration": VIDEO_DURATION,
                "canvas": [CANVAS_WIDTH, CANVAS_HEIGHT],
                "runtime_data": "inlined per frame",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
