#!/usr/bin/env python3
"""
miru_fetch_onepiece_cardgame_dev.py

Fetch publicly hosted card JSON from onepiece-cardgame.dev and write Miru's snapshot format.

Primary data URL (static JSON, not HTML scraping):
  https://onepiece-cardgame.dev/cards.json

Ethics / safety:
  - No authentication
  - Respect robots.txt before fetching
  - >= 1.5s delay between HTTP requests
  - Identifying User-Agent for Project Miru (fan tool)
  - On 429/503: sleep 10s, retry once, then fail soft for that request
  - No bypassing rate limits; conservative single-file fetch for full import

Usage:
  python -m tools.miru_fetch_onepiece_cardgame_dev
  python -m tools.miru_fetch_onepiece_cardgame_dev --dry-run --limit 3
  python -m tools.miru_fetch_onepiece_cardgame_dev --set-code OP01
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "snapshots" / "onepiece_cardgame_dev.json"
SITE_ORIGIN = "https://onepiece-cardgame.dev"
ROBOTS_URL = f"{SITE_ORIGIN}/robots.txt"
CARDS_JSON_URL = f"{SITE_ORIGIN}/cards.json"

MIN_REQUEST_INTERVAL_SEC = 1.5
RETRY_BACKOFF_SEC = 10.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Site uses compact codes in cards.json (see raw fields col, t, r).
CARD_TYPE_MAP: dict[str, str] = {
    "1": "Leader",
    "2": "Character",
    "3": "Event",
    "4": "Stage",
    "5": "DON!!",
}

# Rarity codes — best-effort labels used on English product (site-specific ids).
RARITY_MAP: dict[str, str] = {
    "0": "Unknown",
    "1": "C",
    "2": "UC",
    "3": "R",
    "4": "SR",
    "5": "SEC",
    "6": "L",
    "7": "AA",
    "8": "Special",
    "9": "Special",
}

# Color ids are deck-color internal codes; map only when unambiguous from starter patterns.
COLOR_MAP: dict[str, str] = {
    "1": "Red",
    "4": "Purple",
    "5": "DON",
    "6": "Green",
    "7": "Blue",
    "8": "Black",
    "9": "Yellow",
    "10": "Red",
    "11": "Purple",
    "12": "Black",
    "13": "Green",
    "14": "Blue",
    "15": "Yellow",
    "16": "Yellow",
    "17": "Blue",
    "18": "Purple",
    "19": "Yellow",
    "20": "Green",
    "21": "Blue",
    "22": "Red",
    "23": "Purple",
    "24": "Yellow",
    "25": "Purple",
}

_log = logging.getLogger("miru_fetch_onepiece_cardgame_dev")
_last_request_mono: float | None = None


def _throttle() -> None:
    global _last_request_mono
    now = time.monotonic()
    if _last_request_mono is not None:
        elapsed = now - _last_request_mono
        if elapsed < MIN_REQUEST_INTERVAL_SEC:
            time.sleep(MIN_REQUEST_INTERVAL_SEC - elapsed)
    _last_request_mono = time.monotonic()


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"{SITE_ORIGIN}/",
    }


def check_robots_txt() -> tuple[bool, str]:
    """Fetch robots.txt; return (ok_to_proceed, message)."""
    _throttle()
    req = urllib.request.Request(ROBOTS_URL, headers=_request_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, f"robots.txt fetch failed (aborting): {exc}"

    # Fail closed only on a blanket Disallow: / for all agents (site currently allows; has Sitemap).
    disallow_root = False
    current_all = False
    for raw in body.splitlines():
        ln = raw.strip()
        if not ln or ln.startswith("#"):
            continue
        low = ln.lower()
        if low.startswith("user-agent:"):
            agent = ln.split(":", 1)[-1].strip()
            current_all = agent == "*"
            continue
        if current_all and low.startswith("disallow:"):
            path = ln.split(":", 1)[-1].strip()
            if path == "/":
                disallow_root = True
    if disallow_root:
        return False, "robots.txt disallows / for User-agent: * — aborting."
    return True, "robots.txt checked: no blanket Disallow: / for User-agent: *."


def fetch_http(url: str) -> tuple[int, bytes]:
    """GET url; on 429/503 wait RETRY_BACKOFF_SEC and retry once."""
    last_err: Exception | None = None
    for attempt in range(2):
        _throttle()
        req = urllib.request.Request(url, headers=_request_headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                code = int(resp.getcode() or 0)
                return code, resp.read()
        except urllib.error.HTTPError as e:
            code = int(e.code or 0)
            if code in (429, 503) and attempt == 0:
                _log.warning("HTTP %s for %s — backing off %ss and retrying once", code, url, RETRY_BACKOFF_SEC)
                time.sleep(RETRY_BACKOFF_SEC)
                continue
            raise
        except Exception as e:
            last_err = e
            break
    if last_err:
        raise last_err
    raise RuntimeError("fetch_http: unexpected path")


def normalize_set_code_arg(raw: str) -> str:
    s = raw.strip().upper()
    s = s.replace(" ", "")
    m = re.match(r"^([A-Z]+)[-]?(\d+)$", s)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    if re.match(r"^[A-Z]+\d+$", s):
        return s
    if s in ("P", "MISC", "DON"):
        return s
    return s


def set_prefix_for_filter(normalized: str) -> str:
    """Return cid prefix e.g. OP01 for --set-code OP01."""
    return normalized


def card_matches_set(cid: str, normalized_set: str) -> bool:
    c = (cid or "").strip().upper()
    if not c:
        return False
    pref = set_prefix_for_filter(normalized_set)
    if pref == "DON":
        return c.startswith("DON-")
    if pref in ("P", "MISC"):
        return c.startswith(f"{pref}-") or c.startswith(f"{pref}_")
    # OP01-001, ST12-003, EB01-001
    return c.startswith(pref + "-")


def raw_to_snapshot_card(row: dict[str, Any]) -> dict[str, str]:
    cid = str(row.get("cid") or "").strip()
    parts = cid.split("-", 1)
    set_code = parts[0] if parts else ""

    col = str(row.get("col") or "").strip()
    color = COLOR_MAP.get(col, col or "—")

    t = str(row.get("t") or "").strip()
    card_type = CARD_TYPE_MAP.get(t, t or "—")

    r = str(row.get("r") or "").strip()
    rarity = RARITY_MAP.get(r, r or "—")

    def _txt(x: Any) -> str:
        if x is None:
            return ""
        if isinstance(x, (int, float)):
            return str(x)
        return str(x).strip()

    effect = _txt(row.get("e")).replace("\r\n", "\n").strip()
    trigger = _txt(row.get("al")).replace("\r\n", "\n").strip()

    return {
        "card_code": cid,
        "card_name": _txt(row.get("n")),
        "set_code": set_code,
        "color": color,
        "card_type": card_type,
        "cost": _txt(row.get("cs")),
        "power": _txt(row.get("p")),
        "counter": _txt(row.get("cp")),
        "life": _txt(row.get("l")),
        "rarity": rarity,
        "traits": _txt(row.get("tr")),
        "effect_text": effect,
        "trigger_text": trigger,
    }


def load_cards_array(raw_bytes: bytes) -> list[dict[str, Any]]:
    data = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("cards.json must be a JSON array")
    return [x for x in data if isinstance(x, dict)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch onepiece-cardgame.dev cards.json into Miru snapshot format.")
    p.add_argument(
        "--input-json",
        type=Path,
        default=None,
        metavar="PATH",
        help="Offline: read a local copy of cards.json (same schema as the site) instead of network fetch.",
    )
    p.add_argument("--dry-run", action="store_true", help="Fetch and report but do not write the output file.")
    p.add_argument("--set-code", default="", metavar="CODE", help="Only include cards whose id starts with this set (e.g. OP01, ST01, P).")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="Max cards to include (0 = no cap).")
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument("--skip-robots", action="store_true", help="Skip robots.txt check (not recommended).")
    return p


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args()
    out_path: Path = args.output

    if args.input_json:
        in_path = Path(args.input_json)
        if not in_path.is_file():
            _log.error("Input file not found: %s", in_path)
            return 2
        body = in_path.read_bytes()
        _log.info("Using local input JSON (--input-json); skipping network and robots fetch.")
    else:
        if not args.skip_robots:
            ok, msg = check_robots_txt()
            _log.info("%s", msg)
            if not ok:
                _log.error("%s", msg)
                return 2

        try:
            code, body = fetch_http(CARDS_JSON_URL)
        except Exception as exc:
            _log.error("Failed to fetch %s: %s", CARDS_JSON_URL, exc)
            return 3

        if code != 200:
            _log.error("Unexpected HTTP %s for cards.json", code)
            return 3

    try:
        rows = load_cards_array(body)
    except Exception as exc:
        _log.error("Invalid JSON: %s", exc)
        return 4

    set_filter = ""
    if str(args.set_code or "").strip():
        set_filter = normalize_set_code_arg(str(args.set_code))

    miru_cards: list[dict[str, str]] = []
    errors: list[str] = []

    for row in rows:
        try:
            cid = str(row.get("cid") or "").strip()
            if not cid:
                continue
            if set_filter and not card_matches_set(cid, set_filter):
                continue
            miru_cards.append(raw_to_snapshot_card(row))
            if args.limit and args.limit > 0 and len(miru_cards) >= args.limit:
                break
        except Exception as exc:
            errors.append(f"{row.get('cid')!r}: {exc}")
            continue

    fetched_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source_id": "onepiece-cardgame-dev",
        "fetched_at": fetched_at,
        "cards": miru_cards,
    }
    if errors:
        payload["fetch_warnings"] = errors[:50]
        if len(errors) > 50:
            payload["fetch_warnings_truncated"] = len(errors) - 50

    sets_covered = sorted({c["set_code"] for c in miru_cards if c.get("set_code")})

    if args.dry_run:
        _log.info("DRY-RUN: not writing %s", out_path)
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("--- miru_fetch_onepiece_cardgame_dev ---")
    print(f"cards fetched: {len(miru_cards)}")
    print(f"sets covered ({len(sets_covered)}): {', '.join(sets_covered)}")
    print(f"output path: {out_path} ({'dry-run' if args.dry_run else 'written'})")
    if errors:
        print(f"row warnings: {len(errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
