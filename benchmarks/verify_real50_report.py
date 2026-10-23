import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    root = Path("reports/real50")
    result = json.loads((root / "comparison.json").read_text())
    errors = []
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto((root / "index.html").resolve().as_uri())
        rows = page.locator("#sites tbody tr:visible")
        assert await rows.count() == 50
        await page.get_by_role("searchbox").fill("who")
        assert await rows.count() == 1
        assert "who" in await rows.first.inner_text()
        await page.get_by_role("searchbox").fill("")
        await page.get_by_role("button", name="Improved", exact=True).click()
        assert await rows.count() == len(result["improved_sites"])
        await page.get_by_role("button", name="Still fails adaptive").click()
        assert await rows.count() == len(result["remaining_adaptive_failures"])
        await page.get_by_role("button", name="All sites").click()
        detail = rows.first.locator("details").first
        await detail.locator("summary").click()
        assert await detail.get_attribute("open") is not None
        assert await detail.locator(".excerpt").last.inner_text()
        await page.evaluate("window.scrollTo(0, 0)")
        await page.screenshot(path=str(root / "preview.png"))
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        await page.screenshot(path=str(root / "preview-mobile.png"))
        assert not errors, errors
        await browser.close()
    print("50-site report verified in Chromium: all rows, filters, evidence details, mobile layout.")


if __name__ == "__main__":
    asyncio.run(main())
