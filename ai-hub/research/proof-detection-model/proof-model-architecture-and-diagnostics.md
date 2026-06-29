# Proof Detection Model — Architecture, Diagnostics & Decisions

_Living design doc for the image classifier behind the proof-automation system._
_Last updated: 2026-06-13._

This is the **model-training** side (Roboflow + YOLO/training), kept separate from the
bot integration code in `Tasks/proof_automation_tasks.py`. It captures what we found,
what we decided, and what's left.

---

## 1. The goal

For any screenshot a member submits as proof, the system must:
1. Know **what class it is** — or know it's **none** (abstain, no action).
2. Tell a **done** proof (creator code applied / liked+followed) from a **not-done** one.
3. Reject fakes, scams, and garbage.

Framed as a gate, the priorities are:
- **Primary:** never *false-grant* (give a role to a wrong/incomplete proof). False grant
  is the worst error — the user confirmed this.
- **Secondary:** catch the "not done yet" states so real incompletes get told what to fix.
- **Guardrail:** below a confidence threshold → "uncertain" → no action. Abstaining is free;
  a false grant is not.

> Not "99% accuracy on everything" — that's the wrong target. 99% on the **grant decision**
> is the real goal; the fuzzy classes can abstain.

---

## 2. What we diagnosed on the current model

Dataset: Roboflow project `proof-helper-dection` v3, ~2,031 labelled images, 11 raw classes.
Trained weights tested locally: `proof_best (1).pt` (yolo26m-cls, imgsz 1024, 7 classes).

### 2a. The original training was broken by DATA LEAKAGE (biggest finding)
- The split was **random 70/20/10**. But users submit 2–4 near-identical shots per proof,
  and reposts exist. Random splitting put near-duplicate images in BOTH train and test.
- Result: the model **memorised images**, scored well in validation, then failed on every
  genuinely-new user in production ("wrong every time"). No recipe fixes this — only a
  leakage-safe split does.
- **Fix shipped in the v9 notebook:** group near-duplicates (same Discord msg-id OR
  pHash ≤ 6) and force each group entirely into one split. Verified 0 files leak across
  splits. Leakage-free accuracy came back at **~87%** — lower than the fake number, but real.

### 2b. Severe class imbalance + starved classes
Real **unique** image counts (after de-duping reposts — much lower than raw counts):

| class | raw | unique | note |
|---|---|---|---|
| Using creator code correctly | 719 | 513 | healthy (gate class) |
| Liking and Following | 338 | 286 | |
| Liking Only | 233 | 208 | |
| Following Only | 399 | 199 | |
| Zoom Out | 163 | 139 | |
| **Need to press search** | 47 | **39** | 🔴 starved, and it's the not-applied gate |
| **Scam** | 101 | **14** | 🔴 only ~14 unique — rest are reposts |

Tiny junk classes dropped (<40): Invite Proof (16), Invalid twitter (11), Fake Account (3),
Invalid code (1) — too few to learn, only add noise.

### 2c. Per-class results (leakage-free)
- Strong: Following 98%, Zoom Out 97%, Scam 94% (recall), Liking Only 91%, creator code 92%.
- Weak: **Liking and Following 59–68% recall** — the worst class.
- Gate safety: only ~2–4 scam→creator-code false grants in 2,000 — killable with a
  confidence threshold (≥0.95 on the grant class).

### 2d. The two real problems behind the errors (both fixable, neither is "model too dumb")
1. **Liking Only ↔ Liking and Following confusion.** On Twitter mobile the Follow button
   *disappears* when you follow (no "Following" text). The state IS in the pixels, but the
   button is small and the model under-weights it. ~100 "mislabel suspects" (model
   confidently disagrees with the label) were exported to `~/Downloads/proof_suspects/`
   for manual review.
2. **Zoom Out ↔ Using creator code overlap.** Both show the APPLIED panel. The real
   distinction (confirmed by eyeballing): **is the surrounding Fortnite screen visible
   around the panel?** Panel fills the frame = Zoom Out (too tight to verify, anti-fraud
   reject). Panel shown in-context = creator code correctly. The class boundary was
   labelled inconsistently → 32 high-confidence suspects.

### 2e. Label/separability sanity check
Near-identical images sitting in *different* class folders: only **2** out of 2,000
(both creator-code vs zoom-out, which legitimately look alike). So labels are mostly clean
and classes ARE separable — the errors are fuzzy *boundaries* + the starved classes, not
mass mislabelling.

---

## 3. Key principles we locked in (the "why" behind the design)

- **A class = a decision/action, not a look.** Number of classes should match distinct
  actions, not visual variations.
- **Split on what augmentation CAN'T fix; augment away what it can.**
  - **Theme (dark/light mode)** = a colour change → handled by brightness/contrast/saturation
    **augmentation**, NOT a class split. (Data clustering "found" theme only because we fed it
    brightness — a measurement artifact.)
  - **Mobile vs desktop** = a *layout* change (button positions, chrome) → augmentation can't
    convert one to the other → this is a legitimate **split** axis. Aspect ratio routes it
    cleanly (portrait = mobile, landscape = desktop).
- **Narrow questions beat one confused model.** Break a multi-axis class into binary
  decisions, each visually focused → each can hit near-100%.
- **Orthogonal attributes shouldn't share a softmax.** "Applied?" and "zoomed in?" are
  independent; "liking?" and "following?" are independent. Either model them as separate
  binary models (chosen) or multi-label.
- **Leakage-safe splitting is mandatory** — group near-duplicates into one split, always.
- **More UNIQUE images is the real lever** for the starved classes (Scam, press-search).
  Reposts don't count.

### Rules for splitting a class (so it doesn't backfire)
1. Only split on a distinction you can state in **one sentence, apply in one glance**.
   (Ambiguous boundaries → inconsistent labels → the Zoom-Out mess.)
2. Every leaf class needs its own floor of data (~150+ reliable, 300+ strong). Splitting and
   "get a bigger dataset" are the same project.
3. Split only where you SEE confusion, not for its own sake (fragments data, adds boundaries).

---

## 4. The chosen architecture — multi-model cascade

Decided against a single flat model AND against multi-label (multi-label is the cleaner fit
for Twitter like/follow, but the user chose to stay in the YOLO single-label workflow). The
top-level router is what lets each domain use the right approach.

```
submitted screenshot
        │
   Model 1 — gatekeeper:  garbage / twitter / fortnite
   ├── garbage  → REJECT
   ├── twitter  → Model 2 — mobile or desktop?
   │               ├── mobile  → Model 3 (stage 1): following | liking/both
   │               │              ├── following     → following proof (ask for like)
   │               │              └── liking/both   → Model 3b (stage 2): liking only | liking+following
   │               │                                   ├── liking only       → ask to follow
   │               │                                   └── liking+following  → GRANT (mobile)   [UNVALIDATED — see §4a]
   │               └── desktop → Model 4 — liking / following / both
   └── fortnite → Model 5 — zoomed in?
                   ├── yes → REJECT (ask to zoom out)
                   └── no  → Model 6 — press search or applied?
                              ├── press search → ask to press search
                              └── applied      → GRANT (creator code)
```

### 4a. Mobile stage 2 — the open bet (validate before trusting)
Stage 2 tries to split `liking only` vs `liking + following` from a single **mobile post-view**
image. The deciding signal — the author-level "Follow" button (present for non-followers,
gone once followed) — is **small, sometimes off-frame, and ambiguous in absence**. So this
model may or may not learn it.

**Decision rule, set in advance (don't skip):** train it on the ~500 images, then read the
confusion matrix on a **leakage-safe** test set (near-duplicate grouping — NOT a random split).
- liking-only vs both separates at **≥90% on unseen users** → signal is real, keep stage 2.
- lands near **50%** → it's a coin flip (no signal in the pixels), which on a grant = false
  grants. Cut stage 2 and get "both" from **bot accumulation** instead (user submits the profile
  proof too; bot pairs a `following proof` + a `liking proof` → grant).

⚠️ Leakage-free is the whole point: a random split will show a fake ~95% (memorised
near-duplicates) and then false-grant in production — the exact failure that started this work.

(Visual flow diagram was rendered in-chat; this ASCII version is the source of truth.)

### Why this shape
- **Model 1 router is justified** beyond routing: it lets the fortnite branch be single-label
  (mutually exclusive states) — and would let twitter go multi-label later if needed. You
  can't mix label paradigms in one flat model.
- **Model 5/6 binary split** is the clean fix for the orthogonal "zoom vs applied" axes:
  Model 6 only ever sees properly-framed shots, so SEARCH-vs-APPLIED is always read on a
  readable image.

### Class → action table (the thing that stays simple)
| model output | action |
|---|---|
| garbage | reject, ask to resubmit |
| twitter: both (liking + following) | grant Twitter role |
| twitter: liking only | reply "please also follow" |
| twitter: following only | reply "please also like" |
| fortnite: zoomed in | reject, ask to zoom out |
| fortnite: press search | reply "press search to apply the code" |
| fortnite: applied correctly | grant creator-code role |

---

## 5. Per-model status & data needs

| model | classes | status | data need |
|---|---|---|---|
| 1 — gatekeeper | garbage / twitter / fortnite | not trained | garbage set: diverse, well-represented (NOT "way more than real" — that biases toward rejecting real proofs) |
| 2 — mobile/desktop | mobile / desktop | not trained | easy task; aspect ratio nearly decides it. Desktop is the minority (8–36%) → collect more desktop |
| 3 — mobile twitter (stage 1) | following \| liking/both | research | peel off `following` (profile vs post) — clean, separable |
| 3b — mobile twitter (stage 2) | liking only \| liking+following | **bet — validate** | single post-view; signal = author-follow-button (weak/ambiguous). Keep only if ≥90% on leakage-safe split, else cut → bot accumulation (see §4a) |
| 4 — desktop twitter | liking / following / both | not trained | desktop shows "Following" explicitly + tweet view shows like + follow together → easier |
| 5 — zoom check | zoomed-in / not | not trained | relabel on "is surrounding screen visible?" |
| 6 — code state | press search / applied | not trained | **press-search starved (39 unique)** — top data priority |

### Model 5/6 labelling priority (makes the fortnite classes mutually exclusive)
Apply top-down, first match wins:
1. Panel cropped tight, no game screen around it? → **zoom out** (reject, even if it says APPLIED — anti-fraud).
2. Else, shows APPLIED (green check + code in field)? → **creator code correctly** (grant).
3. Else (SEARCH button showing, not applied)? → **press search**.

---

## 6. Open problems

1. **Mobile Twitter follow signal (Model 3).** "Follow button gone" can mean *following* OR
   *this view never showed it*. No model reads a signal that isn't in the pixels. Real fixes:
   require users to submit the **profile page** (where Following is unambiguous), or go
   multi-label. **Product decision, not a model decision.**
2. **Creator-code text verification.** The model can't reliably read the code text — someone
   could type a different code and it still looks valid. Proper fix = Epic Games API check.
   Otherwise accept the risk.
3. **Console vs PC fortnite styling** (blurry TV photo w/ controller icons vs crisp PC
   screenshot w/ keyboard icons). Don't split — keep variety within each class + colour aug.
4. **Cascade compounding.** 6 models deep: end-to-end accuracy = product of stages. Each
   stage must be near-perfect or errors multiply. Budget for it; keep each model simple.

---

## 7. Training recipe (corrected from the broken v8)

What was wrong in v8 and the fix (these are baked into the v9 notebook):

| setting | v8 (bad) | v9 (fixed) | why |
|---|---|---|---|
| split | random 70/20/10 | leakage-safe (near-dup grouping) | stop memorisation leak |
| model | yolo26x | yolo26m | x overfits a ~1.4k set |
| imgsz | 2560 | 1024 | enough to read the badge; lets batch grow |
| batch | 6 | 32 | stable normalisation — single biggest lift |
| erasing / mixup / scale / perspective | on | **off** | they erase/warp the discriminating badge |
| hsv / brightness / contrast | mild | **on** | makes the model theme-invariant (handles dark/light for free) |
| dropout / label smoothing | 0.3 / 0.1 | 0.1 / 0.05 | over-regularised before |

Rule of thumb: **colour augmentation ON** (safe — recolours without moving the badge),
**geometric/erasing augmentation OFF** (destroys the fine UI detail that separates classes).

---

## 8. Artifacts (where things live)

- **v9 training notebook:** `~/Downloads/proof_helper_v9.ipynb` — leakage-safe split + capping +
  fixed recipe + confidence-threshold analysis cell. Run top-to-bottom on GPU (Kaggle/Lightning).
- **Trained weights tested:** `~/Downloads/proof_best (1).pt` (the leakage-fixed single 7-class
  model — the baseline before the cascade redesign).
- **Mislabel review folders:** `~/Downloads/proof_suspects/` — 103 images the model confidently
  disagrees with, sorted `labelled_X__MODEL_SAYS__Y`, filename prefix = confidence×1000.
- **Roboflow project:** workspace `fruss849-gmail-com`, project `proof-helper-dection`, v3.

---

## 9. Next steps (build order)

1. **Relabel pass** — work through `~/Downloads/proof_suspects/`; fix genuine mislabels.
   Resolve the Zoom-Out vs creator-code boundary on the "surrounding screen visible?" rule.
2. **Collect targeted data** — press-search (39→150+), Scam (14→100+ varied), desktop Twitter.
3. **Decide the mobile follow-signal product rule** (profile-page requirement?).
4. **Restructure Roboflow** into the cascade's per-model datasets (or keep one project and
   split at train time).
5. **Train Model 1 (gatekeeper) and Model 6 (code state) first** — highest value, mostly
   ready. Then 2/4 (easy), then 5, then 3 (hardest).
6. Wire `class → action` table into the bot only after each model clears its bar.
