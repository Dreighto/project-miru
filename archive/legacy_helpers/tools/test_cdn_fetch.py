"""Read-only: test Bandai CDN image URLs (no DB or file writes)."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

TESTS = [
    ("EB01-003 alt", "https://en.onepiece-cardgame.com/images/cardlist/card/EB01-003_alt.png"),
    ("EB02-003 alt", "https://en.onepiece-cardgame.com/images/cardlist/card/EB02-003_alt.png"),
    ("OP01-016 sp (Nami SP)", "https://en.onepiece-cardgame.com/images/cardlist/card/OP01-016_sp.png"),
    ("EB01-003 mr (manga rare)", "https://en.onepiece-cardgame.com/images/cardlist/card/EB01-003_mr.png"),
    ("OP06-020 tr (treasure rare)", "https://en.onepiece-cardgame.com/images/cardlist/card/OP06-020_tr.png"),
]

TIMEOUT = 15
DELAY = 1.0
USER_AGENT = "tcg-watcher-test_cdn_fetch/1.0 (+local)"


def main() -> int:
    passed = 0
    for i, (_label, url) in enumerate(TESTS):
        if i > 0:
            time.sleep(DELAY)

        status_code = None
        reason = ""
        ctype = ""
        clen: int | str = "?"
        body_len = 0
        ok = False

        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "image/*,*/*"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status_code = resp.getcode()
                reason = getattr(resp, "reason", "") or ""
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
                cl_raw = resp.headers.get("Content-Length")
                body = resp.read()
                body_len = len(body)
                if cl_raw is not None and cl_raw.isdigit():
                    clen = int(cl_raw)
                else:
                    clen = body_len

                ok = (
                    status_code == 200
                    and ctype.lower().startswith("image/")
                    and body_len > 10240
                )
        except urllib.error.HTTPError as e:
            status_code = e.code
            reason = e.reason or ""
            ctype = (e.headers.get("Content-Type") or "").split(";")[0].strip() if e.headers else ""
            cl_raw = e.headers.get("Content-Length") if e.headers else None
            try:
                err_body = e.read()
                body_len = len(err_body)
            except Exception:
                body_len = 0
            if cl_raw is not None and str(cl_raw).isdigit():
                clen = int(cl_raw)
            else:
                clen = body_len if body_len else "?"
        except urllib.error.URLError as e:
            status_code = "ERR"
            reason = str(e.reason) if e.reason else str(e)
            ctype = ""
            clen = "?"
        except Exception as e:
            status_code = "ERR"
            reason = str(e)
            ctype = ""
            clen = "?"

        pf = "PASS" if ok else "FAIL"
        if ok:
            passed += 1

        print(
            f"{url} | status={status_code} | content_type={ctype!r} | "
            f"content_length={clen} (body_bytes={body_len}) | {pf}"
        )
        if not ok and reason and status_code != 200:
            print(f"  -> {status_code} {reason}")

    print()
    print(f"Summary: {passed}/5 passed")
    return 0 if passed == 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
