"""
pdf.py — Render the HTML dossier to PDF via headless Chromium (Playwright).

Playwright actually executes the page's JavaScript, so the PDF is a
byte-for-byte print of what you'd see opening the HTML in a real browser —
including the Chart.js charts baked in as rendered graphics. WeasyPrint
can't do this (no JS engine), which is why this needs a real browser.

Requires (one-time setup):
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

_INSTALL_HINT = ("PDF export needs the 'playwright' package and its Chromium browser. "
                 "Install with: pip install playwright && playwright install chromium")


def html_to_pdf(html: str, output_path: str) -> None:
    if sync_playwright is None:
        raise RuntimeError(_INSTALL_HINT)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            raise RuntimeError(f"{_INSTALL_HINT} ({e})") from e
        page = browser.new_page()
        page.set_content(html, wait_until="networkidle")
        page.pdf(
            path=output_path,
            format="A4",
            print_background=True,
            margin={"top": "0.4in", "bottom": "0.4in", "left": "0.3in", "right": "0.3in"},
        )
        browser.close()
