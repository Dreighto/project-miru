from playwright.sync_api import sync_playwright
import time

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)
context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
page = context.new_page()

# Test homepage
page.goto('http://127.0.0.1:18080/')
time.sleep(3)

# Check for horizontal overflow
body_width = page.evaluate('document.body.scrollWidth')
viewport_width = page.evaluate('window.innerWidth')
has_overflow = body_width > viewport_width

print(f'Body scrollWidth: {body_width}px')
print(f'Viewport innerWidth: {viewport_width}px')
print(f'Has horizontal overflow: {has_overflow}')

# Scroll down to see watchlist section
page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
time.sleep(2)
page.screenshot(path='.codex_mobile_home_watchlist.png')
print('Watchlist section captured')

browser.close()
p.stop()
