# Multi-Image Batch Validation — Implementation Plan

> **STATUS: ✅ IMPLEMENTED (2026-06-23)** via /harness. Both phases shipped in
> `Tasks/proof_automation_tasks.py` (+ `partial_proofs` table in `roles.db`).
> Decision: auto-grant the combo (each half ≥99%). Post-mortem:
> `ai-hub/memory/global-memory/context/004-compound-proof-batch-validation.md`.


## Problem
Users submitting multiple images to satisfy compound requirements (e.g., "Following AND liking") get rejected because each image is evaluated independently and rejected for being incomplete. Example:
- User sends Image 1 (following proof) + Image 2 (liking proof)
- Image 1 routes to Model3a → "Following only" → `REJECT_FOLLOWING_ONLY`
- Image 2 never processed
- User gets "you need to follow" even though they provided both proofs across two images

## Current Flow (Broken)
```python
# Lines 1498-1524 in Tasks/proof_automation_tasks.py
for a in analyses:
    decision = await self.tree.process_image(...)
    if decision.action in ['GRANT_LEVEL_1', 'GRANT_LEVEL_2']:
        await self._execute_decision(...)
        return  # ← PROBLEM: Early exit, Image 2 never runs
    
# Fallback: only reply about first image
if all_rejected:
    await self._execute_decision(decisions[0][0], ...)  # ← Only Image 1 mentioned
```

## Solution: Compound Batch Validation

### Phase 1: Collect All Decisions
- Remove the `return` on line 1509 that exits early on grant
- Let ALL images run through the cascade
- Store all decisions: `decisions = [(decision1, analysis1), (decision2, analysis2), ...]`

### Phase 2: Batch-Level Validation
After all images processed, check if the batch satisfies the compound requirement:

```python
# Pseudo-code
batch_result = await validate_batch_decisions(decisions, cfg)
if batch_result.should_grant:
    await _assign_creator_roles(...)
    return
```

### Phase 3: Validation Logic (new file: `utils/batch_validators.py`)

**Rule 1: Twitter Following + Liking Requirement**
```python
def validate_twitter_batch(decisions):
    """
    Requirement: User must show BOTH following AND liking.
    Accept if:
    - Any single image shows "Following and liking" → GRANT
    - One image shows "Following only" AND another shows "Liking only" → GRANT
    - Otherwise → batch to HITL (let staff verify combination)
    """
    classes = [d[0].class_name for d in decisions]
    
    # Single image covers both
    if "Following and liking" in classes:
        return BatchResult(should_grant=True, reason="single_image_complete")
    
    # Two images cover both
    has_following = any("Following only" in c or "Following and liking" in c for c in classes)
    has_liking = any("Liking only" in c or "Following and liking" in c for c in classes)
    
    if has_following and has_liking:
        return BatchResult(should_grant=True, reason="multi_image_complete")
    
    # Incomplete or uncertain
    return BatchResult(should_grant=False, reason="missing_component")
```

**Rule 2: Creator Code (single image, no combining)**
- Creator code proofs require ONE image showing the code path end-to-end
- No combining allowed (can't submit partial screenshots)
- Keep existing logic: if any image grants → grant immediately, stop

### Implementation Checklist

1. **Create `utils/batch_validators.py`**
   - `BatchResult` dataclass (should_grant, reason, details)
   - `validate_twitter_batch(decisions, guild_config)` → BatchResult
   - `validate_creator_batch(decisions, guild_config)` → BatchResult
   - `select_batch_validator(decisions, cfg)` → returns the right validator

2. **Modify `Tasks/proof_automation_tasks.py` lines 1498-1524**
   - Remove early `return` on line 1509 (let Image 2 process)
   - After loop, call `batch_validator(decisions, cfg)`
   - If validator says grant → assign roles + reply success
   - If validator says reject → batch all to HITL (not just Image 1)

3. **Update rejection fallback (line 1517-1520)**
   - When all images rejected/uncertain → batch ALL to HITL (already done on line 1516)
   - Update the "all rejected" case to batch, not just reply about Image 1

4. **Test cases**
   - ✅ Single image "Following and liking" → grant
   - ✅ Two images "Following only" + "Liking only" → grant
   - ✅ One image "Following only" alone → HITL (incomplete)
   - ✅ Creator code single image → grant (no combining)
   - ✅ Creator code two partial images → HITL (need one complete proof)

## Code Locations

| File | Lines | Change |
|------|-------|--------|
| `Tasks/proof_automation_tasks.py` | 1499-1509 | Remove early return, collect all decisions |
| `Tasks/proof_automation_tasks.py` | 1511-1524 | Add batch validator call |
| `utils/batch_validators.py` | NEW | Batch validation logic |
| `utils/model_nodes.py` | - | No change (routing stays same) |

## Risk & Mitigations

| Risk | Mitigation |
|------|-----------|
| User submits 10 images, system slow | Batch validator only checks decision metadata, not re-running models; O(n) check per image |
| Incorrectly combining unrelated images | Staff HITL review acts as final gate; unclear batches go there |
| Regression on creator-code path | Separate validator per proof type; creator code uses original "first grant wins" logic |

## Next Session Prompt

```
Implement multi-image batch validation for Twitter Following + Liking requirement.

**Problem:** User sends 2 images (following proof + liking proof) in one message. 
Bot rejects the following image because it's "following only", then ignores the liking image.
Root cause: Each image evaluated independently; compound requirement not recognized across images.

**Solution:** 
1. Let ALL images run through model cascade (remove early return on GRANT)
2. After cascade, validate the batch as a whole: does it satisfy "Following AND liking"?
3. If yes → grant access. If no → batch all images to HITL.

**Files to modify:**
- Tasks/proof_automation_tasks.py (lines 1498-1524)
- Create utils/batch_validators.py

**Test cases:**
- Single image "Following and liking" → grant ✅
- Two images "Following only" + "Liking only" → grant ✅
- One image "Following only" alone → HITL ✅
- Creator code: no combining allowed (keep original logic)

See ai-hub/plans/multi-image-batch-validation.md for full design.
```

## References
- `[[multi-image-compound-requirement]]` — Memory note with problem details
- `ai-hub/memory/bot-infrastructure/proof-automation.md` — Current batch processing flow
- `ai-hub/memory/bot-infrastructure/automation-tree.md` — Model routing rules
