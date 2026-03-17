from playwright.sync_api import sync_playwright
import time

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)
context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
page = context.new_page()

# Go to library
page.goto('http://127.0.0.1:18080/library')
print('Loaded library page')

# Wait for deferred content
time.sleep(5)

# Scroll to trigger lazy loading
for i in range(3):
    page.evaluate('window.scrollBy(0, 300)')
    time.sleep(0.5)

time.sleep(2)

# Try to find card containers
cards = page.locator('.libraryCard, .card-item, [data-card-code], .library-card-wrapper').all()
print(f'Found {len(cards)} card containers')

if cards:
    cards[0].click()
    time.sleep(2)
    page.screenshot(path='.codex_mobile_test_card_detail.png')
    print('Card detail captured')
else:
    # Try clicking any element in the grid
    page.evaluate('''
        const el = document.querySelector('#libraryDeferredContent img');
        if (el) {
            el.click();
            console.log('Clicked image');
        }
    ''')
    time.sleep(2)
    page.screenshot(path='.codex_mobile_test_card_detail.png')
    print('Card detail captured via JS click')

time.sleep(2)
browser.close()
p.stop()
