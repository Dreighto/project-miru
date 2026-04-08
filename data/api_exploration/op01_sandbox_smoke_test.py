#!/usr/bin/env python3
"""
OP01 Dual-API Sandbox Smoke Test -- Step 1
Fetches raw JSON for 5 cards from JustTCG (authed) and OPTCG API (public).
Saves raw responses and a request budget log.

BOUNDARIES: No DB, no PM runtime, no ingestion, no secret leakage.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time

# Force UTF-8 stdout on Windows to avoid cp1252 encoding errors
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]  # repo root
OUT_DIR = ROOT / "data" / "api_exploration"
ENV_PATH = ROOT / ".env"

# ── constants ──────────────────────────────────────────────────────────────
CARD_CODES = ["OP01-001", "OP01-016", "OP01-025", "OP01-033", "OP01-004"]

OPTCG_API_BASE = "https://optcgapi.com/api"
JUSTTCG_API_BASE = "https://api.justtcg.com/v1"
JUSTTCG_GAME_SLUG = "one-piece-card-game"  # will verify via /games

USER_AGENT = "ProjectMiru/1.0 (OP01-sandbox-smoketest; conservative pacing)"
HTTP_TIMEOUT = 30
PACING_SEC = 1.2  # polite delay between requests


# ── helpers ────────────────────────────────────────────────────────────────
def load_env_key(env_path: Path, key: str) -> str | None:
    """Read a single key from a .env file without importing dotenv."""
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip()
    return None


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, str]:
    """Return (status_code, body_text). On HTTP errors, still returns code + body."""
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = Request(url, headers=hdrs)
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8")
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        return e.code, body
    except (URLError, TimeoutError, OSError) as e:
        return 0, str(e)


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── budget tracker ─────────────────────────────────────────────────────────
class BudgetTracker:
    def __init__(self):
        self.entries: list[dict] = []
        self.daily_total = 0
        self.monthly_total = 0

    def log(self, source: str, endpoint: str, card_code: str) -> None:
        self.daily_total += 1
        self.monthly_total += 1
        self.entries.append({
            "source": source,
            "endpoint": endpoint,
            "request_count": 1,
            "card_code": card_code,
            "timestamp": ts(),
            "running_daily_total": self.daily_total,
            "running_monthly_total": self.monthly_total,
        })

    def save(self, path: Path) -> None:
        payload = {
            "budget_log_version": 1,
            "generated_at": ts(),
            "daily_total": self.daily_total,
            "monthly_total": self.monthly_total,
            "entries": self.entries,
        }
        save_json(path, payload)


# ── main ───────────────────────────────────────────────────────────────────
def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    budget = BudgetTracker()
    results: dict[str, dict] = {
        "justtcg": {"key_found": False, "auth_ok": False, "cards": {}},
        "optcg": {"cards": {}},
    }

    # ── 1. Load JustTCG API key ────────────────────────────────────────────
    api_key = load_env_key(ENV_PATH, "JUSTTCG_API_KEY")
    if api_key:
        results["justtcg"]["key_found"] = True
        print("[OK] JUSTTCG_API_KEY found in .env")
    else:
        print("[FAIL] JUSTTCG_API_KEY not found in .env")
        # Continue — we still run OPTCG tests

    justtcg_headers = {"x-api-key": api_key} if api_key else {}

    # ── 2. Discover JustTCG game slug ──────────────────────────────────────
    justtcg_game_confirmed = False
    if api_key:
        print("\n── JustTCG: verifying game slug via /v1/games ──")
        time.sleep(PACING_SEC)
        status, body = http_get(f"{JUSTTCG_API_BASE}/games", justtcg_headers)
        budget.log("justtcg", "/v1/games", "discovery")
        print(f"  /v1/games → HTTP {status}")
        if status == 200:
            results["justtcg"]["auth_ok"] = True
            try:
                games_data = json.loads(body)
                # save discovery response too
                save_json(OUT_DIR / "justtcg_games_discovery.json", games_data)
                # look for One Piece
                game_list = games_data if isinstance(games_data, list) else games_data.get("data", [])
                for g in (game_list if isinstance(game_list, list) else []):
                    gname = ""
                    gid = ""
                    if isinstance(g, dict):
                        gname = str(g.get("name", "") or g.get("game", "")).lower()
                        gid = str(g.get("id", "") or g.get("slug", "") or g.get("game", ""))
                    elif isinstance(g, str):
                        gname = g.lower()
                        gid = g
                    if "one piece" in gname or "one-piece" in gid.lower():
                        JUSTTCG_GAME_SLUG_ACTUAL = gid
                        justtcg_game_confirmed = True
                        print(f"  [OK] Found One Piece game: slug='{gid}', name='{gname}'")
                        break
                if not justtcg_game_confirmed:
                    print(f"  [WARN] One Piece not found in games list; will try default slug '{JUSTTCG_GAME_SLUG}'")
            except json.JSONDecodeError:
                print("  [WARN] Could not parse /v1/games response")
        else:
            print(f"  [WARN] /v1/games returned HTTP {status}")

    # Use confirmed slug or fallback
    game_slug = JUSTTCG_GAME_SLUG_ACTUAL if justtcg_game_confirmed else JUSTTCG_GAME_SLUG

    # ── 3. Fetch JustTCG cards ─────────────────────────────────────────────
    if api_key:
        print(f"\n── JustTCG: fetching {len(CARD_CODES)} cards (game={game_slug}) ──")
        for code in CARD_CODES:
            time.sleep(PACING_SEC)
            # Primary strategy: search by q with game filter
            url = f"{JUSTTCG_API_BASE}/cards?q={code}&game={game_slug}"
            status, body = http_get(url, justtcg_headers)
            budget.log("justtcg", f"/v1/cards?q={code}&game={game_slug}", code)

            parsed = None
            if status == 200:
                try:
                    parsed = json.loads(body)
                except json.JSONDecodeError:
                    pass

            # Check if we got results
            has_data = False
            if parsed:
                data_arr = parsed.get("data", []) if isinstance(parsed, dict) else parsed
                has_data = isinstance(data_arr, list) and len(data_arr) > 0

            # Fallback: try without game filter if empty
            if not has_data and status == 200:
                time.sleep(PACING_SEC)
                url_fallback = f"{JUSTTCG_API_BASE}/cards?q={code}"
                status2, body2 = http_get(url_fallback, justtcg_headers)
                budget.log("justtcg", f"/v1/cards?q={code} (fallback)", code)
                if status2 == 200:
                    try:
                        parsed2 = json.loads(body2)
                        data_arr2 = parsed2.get("data", []) if isinstance(parsed2, dict) else parsed2
                        if isinstance(data_arr2, list) and len(data_arr2) > 0:
                            parsed = parsed2
                            status = status2
                            has_data = True
                            print(f"  {code} → HTTP {status} (fallback, no game filter) ✓ {len(data_arr2)} result(s)")
                    except json.JSONDecodeError:
                        pass

            if has_data:
                results["justtcg"]["auth_ok"] = True
                if not has_data:
                    pass  # already printed
                else:
                    data_count = len(parsed.get("data", [])) if isinstance(parsed, dict) else "?"
                    print(f"  {code} → HTTP {status} ✓ {data_count} result(s)")
            else:
                print(f"  {code} → HTTP {status} {'(empty)' if status == 200 else '✗'}")

            # Save raw response regardless
            filename = f"justtcg_{code.replace('-', '_')}.json"
            if parsed is not None:
                save_json(OUT_DIR / filename, parsed)
                results["justtcg"]["cards"][code] = {"status": status, "file": filename, "has_data": has_data}
            else:
                # save raw text for debugging
                save_json(OUT_DIR / filename, {"_raw_status": status, "_raw_body": body[:2000]})
                results["justtcg"]["cards"][code] = {"status": status, "file": filename, "has_data": False}

    # ── 4. Fetch OPTCG API cards ───────────────────────────────────────────
    print(f"\n── OPTCG API: fetching {len(CARD_CODES)} cards ──")
    for code in CARD_CODES:
        time.sleep(PACING_SEC)
        url = f"{OPTCG_API_BASE}/sets/card/{code}/"
        status, body = http_get(url)
        budget.log("optcg", f"/api/sets/card/{code}/", code)

        parsed = None
        if status == 200:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                pass

        has_data = parsed is not None and (
            (isinstance(parsed, list) and len(parsed) > 0) or
            (isinstance(parsed, dict) and len(parsed) > 0)
        )

        if has_data:
            count = len(parsed) if isinstance(parsed, list) else 1
            print(f"  {code} → HTTP {status} ✓ {count} variant(s)")
        else:
            print(f"  {code} → HTTP {status} {'(empty)' if status == 200 else '✗'}")

        filename = f"optcg_{code.replace('-', '_')}.json"
        if parsed is not None:
            save_json(OUT_DIR / filename, parsed)
            results["optcg"]["cards"][code] = {"status": status, "file": filename, "has_data": has_data}
        else:
            save_json(OUT_DIR / filename, {"_raw_status": status, "_raw_body": body[:2000]})
            results["optcg"]["cards"][code] = {"status": status, "file": filename, "has_data": False}

    # ── 5. Save budget log ─────────────────────────────────────────────────
    budget.save(OUT_DIR / "request_budget_log.json")

    # ── 6. Summary ─────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("SMOKE TEST SUMMARY")
    print("=" * 60)

    jtcg = results["justtcg"]
    print(f"\nJustTCG:")
    print(f"  API key found:  {jtcg['key_found']}")
    print(f"  Auth success:   {jtcg['auth_ok']}")
    jtcg_ok = sum(1 for c in jtcg["cards"].values() if c.get("has_data"))
    print(f"  Cards with data: {jtcg_ok}/5")

    optcg = results["optcg"]
    optcg_ok = sum(1 for c in optcg["cards"].values() if c.get("has_data"))
    print(f"\nOPTCG API:")
    print(f"  Cards with data: {optcg_ok}/5")

    print(f"\nBudget:")
    print(f"  Daily total:   {budget.daily_total}")
    print(f"  Monthly total: {budget.monthly_total}")

    # ── 7. Files created ───────────────────────────────────────────────────
    created = sorted(OUT_DIR.glob("*.json"))
    print(f"\nFiles created ({len(created)}):")
    for f in created:
        print(f"  {f.relative_to(ROOT)}")

    # ── 8. Top-level fields ────────────────────────────────────────────────
    print("\n── Top-level field analysis ──")

    # JustTCG fields
    for code in CARD_CODES:
        fname = f"justtcg_{code.replace('-', '_')}.json"
        fpath = OUT_DIR / fname
        if fpath.is_file():
            d = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "data" in d:
                print(f"\n  JustTCG response top-level keys: {sorted(d.keys())}")
                data_arr = d.get("data", [])
                if isinstance(data_arr, list) and len(data_arr) > 0:
                    first = data_arr[0]
                    if isinstance(first, dict):
                        print(f"  JustTCG card object keys: {sorted(first.keys())}")
                        # Check for variants
                        if "variants" in first and isinstance(first["variants"], list) and len(first["variants"]) > 0:
                            print(f"  JustTCG variant keys: {sorted(first['variants'][0].keys())}")
                break  # only need one sample

    # OPTCG fields
    for code in CARD_CODES:
        fname = f"optcg_{code.replace('-', '_')}.json"
        fpath = OUT_DIR / fname
        if fpath.is_file():
            d = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(d, list) and len(d) > 0 and isinstance(d[0], dict):
                print(f"\n  OPTCG API card object keys: {sorted(d[0].keys())}")
            elif isinstance(d, dict) and not d.get("_raw_status"):
                print(f"\n  OPTCG API response keys: {sorted(d.keys())}")
            break

    # ── 9. Verdict ─────────────────────────────────────────────────────────
    if jtcg["auth_ok"] and jtcg_ok >= 3 and optcg_ok == 5:
        verdict = "CONFIRMED WORKING"
    elif jtcg["auth_ok"] or optcg_ok >= 3:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAILED"

    print(f"\n{'=' * 60}")
    print(f"VERDICT: {verdict}")
    print(f"{'=' * 60}")

    return 0 if verdict == "CONFIRMED WORKING" else 1


if __name__ == "__main__":
    raise SystemExit(main())
