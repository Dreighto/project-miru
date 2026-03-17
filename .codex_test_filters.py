from playwright.sync_api import sync_playwright
import time

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)
context = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
page = context.new_page()

# Go to library
page.goto('http://127.0.0.1:18080/library')
time.sleep(3)

# Take screenshot of the filter area at the top
page.screenshot(path='.codex_mobile_library_filters.png')
print('Filter controls captured')

# Check the select dropdowns
selects = page.locator('select').all()
for i, select in enumerate(selects):
    # Get the select element's text
    value = select.evaluate('el => el.options[el.selectedIndex].text')
    print(f'Select {i}: "{value}"')

browser.close()
p.stop()
