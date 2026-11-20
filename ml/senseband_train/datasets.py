"""Dataset download + safe extraction + loaders (all returning data in the BAND frame / 250 Hz).

Downloaded archives are UNTRUSTED data: each goes in its own directory, extraction refuses path traversal and
absolute paths and caps total size, and loaders only ever read numeric files from the extracted tree.

Real data used (see docs/04_ML_MODELS.md):
  uci_har   30 people, waist-worn phone, 50 Hz                  -> M1 (activity / posture)
  pamap2    9 people, chest IMU + heart rate, 100 Hz             -> M1 (running etc.), M5 (expected HR)
  cinc2017  8,528 single-lead ECG recordings, AF / normal / other / noisy -> M3 (quality), M4 (rhythm)
  mitbih    (optional) MIT-BIH arrhythmia, via wfdb              -> script only, not used by default
  sisfall   falls + ADLs: the download host was unreachable when this was written; download by hand and use
            `load_sisfall(dir)` (M2 is trained on SIMULATED falls until then, and is flagged as such)
"""
from __future__ import annotations

import csv
import io
import shutil
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
from scipy.io import loadmat
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1] / "data"
RAW, OUT = ROOT / "raw", ROOT

URLS = {
    "uci_har": "https://archive.ics.uci.edu/static/public/240/human+activity+recognition+using+smartphones.zip",
    "pamap2": "https://archive.ics.uci.edu/static/public/231/pamap2+physical+activity+monitoring.zip",
    "cinc2017": "https://physionet.org/files/challenge-2017/1.0.0/training2017.zip",
}
MAX_EXTRACT_BYTES = 6 * 1024 ** 3


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1024:
        return dest
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:   # noqa: S310 - fixed https URLs above
        shutil.copyfileobj(r, f, 1 << 20)
    return dest


def safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    """Extract without trusting member names. Raises on traversal, absolute paths or zip bombs."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(zip_path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_EXTRACT_BYTES:
            raise ValueError("archive too large to extract safely")
        for info in z.infolist():
            target = (dest / info.filename).resolve()
            if dest != target and dest not in target.parents:
                raise ValueError(f"unsafe path in archive: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            out.append(target)
    return out


def fetch(name: str) -> Path:
    """Download (if needed) and extract one dataset into ml/data/<name>/. Returns that directory."""
    final = OUT / name
    if final.exists() and any(final.iterdir()):
        return final
    z = download(URLS[name], RAW / name / f"{name}.zip")
    safe_extract(z, final)
    for inner in list(final.glob("*.zip")):                 # UCI wraps the real archive in another zip
        safe_extract(inner, final)
    return final


# ----------------------------------------------------------------------------------------------- helpers
def align_up(acc: np.ndarray, up: np.ndarray) -> np.ndarray:
    """Rotate a dataset's accelerometer frame so that `up` (gravity direction when standing) becomes +z.
    One rotation per DATASET (not per person), so posture classes keep realistic variation."""
    up = up / np.linalg.norm(up)
    z = np.array([0.0, 0.0, 1.0])
    v, c = np.cross(up, z), float(up @ z)
    if np.linalg.norm(v) < 1e-9:
        return acc if c > 0 else acc * np.array([1, -1, -1])
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    r = np.eye(3) + k + k @ k * (1 / (1 + c))
    return acc @ r.T


# ----------------------------------------------------------------------------------------------- UCI HAR
HAR_CLASSES = {1: "walking", 2: "stairs", 3: "stairs", 4: "sitting", 5: "standing", 6: "lying"}


def load_uci_har(root: Path | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(windows (n,128,3) in g, band frame; labels (str); subject ids) for all 30 subjects, 50 Hz."""
    base = next((root or OUT / "uci_har").rglob("UCI HAR Dataset"))
    xs, ys, ss = [], [], []
    for split in ("train", "test"):
        sig = np.stack([np.loadtxt(base / split / "Inertial Signals" / f"total_acc_{a}_{split}.txt")
                        for a in "xyz"], axis=-1)
        xs.append(sig)
        ys.append(np.loadtxt(base / split / f"y_{split}.txt", dtype=int))
        ss.append(np.loadtxt(base / split / f"subject_{split}.txt", dtype=int))
    x, y, s = np.concatenate(xs), np.concatenate(ys), np.concatenate(ss)
    up = x[y == 5].reshape(-1, 3).mean(axis=0)               # standing windows define "up"
    x = np.stack([align_up(w, up) for w in x])
    return x.astype(np.float32), np.array([HAR_CLASSES[i] for i in y]), s


# ----------------------------------------------------------------------------------------------- PAMAP2
PAMAP_CLASSES = {1: "lying", 2: "sitting", 3: "standing", 4: "walking", 5: "running", 6: "exercising",
                 7: "exercising", 9: "sitting", 10: "sitting", 11: "sitting", 12: "stairs", 13: "stairs",
                 17: "standing", 18: "standing", 20: "exercising", 24: "exercising"}


def pamap_subject_files(root: Path | None = None) -> list[Path]:
    return sorted((root or OUT / "pamap2").rglob("subject10*.dat"))


def load_pamap2(root: Path | None = None, win: int = 128, hop: int = 128
                ) -> dict[str, np.ndarray]:
    """Chest IMU (+/-16 g channel) windows at 50 Hz in the band frame, with mean heart rate per window.

    Returns arrays: x (n,win,3) g, y (str), subject (int), hr (n,) bpm (nan if missing), t (n,) window start s
    (seconds into that subject's recording, for the exertion-history feature)."""
    files = pamap_subject_files(root)
    if not files:
        raise FileNotFoundError("PAMAP2 not found: run `python -m senseband_train.prepare pamap2`")
    raw: list[tuple[int, np.ndarray]] = []
    for f in files:
        d = np.genfromtxt(f, delimiter=" ", usecols=(0, 1, 2, 21, 22, 23))
        raw.append((int(f.stem[-3:]), d))
    # one "up" for the dataset, from all standing samples
    stand = np.concatenate([d[d[:, 1] == 3][:, 3:6] for _, d in raw])
    up = np.nanmean(stand, axis=0)
    xs, ys, ss, hrs, ts = [], [], [], [], []
    for subj, d in raw:
        d = d[d[:, 1] > 0]                                      # drop transient (0)
        acc = d[:, 3:6] / 9.80665
        ok = np.isfinite(acc).all(axis=1)
        acc, lab, hr = acc[ok], d[ok, 1].astype(int), d[ok, 2]
        t0 = d[ok, 0]
        acc = align_up(acc, up / 9.80665)
        acc = resample_poly(acc, 1, 2, axis=0)                  # 100 Hz -> 50 Hz
        lab, hr, t0 = lab[::2][: len(acc)], hr[::2][: len(acc)], t0[::2][: len(acc)]
        for i in range(0, len(acc) - win, hop):
            seg = lab[i:i + win]
            u, c = np.unique(seg, return_counts=True)
            top = int(u[np.argmax(c)])
            if c.max() < 0.9 * win or top not in PAMAP_CLASSES:
                continue
            xs.append(acc[i:i + win].astype(np.float32))
            ys.append(PAMAP_CLASSES[top])
            ss.append(subj)
            h = hr[i:i + win]
            hrs.append(float(np.nanmean(h)) if np.isfinite(h).any() else np.nan)
            ts.append(float(t0[i]))
    return {"x": np.stack(xs), "y": np.array(ys), "subject": np.array(ss), "hr": np.array(hrs), "t": np.array(ts)}


# ----------------------------------------------------------------------------------------------- CinC 2017
CINC_LABELS = {"N": "normal", "A": "af", "O": "other", "~": "noisy"}


def iter_cinc2017(root: Path | None = None, fs_out: int = 250, limit: int | None = None
                  ) -> Iterator[tuple[str, str, np.ndarray]]:
    """Yield (record id, label, ECG in mV resampled to fs_out) for every labelled recording."""
    base = next((root or OUT / "cinc2017").rglob("REFERENCE.csv")).parent
    with open(base / "REFERENCE.csv", newline="") as f:
        rows = list(csv.reader(f))
    for n, (rid, lab) in enumerate(rows):
        if limit and n >= limit:
            break
        m = loadmat(base / f"{rid}.mat")["val"].astype(np.float64).ravel() / 1000.0
        yield rid, CINC_LABELS[lab], resample_poly(m, fs_out // 50, 300 // 50)


# ----------------------------------------------------------------------------------------------- optional
def fetch_mitbih(dest: Path | None = None) -> Path:   # pragma: no cover - needs network
    import wfdb
    d = dest or OUT / "mitbih"
    wfdb.dl_database("mitdb", str(d))
    return d


def load_sisfall(root: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:   # pragma: no cover - manual data
    """SisFall (waist ADXL345 +/-16 g at 200 Hz): files like `SA01/D01_SA01_R01.txt` / `F01_...`.
    Returns 4 s windows at 50 Hz around the strongest sample, label 'fall' (F*) / 'adl' (D*), subject ids."""
    from scipy.signal import resample_poly as rp
    xs, ys, ss = [], [], []
    for f in sorted(root.rglob("*.txt")):
        if f.name[0] not in "DF" or "_" not in f.name:
            continue
        txt = f.read_text().replace(";", "")
        d = np.loadtxt(io.StringIO(txt), delimiter=",", usecols=(0, 1, 2))
        acc = d * (32.0 / 2 ** 13)                              # ADXL345 counts -> g
        acc = rp(acc, 1, 4, axis=0)                             # 200 -> 50 Hz
        k = int(np.argmax(np.linalg.norm(acc, axis=1)))
        lo = max(0, min(k - 100, len(acc) - 200))
        xs.append(acc[lo:lo + 200])
        ys.append("fall" if f.name[0] == "F" else "adl")
        ss.append(int("".join(c for c in f.parent.name if c.isdigit()) or 0))
    return np.stack(xs).astype(np.float32), np.array(ys), np.array(ss)


# ----------------------------------------------------------------------------------------------- own data
def load_recordings(directory: Path, win: int = 128, hop: int = 128) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Windows from 'Record training data' files (edge/senseband_edge/record.py).

    Returns (windows (n, win, 3) g at 50 Hz, labels from the file names/metadata, group ids = file stems)."""
    xs, ys, gs = [], [], []
    for f in sorted(directory.glob("*.npz")):
        z = np.load(f, allow_pickle=False)
        acc = z["acc_g"].astype(np.float32)
        label = str(z["meta_label"]) if "meta_label" in z.files else f.stem.split("_", 1)[-1]
        for i in range(0, len(acc) - win, hop):
            xs.append(acc[i:i + win])
            ys.append(label)
            gs.append(f.stem)
    if not xs:
        return np.zeros((0, win, 3), np.float32), np.array([]), np.array([])
    return np.stack(xs), np.array(ys), np.array(gs)
