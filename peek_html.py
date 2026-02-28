import json, re, requests
p = json.load(open("/data/prices.json"))
it = next(iter(p.values()))
pid = it["product_id"]
url = f"https://www.tcgplayer.com/product/{pid}"
r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
html = r.text or ""
print("PID", pid, "HTTP", r.status_code, "len", len(html))

# Count raw occurrences
print("contains .jpg:", ".jpg" in html.lower())
print("contains .png:", ".png" in html.lower())
print("contains u002f:", "\\u002f" in html.lower())
print("contains \\/ :", "\\/" in html)

# Find any image-ish snippets (including escaped URLs)
patterns = [
    r'(https?://[^"\\s]+\\.(?:jpg|jpeg|png|webp)[^"\\s]*)',
    r'((?:https?:)?//[^"\\s]+\\.(?:jpg|jpeg|png|webp)[^"\\s]*)',
    r'(https?:\\\\/\\\\/[^"\\s]+)',        # https:\/\/...
    r'(https?:\\u002f\\u002f[^"\\s]+)',    # https:\u002F\u002F...
    r'("imageUrl"\\s*:\\s*"[^"]+")',
    r'("imageURL"\\s*:\\s*"[^"]+")',
    r'("image"\\s*:\\s*"[^"]+")',
]
for pat in patterns:
    m = re.search(pat, html, re.I)
    print("pattern", pat, "=>", (m.group(1)[:180] if m else None))
