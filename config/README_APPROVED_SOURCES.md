# Approved sources config (worktree-only)

`miru_approved_sources.json` is the worktree-local allowlist for approved external sources.  
Discovery remains manual-review only; adding a source here does not auto-approve discovery candidates.

## Format

```json
{
  "approved_sources": [
    {
      "source_id": "my-community-db",
      "source_name": "My Community Card DB",
      "source_type": "community-card-database",
      "trust_tier": 4,
      "enabled": true,
      "allowed_access": "public_page",
      "request_spacing_seconds": 2.0,
      "requires_api": false,
      "snapshot_url": "",
      "base_url": "",
      "domain": "",
      "notes": "Added after review; public page only."
    }
  ]
}
```

- **source_id** (required): Unique id; use lowercase, hyphens allowed.
- **source_name**, **source_type**, **notes**: Optional; human-readable.
- **trust_tier**: 1–4 (4 = experimental/manual review). Default 4.
- **enabled**: Default true.
- **allowed_access**: `public_page` | `permitted_api` | `manual_only`. Default `public_page`.
- **request_spacing_seconds**: Min seconds between requests. Default 2.0.
- **requires_api**: If true, Miru will not use this source automatically. Default false.
- **snapshot_url**, **base_url**, **domain**: Optional hints for adapters.

Invalid entries are skipped and reported in Dev status (`approved_sources_config_errors`).
