# Worktree snapshot directory (approved sources)

This directory holds **worktree-local JSON snapshots** for approved external sources when using **snapshot-only** mode (no live HTTP).

## Convention

- **community-cardlist:** Place a compatible card-list JSON at:
  - `data/snapshots/community_cardlist.json`
- **official-cardlist:** Place a compatible card-list JSON at:
  - `data/snapshots/official_cardlist.json`
  - Same schema as community_cardlist; use for two-source agreement and stronger baseline. If you only have one snapshot, you can use it for both sources during testing (pass the path explicitly).

Both files must match the schema expected by `OfficialCardListSourceAdapter`: top-level `"source"` (optional) and `"cards"` array; each card has `card_code`, `card_name`, `set_code`, `set_name`, and other fields (see `tests/fixtures/miru_official_cardlist_sample.json` for reference).

## Usage

- Add the snapshot file(s) (obtained manually or via a one-off export); do not commit production data unless intended.
- Trigger learning with a source and path:
  - CLI: `python -m tools.run_worktree_review_cycle [CARD_CODE] [SOURCE_ID] [SNAPSHOT_PATH_OVERRIDE]`
  - Default SOURCE_ID: `community-cardlist`. Use `official-cardlist` for the second source.
  - Optional SNAPSHOT_PATH_OVERRIDE: use a specific file (e.g. same snapshot for both sources when testing two-source coverage).
  - Or: `python -m tools.miru_learning_engine --mode once --task verify_official_fields --card OP01-001 --source community-cardlist --snapshot-path data/snapshots/community_cardlist.json`

- **Bulk dossier growth (two-source baseline):** To process all cards in a snapshot through both approved sources and then rebuild insights, run:
  - `python -m tools.run_worktree_bulk_dossier_growth [--snapshot PATH] [--limit N]`
  - Uses worktree paths only; snapshot-only. See `tools/run_worktree_bulk_dossier_growth.py`.

No live URL is used until you configure `snapshot_url` and enable throttling.
