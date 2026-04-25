# Project Miru — Remote MCP Gateway Smoke Test

This is the handoff prompt the operator pastes into a fresh Claude Chat thread
once the Stage 1 MCP Gateway is live and the local verification gates in
`docs/mcp_gateway/` plan §8 (tests 1–8) have all passed.

Do NOT run this until:
- The gateway is healthy locally (`Invoke-WebRequest http://127.0.0.1:18766/mcp/<SECRET>/health` returns `{"ok":true,...}`).
- All BLOCKER deny-list tests (plan §8 tests 5–8) have passed locally.
- The Tailscale Funnel path mount is live and `https://room.taila28611.ts.net/mcp/<SECRET>/health` returns 200.
- The Miru custom connector is added in Claude.ai → Settings → Connectors.

---

## Paste everything below into a fresh Claude Chat thread

```
# Project Miru — Remote MCP Gateway Smoke Test

You have a new custom connector called "Miru" that reads files from the Project Miru
repo at D:\dev\miru over HTTPS. This is a sanity check — do not summarize secrets,
hidden files, or log contents. If any forbidden path returns data, stop and tell me.

## What should appear
- The "Miru" connector should expose exactly 9 tools, all prefixed fs_*:
  fs_read_text_file, fs_read_media_file, fs_read_multiple_files,
  fs_list_directory, fs_list_directory_with_sizes, fs_directory_tree,
  fs_search_files, fs_get_file_info, fs_list_allowed_directories.

## Allowed tests (each should succeed and return real content)
1. Call fs_list_allowed_directories() — must return exactly "Allowed directories: D:\dev\miru".
2. Read docs/pm/00_PRINCIPLES.md — first 200 words back to me.
3. Directory tree of docs/pm — confirm no .git / logs / .env entries.
4. Search the repo for "MIRU_FS_ALLOW_ROOT" — report paths found.

## Forbidden tests (each MUST fail with a deny or out-of-root error)
5. Read data/card_catalog.db — expect "denied by Project Miru MCP policy".
6. Read .env — expect deny.
7. Read .git/config — expect deny.
8. Read logs/mcp_gateway_18766_stdout.log — expect deny.
9. Read C:\Users\Dreighto\.env — expect "outside allowed root".
10. Try to write a file — expect "no write tool available".

## Reporting rules
- Do NOT paste contents of any file that matches *.env, *.key, *.pem, or anything
  under .git / logs. If a read unexpectedly succeeds for those, tell me the tool
  returned data and STOP — do not show the data.
- For allowed reads, a one-line summary + the first N lines is fine.
- End with a single line: "SMOKE TEST: PASS" or "SMOKE TEST: FAIL — <reason>".
```

---

## If SMOKE TEST: FAIL

1. Execute fast rollback (see `tools/miru_mcp_gateway/README.md` → Rollback).
2. Capture the failing tool name and the response Claude returned.
3. Do NOT re-mount on Funnel until the deny list / mount path is fixed and
   plan §8 tests 1–8 pass locally again.
