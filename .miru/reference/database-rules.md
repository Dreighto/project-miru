# Reference — Database Rules

```
Reference: database-rules
Architecture: MIRU-INSTRUCTIONS-v2
Fetch when: reading or proposing changes to card_catalog.db.
Last reviewed: 2026-05-08
```

## Database Rules

- card_catalog.db is the live database — never write to it directly from a worker session
- sqlite-ro-snapshot is the only approved DB access path for reads
- All schema changes must be proposed to Claude Chat first and approved by the operator before execution
- sqlite3 is available system-wide at C:\tools\sqlite3\sqlite3.exe
