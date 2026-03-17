# Project Miru: Deck Recognition Feature Design

This document specifies the architecture and phased implementation plan for Miru's **Deck Recognition** feature — the ability to accept an image (screenshot, photo, or video frame) of a deck and automatically identify the cards, returning a structured decklist that can be loaded directly into the Deck Builder.

The feature is governed by Miru's **Verified Intelligence** philosophy throughout: every card identification carries a confidence score, uncertain matches are surfaced for user review, and nothing is committed to the Deck Builder without explicit user confirmation. Miru never silently fabricates a card match.

---

## 1. Summary

### What the feature does

1. User uploads an image of their deck (screenshot, flat spread photo, or real-world photo).
2. Miru processes the image through a recognition pipeline appropriate to the image type.
3. Every recognized card receives a confidence score and match method trace.
4. Cards are split into **high confidence** (auto-tentatively accepted) and **needs review** (flagged for user input) buckets.
5. User reviews the result in a side-by-side interface — confirming, correcting, or removing cards.
6. On confirmation, the deck is written to `miru_user_decks.db` with full provenance trace.

### What it is not

- It is not a "one click, done" feature. User review is always part of the flow.
- It is not a replacement for manual deck entry — it is an accelerator.
- Phase 1 does not require any ML model training. Phases 2 and 3 do.

### Relationship to Verified Intelligence

Confidence scoring in recognition mirrors confidence scoring in insights. The system uses the same vocabulary: it distinguishes what it knows from what it inferred, and it never upgrades a guess to a certainty. The review UI makes confidence scores visible to the user — not hidden behind a false sense of accuracy.

---

## 2. Recommended Phased Implementation

### Phase 1 — Screenshot / Digital Decklist Image

**Target input:** A screenshot or clean photo of text-based decklist output — from the One Piece Card Game official builder, TopDecked, Limitless, YouTube deck reveals, Discord, or similar.

**Why first:** Maximum yield for minimum complexity. One Piece card codes (`OP01-001`, `ST01-002`, `P-003`) are structured, machine-readable, and unambiguous. OCR on digital screenshots is solved problem space. No ML training required. Covers the most common deck-sharing workflow.

**Confidence ceiling:** 0.99 (exact code match against catalog).

**Expected high-confidence rate:** 85–95% for clean digital screenshots.

---

### Phase 2 — Flat Card Spread Photo

**Target input:** Cards laid face-up on a flat surface, minimal overlap, reasonably even lighting. A "decklist photo" taken before a tournament or for sharing.

**Why second:** Structured scene — cards have clear boundaries, full fronts visible, consistent orientation. Visual matching against catalog images is tractable with hash or embedding comparison.

**Requires:** Card segmentation, perspective correction, visual similarity matching, catalog image index (already partially exists).

**Confidence ceiling:** ~0.90 (visual hash similarity is not perfect; alt arts and sleeve design can confuse).

**Expected high-confidence rate:** 60–80% depending on lighting, sleeve glare, and card condition.

---

### Phase 3 — Real-World Deck Photo

**Target input:** Cards in hand, in a deck box, fanned out, or partially visible (only top edges). Game state photos where the deck is partially deployed.

**Why third:** Genuinely hard. Partial occlusion, perspective distortion, glare on sleeves, only the top strip of each card is visible. Requires substantially more sophisticated CV and has a much higher expected review burden.

**Requires:** Top-edge-strip recognition (partial card identification), perspective distortion correction, ensemble of OCR (visible card name text) + visual matching, multi-frame aggregation if video.

**Confidence ceiling:** ~0.75 (partial visibility is a fundamental information loss).

**Expected high-confidence rate:** 30–60%; most cards will enter the review flow.

---

## 3. Recognition Pipeline Design

### Phase 1 Pipeline — Screenshot OCR

```
UPLOAD
  │
  ▼
[1] Image validation
    - MIME type check (JPEG, PNG, WEBP)
    - Max file size: 10 MB
    - Min dimensions: 300 × 200 px
    - Reject non-image files early, not silently
  │
  ▼
[2] Preprocessing
    - Convert to greyscale for OCR
    - Deskew (detect and correct rotation up to ±10°)
    - Adaptive contrast enhancement (CLAHE)
    - Upscale to ≥300 DPI equivalent if small
    - Output: normalized greyscale image
  │
  ▼
[3] OCR extraction
    - Engine: EasyOCR (primary) or Tesseract (fallback)
    - Language: English (card names) + digit detection
    - Output: list of (text_block, bounding_box, ocr_confidence)
    - Preserve spatial layout — adjacent blocks carry quantity context
  │
  ▼
[4] Card code extraction (primary path)
    Regex: r'(?:OP|ST|EB|PRB|P|DON)[\-–]?\d{2}[\-–]\d{3}'
    - Normalize: strip OCR artefacts (em-dash → hyphen, 0 → O correction)
    - Look up each extracted code in the catalog
    - Result: matched_code, confidence = 0.95 (OCR found it) → 0.99 (in catalog)
  │
  ▼
[5] Card name extraction (secondary path — for codes OCR missed)
    - Extract text blocks that match One Piece naming patterns
    - Fuzzy match against cards.card_name using rapidfuzz (token_sort_ratio)
    - Confidence = match_score / 100, capped at 0.85 (names are less unique than codes)
    - Apply set context: if a set code was seen nearby, restrict search to that set
  │
  ▼
[6] Quantity extraction
    - Scan for quantity signals adjacent to each card reference:
        - "×4", "x4", "4x", "(4)", "4 copies", "4　" (full-width digit)
    - Validate against One Piece TCG rules: max 4 copies per non-leader card
    - Default to 1 if no quantity signal found (flag for review)
  │
  ▼
[7] Leader detection
    - Identify which card is type=Leader in catalog matches
    - Validate: exactly one leader expected
    - If zero leaders found: low overall confidence warning
    - If multiple leaders: flag conflict for review
  │
  ▼
[8] Candidate assembly
    For each recognized region:
      candidates = [
        {"code": "OP01-001", "confidence": 0.98, "method": "code_regex"},
        {"code": "OP01-002", "confidence": 0.61, "method": "name_fuzzy"},
        ...
      ]
    Sort candidates by confidence DESC
    Top candidate becomes matched_code for this region
  │
  ▼
[9] Job record written to recognition_jobs + recognition_items
    Status → 'review'
    Response returned to client: job_id, item summary, review URL
```

---

### Phase 2 Pipeline — Flat Spread Visual Recognition

Extends Phase 1 with a card detection stage before text/OCR extraction:

```
UPLOAD
  │
  ▼
[1–2] Image validation + preprocessing (same as Phase 1)
  │
  ▼
[3] Card segmentation
    - Contour detection to find rectangular regions
    - Filter by aspect ratio (~0.71 for portrait One Piece cards)
    - Filter by minimum size (reject noise)
    - Apply perspective transform (homography) per card region
    - Output: list of normalized per-card image crops
  │
  ▼
[4] Per-card visual matching
    Method A — Perceptual hash (pHash):
      - Compute pHash for each crop
      - Compare against pre-computed pHash index of catalog images
      - Confidence = 1 - (hamming_distance / 64), threshold ≥ 0.85
      - Fast, no GPU needed, suitable for clean unsleeved photos

    Method B — Dense visual embedding (Phase 2b, requires GPU):
      - Encode each crop with a lightweight ViT or EfficientNet encoder
      - Nearest neighbor in embedding space
      - Confidence = cosine_similarity score
      - More robust to sleeve colour, lighting variation, alt arts
  │
  ▼
[5] OCR as verification layer
    - Run Phase 1 OCR pipeline on each crop
    - If OCR extracts a card code that matches the visual match → confidence boost (+0.05)
    - If OCR code contradicts visual match → flag both as LOW confidence for review
  │
  ▼
[6] Duplicate counting → quantity
    - Group by matched_code
    - Count of duplicate matches = quantity
    - Validate: ≤4 per card, exactly 1 leader
  │
  ▼
[7–9] Candidate assembly + job record (same as Phase 1)
```

---

### Phase 3 Pipeline — Real-World Deck Photo

Substantially more complex. Requires robust preprocessing and an ensemble of signals:

```
UPLOAD (+ optional: multiple frames / short video clip)
  │
  ▼
[1] Multi-frame handling (if video)
    - Extract frames at 2 fps
    - Deduplicate near-identical frames (perceptual hash)
    - Select up to 10 distinct frames for processing
  │
  ▼
[2] Scene normalization
    - Detect dominant flat surface (table, mat)
    - Correct perspective of the full scene
    - Separate card regions from background
    - Flag unprocessable images early (too dark, too blurry, too small)
  │
  ▼
[3] Partial card detection
    - Cards may show only the top strip (name + set symbol visible)
    - Detect horizontal bands of repeated card structure
    - Extract top-strip crops (approx. top 15% of each card height)
  │
  ▼
[4] Top-strip OCR
    - Card name is printed at the top of all One Piece cards
    - Apply OCR to each top-strip crop
    - Fuzzy match → card name candidates
    - Confidence is inherently lower (partial content, more OCR noise)
  │
  ▼
[5] Visual ensemble
    - Attempt full visual matching if the card is >50% visible
    - Combine with top-strip OCR via weighted vote:
        confidence = 0.6 × visual_confidence + 0.4 × ocr_confidence
  │
  ▼
[6] Multi-frame aggregation (if video)
    - A card seen in multiple frames with same match → confidence boost
    - Contradictory matches across frames → both flagged for review
  │
  ▼
[7] High review burden handling
    - Phase 3 will typically produce many low-confidence items
    - Communicate this clearly before the user starts review:
      "Miru identified 23 of 51 cards with high confidence.
       28 cards need your input."
    - Never silently drop unresolved cards
  │
  ▼
[8] Job record written with phase='realworld'
```

---

### Match Method Taxonomy

| `match_method` value | Description | Typical confidence range |
|---|---|---|
| `code_regex` | Card code extracted by regex, confirmed in catalog | 0.90–0.99 |
| `name_fuzzy` | Card name extracted by OCR, matched by fuzzy string | 0.45–0.85 |
| `visual_phash` | Perceptual hash visual match to catalog image | 0.70–0.92 |
| `visual_embed` | Dense embedding nearest-neighbor match | 0.65–0.95 |
| `ensemble_code_visual` | Code regex + visual match agreement | 0.93–0.99 |
| `ensemble_ocr_visual` | OCR name + visual match agreement | 0.75–0.92 |
| `partial_ocr` | Top-strip OCR only (Phase 3) | 0.40–0.75 |

---

## 4. User Review / Verification Flow

The review flow is the core of the Verified Intelligence integration. It is not an error state — it is the designed outcome for any uncertain match.

### Confidence thresholds and routing

| Confidence | Label | Treatment |
|---|---|---|
| ≥ 0.90 | **High** | Tentatively accepted. Shown with ✓ and confidence badge. User can reject. |
| 0.65–0.89 | **Medium** | Flagged for review. Shown with card image + "Miru thinks this is X". User must accept or correct. |
| 0.40–0.64 | **Low** | Shown with top candidates listed. User must pick from candidates or search manually. |
| < 0.40 | **Unresolved** | Miru could not isolate a match. Shown as a grey slot: "Card not recognized". User must add manually. |

These thresholds are tunable in configuration. They are not hardcoded.

### Review UI layout (conceptual)

```
┌─────────────────────────────────────────────────────┐
│  Miru Recognition Review                            │
│  23 confirmed · 14 need review · 3 unresolved       │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │ [Card image]  OP01-001 Monkey D. Luffy  ✓   │   │  ← high confidence, auto-accepted
│  │              Confidence: 98%   code_regex   │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │ [Card image]  OP01-060 Nami                 │   │  ← medium confidence, needs review
│  │              Miru thinks this is Nami (76%) │   │
│  │              [Accept]  [Choose different]   │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │ [?]           Card not recognized            │   │  ← unresolved
│  │              [Search catalog]               │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  [Confirm Deck →]   (disabled until 0 unresolved)  │
└─────────────────────────────────────────────────────┘
```

### Review state machine per card

```
recognition_item.user_decision:

  pending
    │
    ├─ user clicks Accept  ──────────────────────→  accepted
    │
    ├─ user picks a different card  ─────────────→  corrected
    │    corrected_code = user's pick
    │
    ├─ user removes the card entirely  ──────────→  rejected
    │
    └─ (auto-accepted at load if confidence ≥ 0.90)  ──→  accepted
```

### "Confirm Deck" gate

The **Confirm Deck** button is disabled until `unresolved` count = 0. Every card must reach `accepted`, `corrected`, or `rejected` before the deck can be finalized. This prevents silent data loss.

When the user clicks Confirm:
1. All `accepted` cards (using `matched_code`) + all `corrected` cards (using `corrected_code`) are written to `user_deck_cards`.
2. `rejected` cards are excluded.
3. `user_decks` row is created with `source='recognition'` stored in `notes` or a dedicated `source_json` column.
4. `recognition_jobs.deck_uid` is set to the new `deck_uid`.
5. Version 1 snapshot is written to `user_deck_versions`.

---

## 5. Deck Builder Integration

### Recognition as a Deck Builder entry point

The Deck Builder will have multiple creation paths:

```
New Deck
  ├── Start blank               → empty deck, pick leader
  ├── Import from text          → paste card codes or names
  └── Recognize from image  ←  this feature
        └── upload image
              └── review flow
                    └── confirm → Deck Builder loaded with deck
```

After confirmation, the user lands in the Deck Builder viewing their recognized deck. They can immediately continue editing — adding cards, adjusting quantities — exactly as if they had built it manually.

### Recognition provenance in the Deck Builder

The first version of a recognized deck carries a visible badge: **"Imported via Miru Recognition"**. The `user_deck_versions` row for version 1 includes a `change_summary` value:

```
"Imported via Miru Recognition (job rj-a3f29c1e) — 38 high confidence, 11 reviewed, 2 corrected"
```

This gives the deck a permanent audit trail. If the user later edits the deck, version 2 onwards is normal user-edit history. The recognition origin is preserved in version 1 without contaminating future versions.

### Quantity validation before confirmation

Before writing to `user_deck_cards`, the confirmation step runs the same validation as manual deck entry:

- Exactly 1 leader card
- Non-leader cards: 1–4 copies each
- Main deck: ≤50 non-leader cards (standard format) — flag but allow override
- DON!! cards: excluded from the deck card list (handled separately if applicable)

Validation errors block confirmation and surface specific problems ("Monkey D. Luffy appears 2 times — only 1 leader allowed"). They do not silently discard cards.

---

## 6. Database / Storage Implications

### New tables in `miru_user_decks.db`

#### `recognition_jobs`

```sql
CREATE TABLE IF NOT EXISTS recognition_jobs (
    job_id              TEXT    PRIMARY KEY,
    -- Format: "rj-" + secrets.token_hex(4)
    -- Example: "rj-c8d14f22"

    phase               TEXT    NOT NULL DEFAULT 'screenshot',
    -- 'screenshot', 'spread', 'realworld'

    status              TEXT    NOT NULL DEFAULT 'processing',
    -- 'processing' → 'review' → 'confirmed' | 'abandoned'
    -- 'failed' if pipeline errored before producing any items

    uploaded_at         INTEGER NOT NULL,
    -- Unix timestamp of upload

    source_filename     TEXT    NOT NULL DEFAULT '',
    -- Original filename as uploaded by the user

    source_hash         TEXT    NOT NULL DEFAULT '',
    -- SHA-256 hex digest of the uploaded file content
    -- Used for deduplication: warn if the same image is re-submitted

    image_store_path    TEXT    NOT NULL DEFAULT '',
    -- Relative path to stored image file (if retained for audit)
    -- Empty if the image is not retained after processing

    card_count          INTEGER NOT NULL DEFAULT 0,
    -- Total number of cards Miru attempted to recognize

    high_conf_count     INTEGER NOT NULL DEFAULT 0,
    -- Count with confidence >= 0.90

    review_count        INTEGER NOT NULL DEFAULT 0,
    -- Count with 0.40 <= confidence < 0.90 (needs user review)

    unresolved_count    INTEGER NOT NULL DEFAULT 0,
    -- Count with confidence < 0.40 (no usable match)

    confirmed_at        INTEGER,
    -- Unix timestamp of user confirmation. NULL until confirmed.

    deck_uid            TEXT    NOT NULL DEFAULT '',
    -- Set to the user_decks.deck_uid created on confirmation.
    -- Empty until confirmed.

    notes               TEXT    NOT NULL DEFAULT ''
    -- Optional: pipeline notes, error messages, diagnostic info
);
```

#### `recognition_items`

```sql
CREATE TABLE IF NOT EXISTS recognition_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    job_id          TEXT    NOT NULL REFERENCES recognition_jobs(job_id) ON DELETE CASCADE,

    region_index    INTEGER NOT NULL,
    -- Zero-based order in which this region was detected in the image.
    -- Used for stable ordering in the review UI.

    matched_code    TEXT    NOT NULL DEFAULT '',
    -- The top candidate canonical_code. Empty if unresolved.
    -- Soft reference to cards.canonical_code — no SQL FK (cross-file).

    candidates_json TEXT    NOT NULL DEFAULT '[]',
    -- Top-N candidate matches with their scores. JSON array:
    -- [
    --   {"code": "OP01-001", "confidence": 0.98, "method": "code_regex"},
    --   {"code": "OP01-060", "confidence": 0.44, "method": "name_fuzzy"}
    -- ]
    -- Preserved for review UI: user can pick from candidates list.

    confidence      REAL    NOT NULL DEFAULT 0.0 CHECK (confidence BETWEEN 0.0 AND 1.0),
    -- Confidence of the top candidate (matched_code).

    match_method    TEXT    NOT NULL DEFAULT '',
    -- How this match was produced. See Match Method Taxonomy in §3.

    quantity        INTEGER NOT NULL DEFAULT 1,
    -- Detected quantity for this card.

    user_decision   TEXT    NOT NULL DEFAULT 'pending',
    -- 'pending', 'accepted', 'corrected', 'rejected'

    corrected_code  TEXT    NOT NULL DEFAULT '',
    -- Set when user_decision = 'corrected'. The card the user confirmed.

    UNIQUE(job_id, region_index)
);
```

#### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_recog_jobs_status
    ON recognition_jobs(status, uploaded_at DESC);

CREATE INDEX IF NOT EXISTS idx_recog_items_job
    ON recognition_items(job_id, region_index ASC);

CREATE INDEX IF NOT EXISTS idx_recog_items_decision
    ON recognition_items(job_id, user_decision);
```

### Migration file

These tables belong in a new migration file: `tools/migrations/m004_recognition.sql`.

Applied by: `python -m tools.miru_migrate_db --target user_decks` — but only after the migration manifest is updated to include m004. Alternatively, a dedicated `--target recognition` target can be added to the runner.

### Image storage

The uploaded image file itself is not stored in SQLite. Two strategies depending on infrastructure:

| Strategy | When to use | Notes |
|---|---|---|
| Discard after processing | Default (privacy-first) | `image_store_path = ''`. Nothing retained. |
| Retain in `data/recognition/<job_id>.<ext>` | When debugging or audit trail needed | `image_store_path` set. Retention policy: delete after 30 days. |

The `source_hash` (SHA-256) is always stored — it enables deduplication without storing the image content.

### Catalog image index for visual matching (Phase 2)

Phase 2 requires a precomputed image hash/embedding index. This is not stored in SQLite — it is a separate file:

```
data/
  miru_user_decks.db      ← recognition_jobs, recognition_items
  card_catalog.db         ← existing catalog
  recognition_phash.json  ← {canonical_code: pHash_hex} for all catalog images
                             Updated by: tools/miru_build_phash_index.py
```

The pHash index is generated once and refreshed when new card images are added to the catalog. It is a flat JSON file — small enough (~50 KB for a 1000-card catalog) to load into memory during processing.

---

## 7. Risks and Limitations

### Phase 1 risks

**OCR quality variance**
Digital screenshots are generally clean, but screenshot compression artifacts, dark backgrounds, non-standard fonts (fan sites, custom templates), or small text sizes can degrade OCR accuracy. Mitigation: preprocessing pipeline + fallback to name fuzzy-matching when code extraction fails.

**Regional card code differences**
Japanese cards use the same code format (`OP01-001`) but the printed text is Japanese. If a user submits a screenshot from the JP official site, card names will be in Japanese and fuzzy name matching against the EN catalog will fail. Mitigation: separate JP name index in the catalog; detect language early and route to the appropriate matching path.

**Alt-format code representations**
Some third-party sites display codes without the hyphen (`OP01001`), with spaces, or with full-width characters. The regex must be robust to these variants. `normalize_card_code()` in `miru_ai_onepiece.py` already handles many of these — the OCR extraction step should route through it.

**Unreleased or unknown cards**
Cards not yet in `card_catalog.db` will produce no match. The pipeline should report these as `unresolved` rather than silently dropping them. The user will need to add them manually once the catalog is updated.

---

### Phase 2 risks

**Sleeve design interference**
Opaque sleeves completely block card face recognition. This is unrecoverable for visual matching. Mitigation: detect sleeves early (solid-colour regions where card art is expected) and inform the user: "Cards appear to be sleeved. Visual matching will not work. Please photograph cards from sleeves or use a screenshot instead."

**Alt art and promo cards**
Alt-art versions have substantially different artwork but the same card code. A pHash index built on base art will fail to match alt-art crops confidently. Mitigation: index all `card_variants` images, not just base art. Mark matches against alt-art images with a slightly lower confidence cap (0.88 rather than 0.95) since the layout is more variable.

**Lighting and glare**
Foil cards, holographic cards, and shiny sleeves produce specular highlights that obliterate card art information locally. Mitigation: preprocessing to detect and mask glare regions; reduce confidence for affected crops.

**Card count expectations**
The segmentation step does not know in advance how many cards to expect. For One Piece TCG: a main deck is 50 cards (non-leader). If segmentation finds 38 regions in a spread photo, there is guaranteed truncation — the user should be warned immediately.

---

### Phase 3 risks

**Fundamentally limited information**
When only the top 1–2 cm of each card is visible (fanned deck), the recognizable content is: part of the card name, part of the cost number, and the set logo. This is a hard information ceiling regardless of model sophistication. Phase 3 will always produce a higher review burden than Phase 1 or 2. This must be communicated honestly, not obscured.

**Privacy exposure**
Real-world photos may incidentally capture personal items, faces, or identifiable backgrounds. The system should: (a) process images server-side, (b) not store original images in the default configuration (see image storage in §6), (c) present a clear disclosure to the user before upload.

**GPU infrastructure dependency**
Dense embedding matching (Phase 2b, Phase 3) requires GPU inference for acceptable latency. This has cost and infrastructure implications. The pHash approach (Phase 2a) works on CPU and is the correct starting point — GPU-based embedding is an upgrade path, not a Phase 2 requirement.

**Model maintenance**
Any ML model trained or fine-tuned for card recognition will drift as new card sets are released. The pHash index can be updated incrementally (just add new cards). A trained model may need periodic retraining. This is an ongoing operational cost that does not apply to Phase 1.

---

### Cross-phase risks

**Catalog coverage gaps**
Recognition is only as good as the catalog. Cards in `card_catalog.db` with no `image_path` or `image_url` cannot be visually matched (Phase 2+). The catalog coverage report in the dev tools should include a "recognition-ready" metric: what percentage of cards have usable images.

**Quantity under-counting**
If a card appears 4 times in a spread but the segmentation misses one instance (occlusion, lighting), the pipeline will detect quantity = 3. The user must catch this in review. The review UI should prominently display quantity alongside each card.

**User trust calibration**
If users encounter too many incorrect high-confidence matches early in the product's life, they will stop trusting Miru's confidence scores altogether. It is better to start with conservative thresholds (fewer auto-accepts, more review) and relax them as accuracy is measured. Do not optimize for "impressive demo" at the expense of real-world reliability.

---

## 8. Recommended First Implementation Step

### Build Phase 1 end-to-end, ship nothing else yet.

The exact sequence:

**Step 1 — Catalog pre-processing (no new UI)**

Write `tools/miru_build_recognition_index.py`. This tool queries `card_catalog.db` and produces `data/recognition_code_index.json`:

```json
{
  "OP01-001": {"card_name": "Monkey D. Luffy", "card_type": "Leader", "color": "Red"},
  "OP01-002": {"card_name": "Zoro", "card_type": "Character", "color": "Green"},
  ...
}
```

Also: a name-to-code reverse index (`data/recognition_name_index.json`) using all card names and their aliases, for fuzzy name matching. This is a flat file, loaded into memory per job — fast, no DB round-trips during pipeline.

**Step 2 — Pipeline module (server-side, no HTTP yet)**

Write `tools/miru_recognize_screenshot.py`. Entry point: `recognize_screenshot(image_bytes: bytes) -> RecognitionJob`. This module:
- Runs the Phase 1 pipeline (preprocessing → OCR → code extraction → name fuzzy match → quantity extraction → candidate assembly)
- Returns a structured result object (not yet written to DB)
- Has its own unit tests against known-good screenshots

Test it against at least 5 real screenshots before wiring to HTTP. Catch OCR edge cases early.

**Step 3 — Database migration**

Write `tools/migrations/m004_recognition.sql` with the `recognition_jobs` and `recognition_items` tables.
Run `python -m tools.miru_migrate_db --target recognition` (after adding the target to the runner manifest).

**Step 4 — AI server endpoint**

Add to `tools/miru_ai_server.py`:

```
POST /api/recognition/submit
    multipart/form-data: image file
    Response: {"job_id": "rj-c8d14f22", "status": "review", "item_count": 51, ...}

GET  /api/recognition/job/<job_id>
    Response: full job + items for review UI

POST /api/recognition/job/<job_id>/decide
    Body: [{"region_index": 0, "decision": "accepted"}, {"region_index": 5, "decision": "corrected", "corrected_code": "OP01-060"}, ...]

POST /api/recognition/job/<job_id>/confirm
    Validates all items resolved. Writes user_decks + user_deck_cards.
    Response: {"deck_uid": "ud-a3f29c1e"}
```

**Step 5 — Review UI (dashboard)**

A single new page at `/recognize` (or a modal on the Deck Builder) with:
- File upload area
- After submission: the review card list (high-confidence on left with checkmarks, review items on right with candidate selection)
- Confirm button (disabled until 0 unresolved)

Keep the UI simple for Phase 1. No animation, no streaming. Upload → process → show review page → confirm → redirect to `/deck/<deck_uid>`.

**Step 6 — Measure and calibrate**

After Phase 1 ships, instrument:
- What fraction of cards hit high/medium/low/unresolved on real user uploads
- How often users correct a high-confidence match (false positive rate)
- What card types / sets produce the most corrections

Use this data to tune the confidence thresholds and to identify OCR failure patterns before building Phase 2.

---

### What NOT to build yet

- Do not build a custom ML model for Phase 2 before Phase 1 is live and measured.
- Do not build the pHash index before Phase 1 is stable (it's the Phase 2 prerequisite).
- Do not build a streaming progress UI for Phase 1 (OCR is fast enough for synchronous response, typically < 3 seconds on a modern server).
- Do not build multi-image or video upload for Phase 1.
- Do not build Phase 3 until Phase 2 has real usage data and a calibrated visual matching baseline.

The order matters. Phase 1 teaches you where users actually struggle before you commit to the much larger Phase 2 and 3 investments.
