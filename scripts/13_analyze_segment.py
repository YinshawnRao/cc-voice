"""
对比同一时间段在 [源干声 / Phase1 转换 / Phase2_e50 转换] 三个版本的客观指标。
诊断"嘶哑/撕裂"的源头：是源干声本身就脏？还是 SVC 转换引入？

输出：
  - 数值表（RMS / spectral flatness / spectral centroid / ZCR / harvest f0 stats）
  - mel-spectrogram PNG（三联图，肉眼对比）
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import librosa
import librosa.display
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
SR = 40000

START_S = float(sys.argv[1]) if len(sys.argv) > 1 else 44.0
END_S = float(sys.argv[2]) if len(sys.argv) > 2 else 47.0

TARGETS = [
    ("source",          ROOT / "dataset/source-songs/vocals/yuai.wav"),
    ("phase1_p050",     ROOT / "outputs/comparison_e50_vs_p1/yuai_phase1_vocals.wav"),
    ("phase2_e50_p050", ROOT / "outputs/comparison_e50_vs_p1/yuai_phase2_e50_vocals.wav"),
    ("phase2_e100_p066",ROOT / "outputs/comparison_e100/yuai_e100_p066_vocals.wav"),
]


def load_window(path: Path, start_s: float, end_s: float) -> np.ndarray:
    y, sr = librosa.load(str(path), sr=SR, mono=True,
                         offset=start_s, duration=end_s - start_s)
    return y


def f0_via_pyin(y: np.ndarray) -> np.ndarray:
    f0, voiced, _ = librosa.pyin(y, sr=SR,
                                 fmin=librosa.note_to_hz("C2"),
                                 fmax=librosa.note_to_hz("C7"),
                                 frame_length=2048)
    f0[~voiced] = np.nan
    return f0


def metrics(label: str, y: np.ndarray) -> dict:
    rms = float(np.sqrt(np.mean(y ** 2)))
    zcr = float(np.mean(librosa.feature.zero_crossing_rate(y)))
    sc = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=SR)))
    sflat = float(np.mean(librosa.feature.spectral_flatness(y=y)))
    f0 = f0_via_pyin(y)
    f0_v = f0[~np.isnan(f0)]
    f0_mean = float(np.mean(f0_v)) if len(f0_v) else float("nan")
    f0_std = float(np.std(f0_v)) if len(f0_v) else float("nan")
    voiced_pct = float(len(f0_v) / len(f0)) if len(f0) else 0.0

    print(f"[{label}]")
    print(f"  RMS              = {rms:.4f}   (响度)")
    print(f"  ZCR              = {zcr:.4f}   (越高→越多噪声/擦音)")
    print(f"  spectral_centroid= {sc:7.1f} Hz (亮度)")
    print(f"  spectral_flatness= {sflat:.4f}   (0=纯音，1=白噪声)")
    print(f"  f0_mean          = {f0_mean:7.1f} Hz")
    print(f"  f0_std           = {f0_std:7.1f} Hz (越大→pitch 抖动越严重)")
    print(f"  voiced%          = {voiced_pct*100:5.1f}%")
    print()
    return dict(label=label, y=y, f0=f0, rms=rms, zcr=zcr, sc=sc, sflat=sflat,
                f0_mean=f0_mean, f0_std=f0_std, voiced_pct=voiced_pct)


def plot_spectrograms(results: list[dict], start_s: float, end_s: float) -> Path:
    fig, axes = plt.subplots(len(results), 1, figsize=(12, 3 * len(results)),
                              sharex=True)
    for ax, r in zip(axes, results):
        S = librosa.feature.melspectrogram(y=r["y"], sr=SR, n_mels=128,
                                            fmax=SR // 2)
        S_dB = librosa.power_to_db(S, ref=np.max)
        img = librosa.display.specshow(S_dB, sr=SR, x_axis="time",
                                        y_axis="mel", ax=ax, fmax=SR // 2)
        ax.set_title(f'{r["label"]}  |  ZCR={r["zcr"]:.3f}  '
                     f'sflat={r["sflat"]:.3f}  f0σ={r["f0_std"]:.1f}Hz')
        fig.colorbar(img, ax=ax, format="%+2.0f dB")
    plt.suptitle(f"yuai mel-spec   {start_s:.1f}s – {end_s:.1f}s")
    plt.tight_layout()
    out = ROOT / "outputs/comparison_e50_vs_p1/diagnose_yuai_44_47.png"
    plt.savefig(out, dpi=110)
    plt.close()
    return out


def main() -> int:
    print(f"=== 分析窗口  {START_S:.1f}s – {END_S:.1f}s  (sr={SR}) ===\n")
    results = []
    for label, path in TARGETS:
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            continue
        y = load_window(path, START_S, END_S)
        results.append(metrics(label, y))
    out = plot_spectrograms(results, START_S, END_S)
    print(f"频谱图 → {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
