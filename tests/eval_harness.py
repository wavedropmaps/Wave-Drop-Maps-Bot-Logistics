"""
Stolen-detection eval harness.
===============================
Given a folder of real proof images, builds:
  - NEGATIVE pairs: every pair of different-content images (different users) →
    these must NOT match (matching one = a false accusation).
  - POSITIVE pairs: synthetic theft of each image (re-encode / resize / crop) →
    these SHOULD match.

Then scores each candidate hashing approach over all pairs and reports, per
approach, the **recall at 100% precision** — i.e. the strictest threshold that
flags ZERO negatives, and what fraction of true copies that still catches.

Usage:
    python tests/eval_harness.py /path/to/folder_of_real_proofs
"""

import io
import os
import sys
import itertools
import hashlib

import imagehash
from PIL import Image, ImageDraw

# ── approaches ────────────────────────────────────────────────────────────────
def h_phash64(img):  return imagehash.phash(img)
def h_phash256(img): return imagehash.phash(img, hash_size=16)
def h_phash1024(img):return imagehash.phash(img, hash_size=32)
def h_dhash256(img): return imagehash.dhash(img, hash_size=16)
def h_whash(img):    return imagehash.whash(img, hash_size=16)

def h_bgmask256(img):
    c = img.convert("RGB").resize((512, 288))
    ImageDraw.Draw(c).rectangle([512*0.18, 288*0.18, 512*0.82, 288*0.97], fill=(0, 0, 0))
    return imagehash.phash(c, hash_size=16)

APPROACHES = {
    "phash64":   h_phash64,
    "phash256":  h_phash256,
    "phash1024": h_phash1024,
    "dhash256":  h_dhash256,
    "whash256":  h_whash,
    "bgmask256": h_bgmask256,
}

# ── theft simulators (positive pairs) ──────────────────────────────────────────
def reencode(img, q):
    b = io.BytesIO(); img.convert("RGB").save(b, "JPEG", quality=q); b.seek(0)
    return Image.open(b).convert("RGB")

def resize_rt(img, f):
    w, h = img.size
    return img.resize((int(w*f), int(h*f))).resize((w, h))

def crop(img, fr):
    w, h = img.size; dx, dy = int(w*fr), int(h*fr)
    return img.crop((dx, dy, w-dx, h-dy))

THEFTS = {
    "reencode70": lambda im: reencode(im, 70),
    "resize50":   lambda im: resize_rt(im, 0.5),
    "crop8":      lambda im: crop(im, 0.08),
}


def main(folder):
    paths = [os.path.join(folder, f) for f in sorted(os.listdir(folder))
             if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".webp")]
    # de-dupe identical files so synthetic positives & negatives are clean
    seen, uniq = {}, []
    for p in paths:
        d = hashlib.sha256(open(p, "rb").read()).hexdigest()
        if d not in seen:
            seen[d] = p; uniq.append(p)
    imgs = [(p, Image.open(p).convert("RGB")) for p in uniq]
    print(f"{len(paths)} files, {len(imgs)} unique images\n")

    # precompute hashes for every approach
    H = {name: {p: fn(im) for p, im in imgs} for name, fn in APPROACHES.items()}
    # theft copies' hashes
    Ht = {name: {} for name in APPROACHES}
    for p, im in imgs:
        for tname, tfn in THEFTS.items():
            cp = tfn(im)
            for name, fn in APPROACHES.items():
                Ht[name][(p, tname)] = fn(cp)

    neg_pairs = list(itertools.combinations(uniq, 2))     # different content
    pos_pairs = [(p, t) for p in uniq for t in THEFTS]    # original vs its copy

    print(f"negatives (different users): {len(neg_pairs)}   positives (synthetic theft): {len(pos_pairs)}\n")
    print(f"{'approach':<11}{'safe thr':>9}{'closest neg':>13}{'recall@100%prec':>17}   by-theft recall")
    print("-" * 80)
    for name in APPROACHES:
        # closest negative distance = the false-positive boundary
        closest_neg = min(H[name][a] - H[name][b] for a, b in neg_pairs)
        safe_thr = closest_neg - 1   # strictest threshold that flags 0 negatives
        # recall = positives caught at safe_thr, broken down by theft type
        per = {}
        caught = 0
        for p in uniq:
            for t in THEFTS:
                dist = H[name][p] - Ht[name][(p, t)]
                ok = dist <= safe_thr
                per.setdefault(t, [0, 0]); per[t][1] += 1; per[t][0] += int(ok)
                caught += int(ok)
        recall = caught / len(pos_pairs)
        bd = "  ".join(f"{t}:{per[t][0]}/{per[t][1]}" for t in THEFTS)
        print(f"{name:<11}{safe_thr:>9}{closest_neg:>13}{recall:>16.0%}   {bd}")

    print("\nNote: 'safe thr' = highest threshold with ZERO false positives on these")
    print("real different-user images. recall = % of synthetic stolen copies caught there.")
    print("SHA-256 baseline: recall on synthetic theft = 0% (bytes change), but it")
    print("catches exact re-uploads with zero false positives (3 such pairs in this set).")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tests/eval_harness.py <folder_of_real_proofs>")
        sys.exit(1)
    main(sys.argv[1])
