#!/usr/bin/env python3
"""
把 raw/ 和 dataset/ 下含中文/特殊字符的文件名替换为 ASCII slug，避免 RVC + ffmpeg 在中文路径上炸。

映射在 SLUG_MAP 里维护。运行后会生成 dataset/zhang-shaohan/filename_map.json 记录原名 ↔ slug。

幂等：再次运行不会动已 slug 化的文件。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 用 substring 匹配做映射，写入文件名包含的关键字 → 短 slug
SLUG_MAP = {
    # 训练素材 · Phase 1
    "阿刁": "adiao",
    "欧若拉": "ouluola",
    "看得最远的地方": "kandezuiyuande",
    "隐形的翅膀": "yinxingdechibang",
    "淋雨一直走": "linyuyizhizou",
    "梦里花": "menglihua",
    # 训练素材 · Phase 2 (高音区扩量)
    "潘多拉": "panduola",
    "潘朵拉": "panduola",   # 别名
    "破茧": "pojian",
    "寓言": "yuyan",
    "香水百合": "xiangshuibaihe",
    "暮色回响": "muselhuixiang",
    "遗失的美好": "yishidemehao",
    # 源歌
    "光年之外": "guangnianzhiwai",
    "雨愛": "yuai",
    "雨爱": "yuai",
}

# 子目录顺序：先 raw（原始）→ source-songs/vocals/no_vocals → zhang-shaohan/vocals → slices
DIRS_TO_PROCESS = [
    PROJECT_ROOT / "raw" / "zhang-shaohan",
    PROJECT_ROOT / "raw" / "source-songs",
    PROJECT_ROOT / "dataset" / "zhang-shaohan" / "vocals",
    PROJECT_ROOT / "dataset" / "source-songs" / "vocals",
    PROJECT_ROOT / "dataset" / "source-songs" / "no_vocals",
    PROJECT_ROOT / "dataset" / "zhang-shaohan" / "slices",
]


def find_slug(name: str) -> str | None:
    for keyword, slug in SLUG_MAP.items():
        if keyword in name:
            return slug
    return None


def safe_rename(directory: Path, mapping: dict[str, str]) -> None:
    if not directory.exists():
        return
    for f in sorted(directory.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".wav":
            continue
        # already slug?
        if all(ord(c) < 128 for c in f.name):
            if "_" in f.stem and any(slug in f.stem for slug in SLUG_MAP.values()):
                continue  # likely already slugged
        slug = find_slug(f.name)
        if not slug:
            print(f"  [warn] no slug for: {f.name}")
            continue
        # detect slice files: stem already ends with _NNN
        stem = f.stem
        suffix = ""
        if "_" in stem:
            tail = stem.rsplit("_", 1)[1]
            if tail.isdigit():
                suffix = f"_{tail}"
        new_name = f"{slug}{suffix}.wav"
        new_path = f.with_name(new_name)
        if new_path.exists() and new_path != f:
            print(f"  [skip] target exists: {new_name}")
            continue
        f.rename(new_path)
        mapping[f.name] = new_name
        print(f"  {f.name[:60]}... → {new_name}")


def main() -> int:
    mapping: dict[str, str] = {}
    for d in DIRS_TO_PROCESS:
        print(f"\n[{d.relative_to(PROJECT_ROOT)}]")
        safe_rename(d, mapping)

    map_path = PROJECT_ROOT / "dataset" / "filename_map.json"
    map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
    print(f"\nMap written: {map_path} ({len(mapping)} renames)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
