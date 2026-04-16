#!/usr/bin/env python3
"""
miru_fetch_limitless.py

Fetch One Piece (OP) tournament snapshots from the Limitless public API
(base: https://play.limitlesstcg.com/api) and write data/snapshots/limitless.json.

Uses only unauthenticated JSON endpoints. The live API exposes:
  - GET /tournaments?game=OP
  - GET /tournaments/{id}/details  (single-tournament details; /tournaments/{id} returns 404)
  - GET /tournaments/{id}/standings  (placings + records; /tournaments/{id}/players returns 404)

Ethics: conservative pacing, identifiable User-Agent, 429 backoff, no HTML scraping.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "snapshots" / "limitless.json"
BASE_API = "https://play.limitlesstcg.com/api"
GAME_OP = "OP"

# Identifiable client string (public API; no API key).
USER_AGENT = (
    "ProjectMiru/1.0 (LimitlessTCG snapshot fetcher; worktree; respects /api rate limits)"
)

MIN_INTERVAL_SEC = 1.5
BACKOFF_429_SEC = 30.0
PAGE_SIZE = 50
MAX_PAGES = 200

_last_request_monotonic: float = 0.0

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
log = logging.getLogger("miru_fetch_limitless")


def _monotonic_now() -> float:
    return time.monotonic()


def _rate_limit_wait() -> None:
    """Enforce minimum interval between HTTP requests."""
    global _last_request_monotonic
    now = _monotonic_now()
    elapsed = now - _last_request_monotonic
    wait = MIN_INTERVAL_SEC - elapsed
    if wait > 0:
        time.sleep(wait)


def _note_request_done() -> None:
    global _last_request_monotonic
    _last_request_monotonic = _monotonic_now()


def _respect_rate_limit_headers(headers: dict[str, str]) -> None:
    """If API reports no remaining quota, wait until reset (capped) when parseable."""
    rem = headers.get("x-ratelimit-remaining")
    if rem is None:
        return
    try:
        if int(str(rem).strip()) > 0:
            return
    except ValueError:
        return
    reset = headers.get("x-ratelimit-reset") or headers.get("ratelimit-reset")
    if not reset:
        return
    try:
        ts = float(str(reset).strip())
    except ValueError:
        return
    now = time.time()
    if ts > now:
        wait = min(ts - now, 120.0)
        if wait > 0.1:
            log.info("Rate limit: remaining=0; waiting %.1fs until reset.", wait)
            time.sleep(wait)


def _parse_http_date_retry_after(value: str | None) -> float | None:
    if not value or not str(value).strip():
        return None
    try:
        # Retry-After can be seconds as integer
        return float(str(value).strip())
    except ValueError:
        return None


def fetch_json(
    path: str,
    *,
    query: dict[str, Any] | None = None,
    retry_429: bool = True,
) -> Any:
    """
    GET JSON from BASE_API + path. Respects MIN_INTERVAL_SEC between calls.
    On 429: wait Retry-else BACKOFF_429_SEC, retry once.
    """
    url = BASE_API + path
    if query:
        url += "?" + urlencode(query, doseq=True)

    def _once() -> tuple[Any, dict[str, str]]:
        _rate_limit_wait()
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        with urlopen(req, timeout=120) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read()
        _note_request_done()
        rem = headers.get("x-ratelimit-remaining")
        reset = headers.get("x-ratelimit-reset") or headers.get("ratelimit-reset")
        if rem is not None or reset is not None:
            log.debug("Rate-limit headers: remaining=%s reset=%s", rem, reset)
        _respect_rate_limit_headers(headers)
        return json.loads(body.decode("utf-8")), headers

    try:
        data, headers = _once()
        return data
    except HTTPError as e:
        if e.code == 429 and retry_429:
            ra = None
            if e.headers:
                ra = e.headers.get("Retry-After") or e.headers.get("retry-after")
            sleep_s = _parse_http_date_retry_after(ra)
            if sleep_s is None or sleep_s < 1:
                sleep_s = BACKOFF_429_SEC
            log.warning("429 Too Many Requests — sleeping %.1fs then retrying once.", sleep_s)
            time.sleep(sleep_s)
            try:
                data, _ = _once()
                return data
            except HTTPError as e2:
                if e2.code == 429:
                    log.error("429 persisted after backoff; giving up on %s", url)
                raise
        raise


def _parse_tournament_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _leader_code_from_standing(row: dict[str, Any]) -> str:
    """Best-effort OP leader card code from standings row (deck.id when present)."""
    deck = row.get("deck")
    if isinstance(deck, dict):
        lid = str(deck.get("id") or "").strip().upper()
        if lid and lid.startswith("OP") and "-" in lid:
            return lid
    decklist = row.get("decklist")
    if isinstance(decklist, dict):
        # Some games nest leader; OP may expose leader id elsewhere in future
        for key in ("leader", "leaderCard", "leader_code"):
            v = decklist.get(key)
            if isinstance(v, str) and v.strip().upper().startswith("OP"):
                return v.strip().upper()
            if isinstance(v, dict):
                cid = str(v.get("id") or v.get("code") or "").strip().upper()
                if cid.startswith("OP") and "-" in cid:
                    return cid
    return ""


def collect_tournament_list(
    *,
    days: int,
    hard_limit: int | None,
) -> list[dict[str, Any]]:
    """Paginate /tournaments until past the date window or limits hit."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(days, 0))
    out: list[dict[str, Any]] = []
    page = 1
    pages = 0
    while pages < MAX_PAGES:
        try:
            batch = fetch_json(
                "/tournaments",
                query={"game": GAME_OP, "limit": PAGE_SIZE, "page": page},
            )
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            log.error("Failed to fetch tournament list page %s: %s", page, e)
            break
        if not isinstance(batch, list) or not batch:
            break
        pages += 1
        stop_paging = False
        for t in batch:
            if not isinstance(t, dict):
                continue
            dt = _parse_tournament_date(str(t.get("date") or ""))
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt is not None and dt < cutoff:
                stop_paging = True
                continue
            out.append(t)
            if hard_limit is not None and len(out) >= hard_limit:
                return out
        if stop_paging or len(batch) < PAGE_SIZE:
            break
        page += 1
    return out


def build_snapshot(
    tournament_metas: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, int]]:
    """
    Fetch details + standings per tournament. Returns (snapshot, stats).
    stats keys: tournaments_ok, tournaments_failed, total_players_sum
    """
    tournaments_out: list[dict[str, Any]] = []
    leader_usage: dict[str, dict[str, int]] = {}
    total_players_sum = 0
    ok = 0
    failed = 0

    for meta in tournament_metas:
        tid = str(meta.get("id") or "").strip()
        if not tid:
            failed += 1
            continue
        try:
            details = fetch_json(f"/tournaments/{tid}/details")
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            log.warning("Skipping tournament %s (details): %s", tid, e)
            failed += 1
            continue
        if not isinstance(details, dict):
            log.warning("Skipping tournament %s: details not an object", tid)
            failed += 1
            continue

        try:
            standings = fetch_json(f"/tournaments/{tid}/standings")
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
            log.warning("Skipping tournament %s (standings): %s", tid, e)
            failed += 1
            continue
        if not isinstance(standings, list):
            log.warning("Skipping tournament %s: standings not a list", tid)
            failed += 1
            continue

        players_count = int(details.get("players") or 0)
        total_players_sum += players_count

        results: list[dict[str, Any]] = []
        for row in standings:
            if not isinstance(row, dict):
                continue
            placing = int(row.get("placing") or 0)
            name = str(row.get("name") or row.get("player") or "").strip()
            record = row.get("record") if isinstance(row.get("record"), dict) else {}
            wins = int(record.get("wins") or 0)
            losses = int(record.get("losses") or 0)
            leader_code = _leader_code_from_standing(row)
            results.append(
                {
                    "placing": placing,
                    "player": name or str(row.get("player") or ""),
                    "leader_code": leader_code,
                    "record": {"wins": wins, "losses": losses},
                }
            )
            if leader_code:
                bucket = leader_usage.setdefault(
                    leader_code,
                    {"appearances": 0, "top8": 0, "wins": 0},
                )
                bucket["appearances"] += 1
                if placing <= 8:
                    bucket["top8"] += 1
                if placing == 1:
                    bucket["wins"] += 1

        fmt = details.get("format")
        tournaments_out.append(
            {
                "tournament_id": tid,
                "name": str(details.get("name") or meta.get("name") or ""),
                "date": str(details.get("date") or meta.get("date") or ""),
                "players": players_count,
                "format": "" if fmt is None else str(fmt),
                "results": results,
            }
        )
        ok += 1

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "schema_version": 1,
        "source_id": "limitless",
        "fetched_at": fetched_at,
        "api_note": (
            "Tournament details use GET /tournaments/{id}/details; "
            "placings use GET /tournaments/{id}/standings (public API)."
        ),
        "tournaments": tournaments_out,
        "meta_summary": {
            "leader_usage": leader_usage,
        },
    }
    stats = {
        "tournaments_ok": ok,
        "tournaments_failed": failed,
        "total_players_sum": total_players_sum,
    }
    return snapshot, stats


def top_leader_lines(leader_usage: dict[str, dict[str, int]], n: int = 5) -> list[str]:
    ranked = sorted(
        leader_usage.items(),
        key=lambda kv: (-(kv[1].get("appearances") or 0), kv[0]),
    )[:n]
    lines = []
    for code, m in ranked:
        a = int(m.get("appearances") or 0)
        lines.append(f"  {code}: {a} appearances")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Limitless OP tournament snapshot (public API, no API key)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print summary but do not write limitless.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of tournaments to include after date filter (testing).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        metavar="N",
        help="Only include tournaments scheduled on or after (now - N days). Default: 90.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT}).",
    )
    args = parser.parse_args(argv)

    log.info("User-Agent: %s", USER_AGENT)
    log.info("Minimum %.1fs between requests; 429 → retry once after backoff.", MIN_INTERVAL_SEC)

    metas = collect_tournament_list(days=args.days, hard_limit=args.limit)
    log.info("Tournament list candidates after date filter: %s", len(metas))

    snapshot, stats = build_snapshot(metas)
    leader_usage = snapshot["meta_summary"]["leader_usage"]

    print("")
    print("--- Limitless OP snapshot ---")
    print(f"Tournaments in snapshot: {stats['tournaments_ok']}")
    print(f"Tournaments skipped (errors): {stats['tournaments_failed']}")
    print(f"Total reported players (sum of tournament player counts): {stats['total_players_sum']}")
    print("Top leaders by appearances:")
    lines = top_leader_lines(leader_usage, 5)
    if not lines:
        print("  (no leader codes extracted — deck metadata empty for this sample)")
    for line in lines:
        print(line)
    print(f"Output path: {args.output}")
    if args.dry_run:
        print("(dry-run: file not written)")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
