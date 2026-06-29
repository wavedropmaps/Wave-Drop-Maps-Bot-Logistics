# Context: ViT Model Silently Randomizes Weights on transformers Version Mismatch

**Date:** 2026-06-20
**Topic:** The ViT proof model (`Models/model 3.safetensors`, the Model3b node) and its `transformers` version dependency

## The Symptom
The ViT-based proof model (Model3b — the deep "Following + Liking" mobile check) could "load" successfully yet produce nonsensical predictions. Because the auto-decision path can grant proofs at high confidence, garbage-but-confident predictions created a **false-grant** risk.

## The Root Cause
- The ViT model weights (`Models/model 3.safetensors`, a `vit-base-patch16-224-in21k`) are tightly coupled to the specific `transformers` library version they were exported under, and the loader does key-remapping for older ViT layer names.
- Loading them under a mismatched `transformers` version can cause the library to **silently re-initialize** mismatched layers to random weights instead of erroring out — so the model appears to load but is effectively random.
- The Model3b node uses a more lenient `< 0.70` confidence gate (vs `< 0.99` on the YOLO nodes), so random-but-confident outputs are more likely to slip through to an auto-action.

## The Lesson Learned
- **Pin the `transformers` version** in `requirements.txt` to the exact version the ViT was exported with; never silently upgrade it.
- After any environment change, sanity-check the ViT on a known proof / non-proof pair before trusting auto-decisions.
- Treat "loads without error" as NOT the same as "loaded correctly" for safetensors/transformers models.
