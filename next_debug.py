import json, re, requests

p = json.load(open("/data/prices.json"))
it = next(iter(p.values()))
pid = it["product_id"]
url = f"https://www.tcgplayer.com/product/{pid}"

r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=20)
html = r.text or ""
print("PID", pid, "HTTP", r.status_code, "len", len(html))

# Check Next.js payload
m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
print("has __NEXT_DATA__", bool(m))

def deep_find_urls(obj, found):
    if isinstance(obj, dict):
        for v in obj.values():
            deep_find_urls(v, found)
    elif isinstance(obj, list):
        for v in obj:
            deep_find_urls(v, found)
    elif isinstance(obj, str):
        s = obj.strip()
        if s.startswith("http") or s.startswith("//"):
            found.add(s)

if m:
    raw = m.group(1)
    try:
        data = json.loads(raw)
        urls = set()
        deep_find_urls(data, urls)
        # Print a few interesting ones (image-ish first)
        imageish = [u for u in urls if any(k in u.lower() for k in ["img","image","cdn","cloudfront","tcgplayer"])]
        print("urls_found", len(urls))
        print("imageish_found", len(imageish))
        for u in list(imageish)[:25]:
            print("  ", u)
    except Exception as e:
        print("NEXT_DATA json parse failed:", e)
else:
    # fallback: show ANY urls found in HTML
    urls = set(re.findall(r'["\']((?:https?:)?//[^"\']+)["\']', html))
    print("urls_in_html", len(urls))
    for u in list(urls)[:25]:
        print("  ", u)
