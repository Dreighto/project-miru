"""OP01 printing-completeness crawl. PRO-904.

Read+crawl only. Fetches Bandai cardlist freewords search for OP01-001..121,
parses printing rows out of returned markdown, writes structured JSON to
data/bandai_op01_crawl.json. Diff is a separate script (op01_bandai_diff.py).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "bandai_op01_crawl.json"
LOG_PATH = ROOT / "data" / "bandai_op01_crawl.log"

FIRECRAWL_URL = "https://api.firecrawl.dev/v1/scrape"
SEARCH_URL = "https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-{nnn}"

CARD_NUMBERS = [f"{i:03d}" for i in range(1, 122)]  # 001..121

# Throttle: handoff says 502 req/min cap was hit. Stay well under.
# Each request also has a 7s waitFor inside FireCrawl, so we're naturally slow.
INTER_REQUEST_SLEEP = 1.5  # seconds between requests
MAX_RETRIES = 3
RETRY_BACKOFF = 5.0  # base seconds, doubled per retry

# Regexes built once
IMG_URL_RE = re.compile(
    r"https://en\.onepiece-cardgame\.com/images/cardlist/card/(OP01-\d{3}(?:_[a-z]\d+)?)\.png"
)
# Header looks like:  OP01-016 \| R \| CHARACTER
HEADER_RE = re.compile(
    r"(OP01-\d{3})\s*\\\|\s*([A-Z][A-Z0-9 ]*?)\s*\\\|\s*(CHARACTER|LEADER|EVENT|STAGE)"
)
# Card name appears on its own line after the header
CARD_SET_RE = re.compile(
    r"###\s*Card Set\(s\)\s*\n+([^\n].*?)(?=\n###|\nCARD VIEW|\Z)",
    re.DOTALL,
)


def load_env_key() -> str:
    key = os.environ.get("FIRECRAWL_API_KEY")
    if key:
        return key.strip()

    env_path_str = os.environ.get(
        "LOGUEOS_ENV_PATH",
        r"D:\dev\LogueOS-Orchestrator\.env",
    )
    env_path = Path(env_path_str)
    if not env_path.exists():
        raise RuntimeError(f"FIRECRAWL_API_KEY env var not set and {env_path} does not exist")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("FIRECRAWL_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"FIRECRAWL_API_KEY not found in {env_path}")


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def scrape_one(api_key: str, nnn: str) -> dict[str, Any]:
    url = SEARCH_URL.format(nnn=nnn)
    payload = {
        "url": url,
        "formats": ["markdown"],
        "waitFor": 7000,
        "onlyMainContent": True,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err: str | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(FIRECRAWL_URL, headers=headers, json=payload, timeout=90)
        except requests.RequestException as e:
            last_err = f"request exception: {e}"
            log(f"OP01-{nnn} attempt {attempt} {last_err}")
        else:
            if r.status_code == 200:
                body = r.json()
                if body.get("success"):
                    return body.get("data", {})
                last_err = f"success=false body={body!r}"
            elif r.status_code in (429, 502, 503, 504):
                last_err = f"http {r.status_code}"
            else:
                last_err = f"http {r.status_code} body={r.text[:200]}"
            log(f"OP01-{nnn} attempt {attempt} {last_err}")
        if attempt < MAX_RETRIES:
            backoff = RETRY_BACKOFF * (2 ** (attempt - 1))
            log(f"OP01-{nnn} backing off {backoff:.1f}s")
            time.sleep(backoff)
    raise RuntimeError(f"scrape failed for OP01-{nnn}: {last_err}")


def parse_printings(markdown: str, queried_card: str) -> list[dict[str, Any]]:
    """Extract one record per printing block."""
    # Split on the "CARD VIEW" sentinel that closes each block.
    # The header chrome (filters, menus) is before the first card image, so we
    # filter chunks by presence of an OP01 image URL.
    blocks = markdown.split("\nCARD VIEW\n")
    printings: list[dict[str, Any]] = []
    seen_print_ids: set[str] = set()

    for block in blocks:
        img_match = IMG_URL_RE.search(block)
        if not img_match:
            continue
        full_id = img_match.group(1)  # e.g. OP01-016 or OP01-016_p4
        if "_p" in full_id:
            base_code, print_suffix = full_id.split("_", 1)
            print_id = "_" + print_suffix  # e.g. _p4
        else:
            base_code = full_id
            print_id = "base"

        # Filter: drop crawl rows whose number != queried number (spec section 4).
        if base_code != queried_card:
            continue

        # Dedupe (same printing appears twice — once in image, once in TEXT VIEW header).
        if full_id in seen_print_ids:
            continue
        seen_print_ids.add(full_id)

        # Rarity from header line
        header = HEADER_RE.search(block)
        rarity = header.group(2).strip() if header else None

        # Card name: line right after the header
        name = None
        if header:
            tail = block[header.end() :]
            lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
            if lines:
                name = lines[0]

        # Card Set(s)
        set_match = CARD_SET_RE.search(block)
        card_set = set_match.group(1).strip() if set_match else None
        if card_set:
            # Markdown escapes brackets as \[ \]
            card_set = card_set.replace("\\[", "[").replace("\\]", "]")

        # Build image URL (preserve query string for cache busting)
        img_full_url_match = re.search(
            r"https://en\.onepiece-cardgame\.com/images/cardlist/card/"
            + re.escape(full_id)
            + r"\.png(?:\?[^\s)]*)?",
            block,
        )
        image_url = img_full_url_match.group(0) if img_full_url_match else None

        printings.append(
            {
                "card_number": base_code,
                "print_id": print_id,
                "full_id": full_id,
                "name": name,
                "rarity": rarity,
                "card_set": card_set,
                "image_url": image_url,
            }
        )

    return printings


def main() -> int:
    global OUT_PATH, LOG_PATH
    api_key = load_env_key()

    # Optional smoke-test: BANDAI_CRAWL_ONLY="016,017" or "016".
    only = os.environ.get("BANDAI_CRAWL_ONLY", "").strip()
    if only:
        cards = [c.zfill(3) for c in only.split(",") if c.strip()]
        OUT_PATH = ROOT / "data" / "bandai_op01_crawl.smoke.json"
        LOG_PATH = ROOT / "data" / "bandai_op01_crawl.smoke.log"
    else:
        cards = CARD_NUMBERS
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.unlink(missing_ok=True)

    log(f"PRO-904 crawl starting. {len(cards)} cards. throttle={INTER_REQUEST_SLEEP}s")
    all_printings: list[dict[str, Any]] = []
    failures: list[str] = []
    coverage: dict[str, int] = {}  # nnn -> printing count

    started = time.time()
    for idx, nnn in enumerate(cards, start=1):
        queried = f"OP01-{nnn}"
        try:
            data = scrape_one(api_key, nnn)
        except RuntimeError as e:
            log(f"FAIL {queried}: {e}")
            failures.append(queried)
            coverage[queried] = 0
        else:
            md = data.get("markdown", "")
            printings = parse_printings(md, queried)
            coverage[queried] = len(printings)
            all_printings.extend(printings)
            log(f"OK {queried} -> {len(printings)} printings ({idx}/{len(cards)})")
        time.sleep(INTER_REQUEST_SLEEP)

    elapsed = time.time() - started
    out = {
        "schema_version": 1,
        "ticket": "PRO-904",
        "source": "https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-NNN",
        "queried_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "card_numbers_queried": len(cards),
        "card_numbers_with_results": sum(1 for v in coverage.values() if v > 0),
        "total_printings": len(all_printings),
        "failures": failures,
        "coverage": coverage,
        "printings": all_printings,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    log(
        f"DONE elapsed={elapsed:.1f}s printings={len(all_printings)} failures={len(failures)} out={OUT_PATH}"
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
