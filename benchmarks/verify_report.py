"""Exercise the generated report in a real browser and save a preview."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    errors = []
    report = Path("reports/index.html").resolve()
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(report.as_uri())
        rows = page.locator("#case-table tbody tr:visible")
        assert await rows.count() == 22
        await page.get_by_role("searchbox").fill("/js")
        assert await rows.count() == 3
        await page.get_by_role("searchbox").fill("")
        await page.get_by_role("button", name="Strategy misses").click()
        assert await rows.count() == 3
        await page.get_by_role("button", name="All cases").click()
        assert await rows.count() == 22
        details = rows.first.locator("details").first
        await details.locator("summary").click()
        assert await details.get_attribute("open") is not None
        assert await details.locator(".excerpt").inner_text()
        await page.evaluate("window.scrollTo(0, 0)")
        await page.screenshot(path="reports/preview.png")
        await page.set_viewport_size({"width": 390, "height": 844})
        assert await page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), "Report overflows the mobile viewport"
        await page.screenshot(path="reports/preview-mobile.png")
        assert not errors, errors
        await browser.close()
    print("Report verified: filters, 22 case rows, expandable evidence, mobile layout, no JS errors.")


if __name__ == "__main__":
    asyncio.run(main())
