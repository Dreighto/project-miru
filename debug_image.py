import json, re, requests
p = json.load(open('/data/prices.json'))
it = next(iter(p.values()))
pid = it['product_id']
url = f'https://www.tcgplayer.com/product/{pid}'
r = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=20, allow_redirects=True)
html = r.text or ''
print('PID', pid)
print('HTTP', r.status_code)
print('FINAL', r.url)
print('len(html)', len(html))
print('has og:image', 'og:image' in html)
print('has twitter:image', 'twitter:image' in html)

m1 = re.search(r'property=[\"\']og:image[\"\'][^>]*content=[\"\']([^\"\']+)', html, re.I)
m2 = re.search(r'name=[\"\']twitter:image[\"\'][^>]*content=[\"\']([^\"\']+)', html, re.I)
print('og:image content', m1.group(1) if m1 else None)
print('twitter:image content', m2.group(1) if m2 else None)
