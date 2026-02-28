import json
import time
import os
import re
from flask import Flask, Response, send_from_directory

PRICES_PATH = "/data/prices.json"
IMAGES_ROOT = "/images"

CODE_RE = re.compile(r"\b([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\b", re.I)
ANY_CODE_VARIANT_RE = re.compile(r"([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\(([^)]+)\)", re.I)

app = Flask(__name__)

ALT_MARKERS = (
    "alternate art", "alt art", "alt-art", "alternate-art",
    "parallel", "manga", "special art", "special",
    "pirate foil", "promo foil", "foil",
)

ILLUST_MARKERS = (
    "illustration", "illustration box", "illustrationbox",
    "illustrationboxvol", "illustrationboxvol.",
    "illustration box vol", "illustration box vol.",
)


def load_prices():
    try:
        with open(PRICES_PATH, "r") as f:
            obj = json.load(f)
            return list(obj.values()) if isinstance(obj, dict) else []
    except Exception:
        return []


def fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


def wants_alt(name: str) -> bool:
    s = (name or "").lower()
    return any(m in s for m in ALT_MARKERS)


def wants_illustration(name: str) -> bool:
    s = (name or "").lower()
    return any(m in s for m in ILLUST_MARKERS)


def variant_is_altish(v: str) -> bool:
    v = (v or "").strip().lower()
    if not v:
        return False
    if v == "alt":
        return True
    return any(m in v for m in ALT_MARKERS)


def variant_is_illustrationish(v: str) -> bool:
    v = (v or "").strip().lower()
    if not v:
        return False
    return any(m in v for m in ILLUST_MARKERS)


def build_image_index():
    """
    by_base:
      "P-093(ILLUSTRATIONBOXVOL.6)" -> ".../P-093(IllustrationBoxVol.6).png"
      "OP11-067(ALT)" -> ".../OP11-067(Alt).png"
      "OP11-067" -> ".../OP11-067.png"

    by_code:
      "OP11-067" -> {"normal": "...", "alt": "...", "illust": "...", "variants": {...}}
    """
    by_base = {}
    by_code = {}

    if not os.path.isdir(IMAGES_ROOT):
        return {"by_base": by_base, "by_code": by_code}

    for root, _, files in os.walk(IMAGES_ROOT):
        for fn in files:
            low = fn.lower()
            if not low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue

            base, _ext = os.path.splitext(fn)
            if not base:
                continue

            abs_path = os.path.join(root, fn)
            rel_path = os.path.relpath(abs_path, IMAGES_ROOT).replace("\\", "/")

            by_base[base.upper()] = rel_path

            m = re.match(r"^([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})(\(([^)]+)\))?$", base, re.I)
            if not m:
                continue

            code = (m.group(1) or "").upper()
            variant = (m.group(3) or "").strip()
            variant_l = variant.lower()

            entry = by_code.setdefault(code, {"variants": {}})

            if variant == "":
                entry["normal"] = rel_path
            else:
                entry["variants"][variant_l] = rel_path
                if variant_is_altish(variant_l):
                    entry["alt"] = rel_path
                if variant_is_illustrationish(variant_l):
                    entry["illust"] = rel_path

    return {"by_base": by_base, "by_code": by_code}


IMAGE_INDEX = build_image_index()


@app.get("/img/<path:filename>")
def img(filename):
    return send_from_directory(IMAGES_ROOT, filename)


def choose_image_path(name: str, code: str):
    """
    Selection order:

    1) If name contains any CODE(Variant) and exact base exists -> use it
    2) If illustration requested -> prefer illust
    3) If alt requested -> prefer alt
    4) Default normal then alt
    """
    idx_base = IMAGE_INDEX["by_base"]
    idx_code = IMAGE_INDEX["by_code"]

    # 1) Exact CODE(Variant) anywhere in name
    candidates = []
    for m in ANY_CODE_VARIANT_RE.finditer(name or ""):
        c = (m.group(1) or "").upper()
        v = (m.group(2) or "").strip()
        full = f"{c}({v})".upper()
        if full in idx_base:
            candidates.append((c, v, full))

    if candidates:
        if wants_illustration(name):
            for _c, v, full in candidates:
                if variant_is_illustrationish(v):
                    return idx_base[full]
        if wants_alt(name):
            for _c, v, full in candidates:
                if variant_is_altish(v):
                    return idx_base[full]
        return idx_base[candidates[0][2]]

    if not code:
        return None

    entry = idx_code.get(code, {}) or {}
    variants = entry.get("variants", {}) or {}

    if wants_illustration(name):
        if entry.get("illust"):
            return entry["illust"]
        for vname_l, path in variants.items():
            if variant_is_illustrationish(vname_l):
                return path
        return entry.get("normal") or entry.get("alt")

    if wants_alt(name):
        if entry.get("alt"):
            return entry["alt"]
        for vname_l, path in variants.items():
            if variant_is_altish(vname_l):
                return path
        return entry.get("alt") or entry.get("normal")

    return entry.get("normal") or entry.get("alt")


def clean_display_name(name: str, code: str) -> str:
    """
    Removes duplicate leading codes like:
      "P-088 P-088 Trafalgar Law" -> "Trafalgar Law"
      "EB03-062 EB03-062 ..." -> "..."
    """
    s = (name or "").strip()
    if not s:
        return s

    if code:
        re_lead = re.compile(rf"^\s*(?:{re.escape(code)}(?:\([^)]+\))?\s+)+", re.I)
        s2 = re_lead.sub("", s).strip()
        if s2:
            s = s2

    s = re.sub(r"^\s*([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})(?:\([^)]+\))?\s+", "", s, flags=re.I).strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def calc_deal(price: float, target: float):
    """
    Returns: hit(bool), pct(float), label(str), tier(str)
    pct positive when under target: (target - price)/target * 100
    """
    if target is None or price is None:
        return False, None, "", "watch"

    if target <= 0:
        return False, None, "", "watch"

    diff = target - price
    pct = (diff / target) * 100.0

    if price <= target:
        if pct >= 15:
            tier = "deal3"
        elif pct >= 5:
            tier = "deal2"
        else:
            tier = "deal1"
        return True, pct, f"▼ {pct:.1f}% under target", tier
    else:
        tier = "over"
        return False, pct, f"▲ {abs(pct):.1f}% above target", tier


@app.get("/")
def index():
    items = load_prices()

    enriched = []
    for it in items:
        name = (it.get("name", "") or "").strip()
        code = (it.get("code") or "").upper()

        if not code:
            m = CODE_RE.search(name)
            code = m.group(1).upper() if m else ""

        img_path = choose_image_path(name, code)
        display_name = clean_display_name(name, code)

        price_f = None
        target_f = None
        price_txt = ""
        target_txt = ""

        try:
            price_f = float(it.get("price", 0))
            price_txt = f"${price_f:.2f}"
        except Exception:
            pass

        try:
            target_f = float(it.get("target", 0))
            target_txt = f"${target_f:.2f}"
        except Exception:
            pass

        hit, pct, pct_label, tier = calc_deal(price_f, target_f)

        enriched.append({
            "it": it,
            "code": code,
            "name": name,
            "display_name": display_name,
            "img_path": img_path,
            "price_txt": price_txt,
            "target_txt": target_txt,
            "hit": hit,
            "pct": pct if pct is not None else -9999.0,
            "pct_label": pct_label,
            "tier": tier,
        })

    def sort_key(x):
        it = x["it"]
        last_ts = int(it.get("last_checked_ts", 0) or 0)
        hit_bucket = 0 if x["hit"] else 1
        deal_rank = -x["pct"] if x["hit"] else 0
        recency = -last_ts
        nm = (x["display_name"] or "").lower()
        return (hit_bucket, deal_rank, recency, nm)

    enriched.sort(key=sort_key)

    cards_html = []
    for x in enriched:
        it = x["it"]
        code = x["code"]
        display_name = x["display_name"]
        img_path = x["img_path"]

        hit = x["hit"]
        tier = x["tier"]

        badge = '<span class="badge buy">BUY</span>' if hit else '<span class="badge watch">WATCH</span>'
        pct_tag = f'<div class="pct {tier}">{x["pct_label"]}</div>' if x["pct_label"] else ""

        if img_path:
            img_tag = f'<img class="thumb" src="/img/{img_path}" loading="lazy" alt="">'
        else:
            img_tag = '<div class="thumb ph">No image</div>'

        card_class = f"card {tier}" if tier else "card"
        price_class = "val current buy" if hit else "val current"

        cards_html.append(f"""
        <div class="{card_class}">
          <div class="row">
            <div class="left">
              {img_tag}
            </div>

            <div class="right">
              <div class="top">
                <div class="codebar">
                  <span class="code">{code}</span>
                  {badge}
                </div>
                <div class="ts">{fmt_time(it.get("last_checked_ts", 0))}</div>
              </div>

              <div class="title">{display_name}</div>

              {pct_tag}

              <div class="prices">
                <div class="pill">
                  <div class="label">Current</div>
                  <div class="{price_class}">{x["price_txt"]}</div>
                </div>
                <div class="pill">
                  <div class="label">Target</div>
                  <div class="val target">{x["target_txt"]}</div>
                </div>
              </div>

              <a class="buybtn" href="{it.get("url","")}" target="_blank" rel="noopener">Open on TCGplayer</a>
            </div>
          </div>
        </div>
        """)

    idx_base = IMAGE_INDEX["by_base"]
    idx_code = IMAGE_INDEX["by_code"]

    html = f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>TCG Watcher</title>
      <style>
        :root {{
          --bg: #070a0f;
          --stroke: rgba(255,255,255,0.10);
          --muted: rgba(255,255,255,0.55);
          --muted2: rgba(255,255,255,0.35);

          --blueStroke: rgba(78,161,255,0.38);
          --gText: rgba(200, 255, 225, 0.95);
          --reds: rgba(255, 90, 90, 0.38);

          --g1s: rgba(66, 214, 138, 0.45);
          --g2s: rgba(66, 214, 138, 0.62);
          --g3s: rgba(66, 214, 138, 0.85);
        }}

        body {{
          background: radial-gradient(1200px 800px at 20% -10%, rgba(78,161,255,0.12), transparent 55%),
                      radial-gradient(900px 700px at 110% 10%, rgba(66,214,138,0.10), transparent 55%),
                      var(--bg);
          color: white;
          font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
          padding: 14px;
          margin: 0;
          font-size: 13px;
        }}

        h2 {{
          margin: 8px 0 6px 0;
          font-size: 20px;
          letter-spacing: 0.2px;
        }}

        .meta {{
          color: var(--muted);
          font-size: 12px;
          margin-bottom: 12px;
        }}

        .grid {{
          display: grid;
          grid-template-columns: 1fr;
          gap: 12px;
        }}

        @media (min-width: 900px) {{
          .grid {{
            grid-template-columns: 1fr 1fr;
          }}
        }}

        .card {{
          background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012));
          border: 1px solid var(--stroke);
          border-radius: 14px;
          padding: 10px;
          overflow: hidden;
          position: relative;
          box-shadow: 0 14px 30px rgba(0,0,0,0.35);
        }}

        .card.deal1 {{
          border-color: var(--g1s);
          box-shadow:
            0 0 0 1px rgba(66,214,138,0.18) inset,
            0 0 14px rgba(66,214,138,0.12),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .card.deal2 {{
          border-color: var(--g2s);
          box-shadow:
            0 0 0 1px rgba(66,214,138,0.24) inset,
            0 0 18px rgba(66,214,138,0.18),
            0 0 40px rgba(66,214,138,0.12),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .card.deal3 {{
          border-color: var(--g3s);
          box-shadow:
            0 0 0 1px rgba(120,255,190,0.22) inset,
            0 0 22px rgba(120,255,190,0.22),
            0 0 55px rgba(120,255,190,0.16),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .card.deal3::before {{
          content: "";
          position: absolute;
          inset: -2px;
          border-radius: 16px;
          background: conic-gradient(from 180deg,
            rgba(120,255,190,0.0),
            rgba(120,255,190,0.25),
            rgba(120,255,190,0.0)
          );
          filter: blur(10px);
          opacity: 0.7;
          pointer-events: none;
        }}

        .card.over {{
          border-color: var(--reds);
          box-shadow:
            0 0 0 1px rgba(255,90,90,0.14) inset,
            0 0 14px rgba(255,90,90,0.10),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .row {{
          display: grid;
          grid-template-columns: 124px 1fr;
          gap: 12px;
          align-items: start;
          position: relative;
          z-index: 1;
        }}

        @media (max-width: 360px) {{
          .row {{ grid-template-columns: 120px 1fr; }}
        }}

        .left {{
          display: flex;
          justify-content: center;
          align-items: flex-start;
        }}

        /* IMPORTANT: doubled braces for f-string safety */
        .thumb {{
          width: 124px;
          border-radius: 12px;
          border: 1px solid rgba(255,255,255,0.14);
          background: rgba(0,0,0,0.35);
          box-shadow:
            0 6px 14px rgba(0,0,0,0.45),
            0 0 0 1px rgba(255,255,255,0.05) inset;
          display: block;
        }}

        .ph {{
          width: 124px;
          height: 124px;
          border-radius: 12px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: rgba(255,255,255,0.06);
          color: var(--muted);
          border: 1px solid rgba(255,255,255,0.10);
        }}

        .top {{
          display: flex;
          justify-content: space-between;
          gap: 10px;
          align-items: flex-start;
          margin-bottom: 6px;
        }}

        .codebar {{
          display: flex;
          align-items: center;
          gap: 8px;
          flex-wrap: wrap;
        }}

        .code {{
          font-weight: 900;
          letter-spacing: 0.8px;
          font-size: 12px;
          color: rgba(255,255,255,0.85);
          background: rgba(255,255,255,0.06);
          border: 1px solid rgba(255,255,255,0.10);
          padding: 3px 8px;
          border-radius: 999px;
        }}

        .badge {{
          font-size: 11px;
          font-weight: 850;
          letter-spacing: 0.6px;
          padding: 3px 10px;
          border-radius: 999px;
          border: 1px solid rgba(255,255,255,0.10);
        }}

        .badge.watch {{
          background: rgba(255,255,255,0.06);
          color: rgba(255,255,255,0.80);
        }}

        .badge.buy {{
          background: rgba(66,214,138,0.20);
          color: var(--gText);
          border-color: rgba(120,255,190,0.35);
        }}

        .ts {{
          color: var(--muted2);
          font-size: 11px;
          line-height: 1.15;
          text-align: right;
          max-width: 135px;
        }}

        .title {{
          font-size: 14px;
          font-weight: 800;
          line-height: 1.15;
          word-break: break-word;
          margin-bottom: 6px;
        }}

        .pct {{
          font-size: 12px;
          font-weight: 800;
          margin-bottom: 10px;
          padding: 6px 10px;
          border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.10);
          background: rgba(255,255,255,0.04);
          color: rgba(255,255,255,0.82);
        }}

        .pct.deal1 {{
          border-color: rgba(66,214,138,0.28);
          background: rgba(66,214,138,0.08);
          color: rgba(200,255,225,0.92);
        }}

        .pct.deal2 {{
          border-color: rgba(66,214,138,0.40);
          background: rgba(66,214,138,0.10);
          color: rgba(200,255,225,0.96);
        }}

        .pct.deal3 {{
          border-color: rgba(120,255,190,0.60);
          background: rgba(120,255,190,0.12);
          color: rgba(220,255,235,0.98);
          box-shadow: 0 0 18px rgba(120,255,190,0.10);
        }}

        .pct.over {{
          border-color: rgba(255,90,90,0.35);
          background: rgba(255,90,90,0.08);
          color: rgba(255,210,210,0.95);
        }}

        .prices {{
          display: grid;
          gap: 8px;
          margin-bottom: 10px;
        }}

        .pill {{
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 12px;
          padding: 8px 10px;
        }}

        .label {{
          color: var(--muted);
          font-size: 11px;
          margin-bottom: 4px;
        }}

        .val {{
          font-weight: 950;
          font-size: 20px;
          letter-spacing: 0.3px;
        }}

        .val.current {{
          color: rgba(255,255,255,0.92);
        }}

        .val.current.buy {{
          color: rgba(220,255,235,0.98);
          text-shadow: 0 0 14px rgba(120,255,190,0.18);
        }}

        .val.target {{
          color: rgba(255,255,255,0.86);
        }}

        .buybtn {{
          display: block;
          text-align: center;
          padding: 10px 10px;
          border-radius: 12px;
          background: linear-gradient(180deg, rgba(78,161,255,0.22), rgba(78,161,255,0.14));
          border: 1px solid var(--blueStroke);
          color: rgba(215,235,255,0.95);
          font-weight: 850;
          letter-spacing: 0.2px;
          text-decoration: none;
          user-select: none;
        }}

        .card.deal3 .buybtn {{
          background: linear-gradient(180deg, rgba(120,255,190,0.20), rgba(66,214,138,0.12));
          border-color: rgba(120,255,190,0.45);
        }}

        .buybtn:active {{
          transform: translateY(1px);
          filter: brightness(1.05);
        }}
      </style>
    </head>
    <body>
      <h2>TCG Watcher Dashboard</h2>
      <div class="meta">
        Auto-refresh every 60s • {len(enriched)} items • Images indexed: base={len(idx_base)} codes={len(idx_code)}
      </div>

      <div class="grid">
        {''.join(cards_html) if cards_html else '<div class="card">No data yet.</div>'}
      </div>

      <script>setTimeout(()=>location.reload(),60000);</script>
    </body>
    </html>
    """

    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)