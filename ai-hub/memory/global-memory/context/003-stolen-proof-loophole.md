# Context: Stolen Proofs Stored as Legitimate Reference Data

**Date:** 2026-06-20
**Topic:** A stolen-proof detection loophole that polluted the reference set

## The Symptom
Stolen or duplicated proofs were being accepted and then **stored as legitimate reference proofs**. Once in the reference set, they polluted future detection queries — a stolen image could later be matched against itself and treated as "known good," letting subsequent reuse slip through.

## The Root Cause
- The detection flow stored a proof as a reference BEFORE fully validating its provenance, so a stolen proof that passed the initial checks became part of the trusted corpus (`proof_submissions`).
- Future similarity / dedup queries then compared new submissions against these tainted references, weakening the very check meant to catch theft.

## The Lesson Learned
- Validate provenance FIRST; only store a proof as a known-good reference after it clears stolen/duplicate checks.
- An **exact-match layer** was added (SHA-256 byte-identical match + Discord attachment-ID reuse) to catch identical re-submissions before they can be granted or stored, alongside the perceptual-hash (pHash, threshold 20, with mirror check) fuzzy layer.
- Any "store as reference" step in a fraud-detection pipeline must be gated on the fraud check passing — never the other way around.
