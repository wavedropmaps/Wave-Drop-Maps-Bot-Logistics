# Stolen-Proof Detection — Running Plan & Findings

Living checklist for figuring out a stolen-proof detector that catches real
theft **without false-accusing innocent users**. Tick boxes as we go; findings
logged at the bottom.

**Current config (in code, 2026-06-11):** `STOLEN_CHECKS_ENABLED=True`; EXACT
(SHA-256 + attachment) **enforces** (`STOLEN_WARN_USER=True`) → copy-proof channel;
PERCEPTUAL = 256-bit pHash (`PHASH_HASH_SIZE=16`) @ threshold **20**, **log-only** →
testing channel; mirror-flip variant checked (`PHASH_CHECK_MIRROR=True`); OCR off;
strike counter + filename clue in embeds; collector always stores fingerprints
(even when prooftoggle off / thief warned / pHash fails).
⚠️ **Bot needs a restart/redeploy to run this** — and the 2026-06-11 changes are
not yet committed.

---

## Phase 1 — Collect real data  ⏳ IN PROGRESS
- [x] Confirm collectors run passively with detection off (fingerprint DB + proof_assets archival)
- [x] Obtain a real starter set — **54 Loot Routes images** (drive-download, different users) ✅
- [ ] Grow to ~150–300 images across 50+ distinct users (let bot accumulate)
      ⚠️ the original 54-image export folder (drive-download…) was DELETED locally —
      a fresh export from the server DB/archive is needed before any eval re-run.
- [~] Get **Server 1 (Wave Drop Maps)** proofs — exact matches confirmed working
      there (2 live catches). Still need MULTIPLE different-user Twitter proofs to
      measure perceptual separation — two legit users screenshotting the SAME pinned
      tweet may collide; until measured, never tighten perceptual on Server 1.
- [~] Collect **real confirmed-stolen** cases = gold labels — **4 live-catch pairs**
      saved as `~/Downloads/NEW_*` / `OLD_*` (3 exact, 1 JPG→PNG re-save at dist 0).
      ⚠️ Only copies — back them up somewhere safer before they go the way of the
      54-image folder.

## Phase 2 — Build labeled eval set  ⏳ IN PROGRESS
- [x] Identify negative pairs (different users, same screen) — bulk, free
      (⚠️ source folder deleted — needs re-export for any future run)
- [x] Generate synthetic positive pairs (re-encode / resize / crop of real images)
- [~] Add real confirmed-stolen positives — 4 pairs exist (Downloads NEW_/OLD_)
- [ ] Save as `tests/eval_pairs.csv`

## Phase 3 — Eval harness (score every approach)  ⏳ IN PROGRESS
- [x] A. SHA-256 / attachment (exact baseline)
- [x] B. Full-image pHash 64-bit (current)
- [x] C. High-res pHash 256 / 1024-bit
- [x] D. Background-region hash (crude central mask) — **flopped, mask misaligned**
- [ ] D2. Better dialog localization (brightness-detect / reuse YOLO) for background hash
- [x] E. ORB keypoint matching — **tested 2026-06-10, works as 2nd stage with warp+NCC verify** (see findings)
- [x] Harness script: `tests/eval_harness.py`

## Phase 4 — Metrics & threshold selection  ⏳ IN PROGRESS
- [x] Precision/recall per approach on the 54-image set
- [ ] Re-run on the larger dataset (Phase 1) to confirm thresholds hold
- [ ] Final pick: approach + threshold with best recall @ 100% precision

## Phase 5 — Ship winner + safety net  ✅ DONE (creator-code; Twitter pending data)
- [x] Switch pHash to **256-bit** (`PHASH_HASH_SIZE=16`), threshold **20**
- [x] EXACT signals (SHA-256 + attachment) → **auto-warn user** + log
- [x] PERCEPTUAL signal (256-bit pHash) → **staff review only**, never warns/blocks user
- [x] OCR/username DISABLED (useless on creator-code screen + RAM); EasyOCR never loads
- [x] Dedicated copy-proof channel `1512346144448188486` + detailed old-vs-new diagnostic embed
- [x] Detection RE-ENABLED (`STOLEN_CHECKS_ENABLED = True`)
- [x] Test run on real images: exact→warn, re-encode→staff-only, different-user→nothing ✅
- [ ] Twitter/Server-1 username detection — pending Server 1 data (OCR stays off)
- [ ] Confirm threshold 20 on a larger dataset — user decision 2026-06-11: leave
      everything at 20 / log-only and watch how the current rollout performs first
- [x] Mirror-flip check, strike counter, filename clue, collector leak fixes —
      shipped 2026-06-11 (see findings below)

---

## Findings log

### Eval harness results (51 unique real images, 1275 negative pairs, 153 synthetic-theft positives)
Recall measured at the strictest threshold with **ZERO false positives**:

| approach | safe threshold | closest different-user | margin | recall@100%prec | catches crops? |
|---|---|---|---|---|---|
| phash64   | 5   | 6   | **4 bits / 64 (6%)**  | 67% | no |
| **phash256**  | 45  | 46  | **44 bits / 256 (17%)** | 67% | no |
| phash1024 | 199 | 200 | ~17% | 67% | no |
| dhash256  | 41  | 42  | ~16% | 67% | no |
| whash256  | 25  | 26  | ~9%  | 70% | 5/51 crops |
| bgmask256 | 31  | 32  | ~12% | 67% | no |

### Key conclusions
- **CORRECTION to my earlier claim:** 64-bit pHash is *not* unusable — at its
  ORIGINAL threshold of **5** it has zero false positives here and catches all
  re-encode/resize theft. My mistake was raising it to **10**, which crosses the
  false-positive line (closest different-user pair = 6). **Threshold 10 was wrong.**
- **256-bit pHash is the best choice:** same 100%-precision recall as 64-bit but a
  **far bigger safety margin** (44-bit gap vs 4-bit) → much more robust to new data
  shifting the boundary. Recommended: **256-bit pHash, threshold ~20.**
- **Re-encode + resize theft → 100% caught** by every perceptual approach at 100% precision.
- **Crops → essentially uncatchable** (only whash got 5/51) without hitting different users. Accept this gap.
- **Background masking still didn't beat plain 256-bit.** Drop it unless D2 (smart localization) proves out.
- **3 real exact-duplicate pairs** (same file, 2 message IDs) — exact matching catches
  real in-the-wild re-uploads with zero ambiguity. (Theft vs self-repost = human checks message IDs.)

### Live production catches (log-only mode, observed)
- **Exact (Loot Routes):** @lhhhhhh re-uploaded @Kaku's byte-identical file 1 min
  later → CONFIRMED exact (SHA-256). Real cross-user theft.
- **Perceptual (Loot Routes):** user 734… re-saved user 1454…'s photo as PNG
  (vs original JPG, different bytes) 2 min later → caught by 256-bit pHash (dist 0)
  which SHA-256 would MISS. Proves the perceptual layer's value. REVIEW only.
- **Exact (Server 1 / Twitter):** user 784… re-uploaded user 1224…'s identical
  tweet screenshot 4 min later → CONFIRMED exact. First Server-1 data point; exact
  matching works on Twitter proofs too.
- **4th catch:** second Server-1 exact re-upload (Twitter feed screenshot, byte-identical,
  129 KB pair saved 2026-06-10). All 4 pairs preserved as `NEW_*`/`OLD_*` in Downloads.
- All catches correct; zero false accusations so far.

### Evasion-robustness test (2026-06-10, on 4 real live-catch pairs from Downloads)
All 4 real NEW/OLD catch pairs measured: 3 byte-identical (SHA-256, pHash dist 0),
1 JPG→PNG re-save (pHash-256 dist **0** — nowhere near threshold 20).

**What pHash-256 @ 20 survives vs misses** (transforms applied to the 4 real originals):

| transform | pHash-256 dist | caught @20? |
|---|---|---|
| JPEG q40 re-save | 2–4 | ✅ |
| brightness +30% | 4–8 | ✅ |
| rotate 2° | 30–42 | ❌ |
| flip horizontal | 124–136 | ❌ (trivial fix: also hash mirrored image) |
| crop 10% / crop top 25% | 100–138 | ❌ |
| pad 10% border | 66–92 | ❌ |
| screenshot-of-screenshot (0.7× + border) | 56–126 | ❌ |

**ORB two-stage (ORB → RANSAC homography → warp → NCC on aligned overlap):**
- Positives (rot2/crop10/croptop25/pad10/rescreen on 4 real proofs): inliers 334–790, **NCC 0.955–0.999**
- Hard negatives (different users, same standardized creator-code screen, the two
  highest-ORB-inlier pairs out of 1431): 401 inliers / **NCC 0.458**, 319 / **NCC 0.773**
- Known real dupes in the 54-image set (same filename, 2 msg ids): NCC **1.000** — ORB
  found all 3 real thefts on its own (top-3 inlier pairs of the whole set)
- Raw inlier count alone OVERLAPS (positives min 334 vs negatives max 401) — the
  **NCC ≥ ~0.90 verify step is what gives clean separation** (gap: 0.955 vs 0.773)
- ORB does NOT catch flips (descriptors not mirror-invariant; 16–43 inliers) — use the
  mirrored-pHash check for flips instead.

### Recommendations from this round — user decisions (2026-06-10/11)
1. Tiered perceptual enforcement (≤5 auto-warn) — **REJECTED for now** ("still no").
2. **Mirror pHash — SHIPPED.** Query-time only (nothing extra stored): the flipped
   incoming image's hash is also compared (`PHASH_CHECK_MIRROR=True`). Flipped copies
   match at dist 0. Log-only like all perceptual. NOTE: mirrored negatives never
   measured (dataset folder deleted) — fine while log-only.
3. ORB+warp+NCC second stage — **REJECTED by user** ("3 not work"). Findings above
   stay valid if revisited. Crops remain the accepted gap.
4. **Filename — SHIPPED.** `filename` column stored; embed shows "Filename match"
   line ONLY inside an already-triggered alert, and only for non-generic names
   (GENERIC_FILENAMES blocklist). Supporting clue only, per user.
5. EXIF embed context — not done (EXIF already shown raw in embed).
6. Raising log-only threshold 20→40 — **REJECTED** ("want to see how current goes").

### Shipped from the code audit (2026-06-11)
- **Strike counter:** `stolen_flags` table; every exact/perceptual flag recorded;
  embed shows "Prior flags: N× confirmed + M× look-alike". COUNT ONLY — no action.
- **Leak fix 1:** exact-warn path now stores fingerprints of ALL images in the
  message (pHash computed on the spot) before warning + stopping.
- **Leak fix 2:** `-z prooftoggle` off now runs a collector-only path (download +
  fingerprint + store; no detection/replies/roles). Toggle-off embed text updated.
- **Leak fix 3:** submissions are stored even when pHash fails (phash='' row) so
  the SHA-256/attachment layer still protects PIL-unopenable images (HEIC etc.);
  fuzzy lookups filter `phash!=''`.
- **Robustness:** YOLO batch crash no longer aborts the handler (dead isinstance
  check replaced with try/except; heavy analysis wrapped too) — storage always runs.
- **Side-by-side comparison image** (`COMPARISON_IMAGE_ENABLED=True`): review embeds
  now render one labeled composite (OLD left / NEW right) via `_build_comparison_jpg`;
  full-size NEW_/OLD_ files still attached for download. First real perceptual
  Server-1 catch observed 2026-06-11: two users posting the same casino-scam spam
  image, dist 8 — detector doubles as spam-repost catcher.
- Verified: py_compile clean; functional test on temp DB (exact+filename, same-user
  exclusion, fuzzy straight, fuzzy mirrored dist 0, phash-less row exact-matchable,
  strike counts); migration test from old prod schema (NULL filenames fine).

### Code-level audit of the current pipeline (2026-06-10)
Full read of `Tasks/proof_automation_tasks.py`. Real holes found IN the existing system
(no new detection tech needed to fix):

1. **Exact-match warn skips storage** (`return` at ~L914 happens before the store loop
   ~L936) — the thief's submission is never recorded, and other images in that same
   message are never fingerprinted either.
2. **No repeat-offender memory** — every catch looks like a first offense; flags live
   only as Discord embeds. No strike count, no escalation.
3. **`-z prooftoggle` off also stops the fingerprint collector** (`_is_enabled` gate at
   ~L797 returns before storage) — images posted while toggled off are blind spots
   forever. Contradicts the "submissions still stored" design promise (only the master
   switch honors it).
4. **Images PIL can't open are never stored at all** — storage is gated on
   `if a["phash"]` (~L937), so a failed pHash drops the row INCLUDING its sha256.
   HEIC/corrupt images are permanently unprotected even from exact re-uploads.
5. **Dead exception check in `_run_heavy_analysis`** (~L860) — `run_in_executor`
   raises at the `await`, so a YOLO crash aborts the whole handler before storage.
6. **Single global pHash threshold across both servers** — Server 1 (Twitter
   screenshots of the SAME pinned tweet by different legit users) has a much higher
   natural-collision risk than Loot Routes. Per-guild thresholds needed before any
   perceptual tightening; Server 1 negative data still missing.
7. Empirical: ALL 4 real live catches are dist ≤ 0–2 lazy copies. No edited/cropped
   theft observed in the wild yet. The biggest practical gaps are enforcement tiering
   + offender memory, not detection tech.

### Decision so far (historical — all three since DONE and shipped)
- ~~Ship exact-only now~~ → shipped, enforcing live.
- ~~Add 256-bit pHash @ ~20 as staff heads-up only~~ → shipped, log-only to testing channel.
- ~~Fix threshold 10 / 64-bit before fuzzy re-enable~~ → done (256-bit @ 20).

---

## NEXT ACTIONS (as of 2026-06-11)
0. **USER: retrain YOLO as 8-class** (scams were being forced into proof classes —
   closed-set classifier has no "none of the above"). New classes: **6 = Other /
   Not a proof** (memes/random images → bot stays silent), **7 = Scam** (casino
   spam etc. → staff alert, delete switchable). Bot code is ALREADY 8-class-ready
   (drop-in weights swap): class 6 never acts; class 7 → `_post_scam_alert` to the
   testing channel, `SCAM_DELETE_ENABLED=False` until proven, threshold 0.90.
   ⚠️ TRAINING GOTCHA: ultralytics assigns class indices by ALPHABETICAL folder
   sort — name the new dataset folders so they sort AFTER the existing six
   (e.g. `6_other/`, `7_scam/`), or classes 0–5 shift and every threshold breaks.
1. **Commit + restart/redeploy the bot** — live instance is behind; the strike
   counter, mirror check, filename clue and collector leak fixes only apply after.
2. **Watch the testing channel** (`1512090290922586272`) for perceptual logs —
   gathering evidence on threshold 20 before any tightening (user's call).
3. **Back up the 4 real catch pairs** out of ~/Downloads (only copies that exist).
4. **Collect Server-1 different-user Twitter proofs** (~10+) → measure legit-vs-legit
   pHash distances before ANY perceptual change touches Server 1.
5. **Re-export a bigger Loot Routes set** from the server archive → re-run
   `tests/eval_harness.py` to confirm threshold 20 at scale.
6. Open/parked: D2 dialog-localized region hash; OCR username for Twitter;
   `tests/eval_pairs.csv`; ORB 2nd stage (validated but rejected for now).
