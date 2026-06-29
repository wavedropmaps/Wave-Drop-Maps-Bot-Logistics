"""
pHash threshold tuning / validation for proof stolen-detection.
================================================================
Two modes:

1. Synthetic (default):
       python tests/phash_tune.py
   Builds a fake "SUPPORT A CREATOR" proof, generates the kinds of copies
   people actually make (re-encode, resize, crop, screenshot-of-screenshot,
   overlay) plus several *different users*, and prints the pHash Hamming
   distance for each. Use it to pick PHASH_DUPE_THRESHOLD.

2. Real images:
       python tests/phash_tune.py /path/to/folder_of_proof_images
   Computes the nearest-neighbour distance between every pair of real images
   and lists the closest pairs. THIS is the one that matters — synthetic data
   can't tell you how similar your real Fortnite store screenshots are to each
   other. If genuinely different users come out at low distances on real data,
   lower the threshold (or rely on SHA-256 / attachment checks instead).

Requires: pillow, imagehash, numpy.
"""

import io
import os
import sys

import imagehash
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

# Keep in sync with Tasks/proof_automation_tasks.py
PHASH_DUPE_THRESHOLD = 10


def _phash(img):
    return imagehash.phash(img)


# ── Synthetic proof generator ────────────────────────────────────────────────
def make_proof(seed=0, code="wavedropmaps", panel=True):
    import numpy as np
    rng = np.random.RandomState(seed)
    h, w = 720, 1280
    base = np.zeros((h, w, 3), np.uint8)
    for _ in range(40):
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        col = rng.randint(0, 255, 3)
        rr = rng.randint(80, 400)
        yy, xx = np.ogrid[:h, :w]
        base[(xx - cx) ** 2 + (yy - cy) ** 2 < rr * rr] = col
    img = Image.fromarray(base).filter(ImageFilter.GaussianBlur(8))
    if panel:
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([380, 210, 900, 520], radius=24, fill=(40, 30, 70))
        d.text((430, 250), "SUPPORT A CREATOR", fill=(235, 235, 245))
        d.text((430, 300), "Your in-game purchases help support this creator.", fill=(190, 190, 205))
        d.rounded_rectangle([430, 360, 760, 400], radius=12, fill=(70, 60, 100))
        d.text((445, 372), code, fill=(230, 230, 240))
        d.rounded_rectangle([430, 430, 560, 470], radius=10, fill=(60, 90, 60))
        d.text((470, 442), "APPLIED", fill=(180, 230, 180))
    return img


def _reencode(img, q):
    b = io.BytesIO(); img.save(b, "JPEG", quality=q); b.seek(0)
    return Image.open(b).convert("RGB")


def _resize_rt(img, f):
    w, h = img.size
    return img.resize((int(w * f), int(h * f))).resize((w, h))


def _crop(img, fr):
    w, h = img.size; dx, dy = int(w * fr), int(h * fr)
    return img.crop((dx, dy, w - dx, h - dy))


def _sss(img):
    x = _resize_rt(img, 0.6).filter(ImageFilter.GaussianBlur(0.8))
    return _reencode(ImageEnhance.Brightness(x).enhance(0.92), 75)


def _overlay(img):
    x = img.copy(); ImageDraw.Draw(x).text((40, 40), "@someuser", fill=(255, 255, 0))
    return x


def run_synthetic():
    orig = make_proof(seed=1, code="wavedropmaps")
    ph = _phash(orig)

    def d(img):
        return _phash(img) - ph

    print(f"PHASH_DUPE_THRESHOLD = {PHASH_DUPE_THRESHOLD}\n")
    print("== Copies of ONE proof — SHOULD be flagged (dist ≤ threshold) ==")
    copies = [
        ("re-encode JPEG q90", _reencode(orig, 90)),
        ("re-encode JPEG q50", _reencode(orig, 50)),
        ("resize 50% roundtrip", _resize_rt(orig, 0.5)),
        ("resize 30% roundtrip", _resize_rt(orig, 0.3)),
        ("crop 5% border", _crop(orig, 0.05)),
        ("crop 15% border", _crop(orig, 0.15)),
        ("screenshot-of-screenshot", _sss(orig)),
        ("add @username overlay", _overlay(orig)),
    ]
    for n, im in copies:
        dist = d(im)
        print(f"  {n:<28} dist={dist:<3} {'FLAG' if dist <= PHASH_DUPE_THRESHOLD else 'miss'}")

    print("\n== Different users (different backgrounds) — should NOT flag ==")
    worst = 999
    for s in (2, 3, 4, 5, 6):
        im = make_proof(seed=s, code=f"user{s}code")
        dist = d(im)
        worst = min(worst, dist)
        print(f"  different user seed={s:<2} dist={dist:<3} {'FALSE-POSITIVE!' if dist <= PHASH_DUPE_THRESHOLD else 'ok'}")
    print(f"\nClosest different-user distance: {worst} (must stay > threshold {PHASH_DUPE_THRESHOLD})")


def run_real(folder):
    paths = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
             if os.path.splitext(f)[1].lower() in
             (".png", ".jpg", ".jpeg", ".webp", ".bmp")]
    if len(paths) < 2:
        print(f"Need ≥2 images in {folder}; found {len(paths)}.")
        return
    hashes = []
    for p in paths:
        try:
            hashes.append((p, imagehash.phash(Image.open(p))))
        except Exception as e:
            print(f"  skip {p}: {e}")
    pairs = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            pairs.append((hashes[i][1] - hashes[j][1], hashes[i][0], hashes[j][0]))
    pairs.sort()
    print(f"{len(hashes)} images, {len(pairs)} pairs. Closest pairs (dist ≤ threshold would be flagged):\n")
    for dist, a, b in pairs[:25]:
        flag = "FLAG" if dist <= PHASH_DUPE_THRESHOLD else ""
        print(f"  dist={dist:<3} {flag:<4} {os.path.basename(a)}  <->  {os.path.basename(b)}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_real(sys.argv[1])
    else:
        run_synthetic()
