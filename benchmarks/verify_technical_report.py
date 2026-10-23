"""Exercise the actual local report, including mobile overflow and evidence links."""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    comparison = json.loads(Path("reports/technical-final/comparison.json").read_text())
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto("http://127.0.0.1:8788/technical-final/")
        assert await page.locator("#sites tbody tr").count() == 50
        assert await page.locator("#count").inner_text() == "50 sites"
        await page.locator("#search").fill("openstax")
        assert await page.locator("#sites tbody tr:visible").count() == 1
        await page.locator("#sites tbody tr:visible summary").last.click()
        assert "Physics" in await page.locator("#sites tbody tr:visible pre").last.inner_text()
        await page.locator("#search").fill("")
        await page.locator("#filter").select_option("improved")
        assert await page.locator("#sites tbody tr:visible").count() == len(comparison["improved_sites"])
        await page.locator("#filter").select_option("failed")
        assert await page.locator("#sites tbody tr:visible").count() == len(comparison["remaining"])
        await page.locator("#filter").select_option("all")
        await page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
        await page.screenshot(path="reports/technical-final/preview.png")
        await page.screenshot(path="reports/technical-final/preview-full.png", full_page=True)
        for suffix in ("after/run-0-adaptive.json", "exa/run-0-http.json", "verification.json"):
            response = await page.request.get("http://127.0.0.1:8788/technical-final/" + suffix)
            assert response.ok
            assert await response.json()
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        await page.evaluate("window.scrollTo(0, 0)")
        await page.screenshot(path="reports/technical-final/preview-mobile.png")
        await page.screenshot(path="reports/technical-final/preview-mobile-full.png", full_page=True)
        await browser.close()
    assert not errors
    result = {"verified": True, "site_rows": 50, "search_and_filters": True,
              "real_extracted_text_visible": True, "raw_evidence_links": True,
              "mobile_no_horizontal_overflow": True, "page_errors": errors}
    Path("reports/technical-final/report-verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
