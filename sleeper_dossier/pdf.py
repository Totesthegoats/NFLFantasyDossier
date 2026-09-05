"""
pdf.py — Render the paginated dossier HTML to PDF via headless Chromium.

Uses Playwright so Chart.js charts actually execute and render before the
PDF is captured — WeasyPrint has no JS engine and produces blank chart
boxes. Two-step chart wait is implemented:
  1. window.__chartsExpected / __chartsRendered counters (set by pdf_render.py's
     chart JS) let Playwright know exactly when all charts are done.
  2. document.fonts.ready ensures no missing-glyph boxes in text.

Physical page size is A4 landscape (297mm × 210mm), controlled by the
CSS @page rule in pdf_render.py — we set preferCSSPageSize=True so
Playwright honours that instead of defaulting to portrait.

WeasyPrint is documented here as a lighter fallback ONLY if charts are
pre-rendered to static images; it is not implemented.

One-time setup:
    pip install playwright
    playwright install chromium
"""

from __future__ import annotations
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

_INSTALL_HINT = ("PDF export needs the 'playwright' package and its Chromium browser. "
                 "Install with: pip install playwright && playwright install chromium")


def html_to_pdf(html: str, output_path: str, *, timeout_ms: int = 20_000) -> None:
    """Convert `html` to a PDF at `output_path`.

    `timeout_ms` is the maximum time to wait for Chart.js to finish
    rendering all charts before the PDF is captured. Increase it for
    very large reports or slow machines. A warning is printed (not an
    error) if the timeout fires so the PDF is still written.
    """
    if sync_playwright is None:
        raise RuntimeError(_INSTALL_HINT)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            raise RuntimeError(f"{_INSTALL_HINT} ({e})") from e

        page = browser.new_page()

        # set_content + networkidle handles CDN chart.js/datalabels scripts
        page.set_content(html, wait_until="networkidle")

        # ── Wait for all Chart.js charts to finish rendering ─────────────
        # pdf_render.py sets window.__chartsExpected (total chart count) and
        # increments window.__chartsRendered in each chart's animation.onComplete
        # callback (animation.duration = 0, so "complete" fires synchronously
        # after the first frame — typically < 100ms per chart).
        # If __chartsExpected is 0 (no charts on this page), skip immediately.
        try:
            page.wait_for_function(
                ("() => { "
                 "  const exp = window.__chartsExpected || 0; "
                 "  const got = window.__chartsRendered || 0; "
                 "  return exp === 0 || got >= exp; "
                 "}"),
                timeout=timeout_ms,
            )
        except Exception:
            expected = page.evaluate("window.__chartsExpected || 0")
            rendered = page.evaluate("window.__chartsRendered || 0")
            print(
                f"  [pdf warn] chart wait timed out: {rendered}/{expected} charts rendered. "
                f"PDF will be written anyway — charts may be blank if this is non-zero.",
                file=sys.stderr,
            )

        # Ensure web fonts are fully loaded (avoids missing-glyph boxes)
        page.evaluate("async () => { await document.fonts.ready; }")

        # ── Print to PDF ────────────────────────────────────────────────────
        # prefer_css_page_size=True honours @page { size: 297mm 210mm; } from
        # pdf_render._PDF_CSS — without it Playwright defaults to A4 portrait,
        # overriding the landscape declaration.
        # print_background=True is REQUIRED: without it the navy header bands,
        # coloured card heads, and background fills all print white.
        # No margin dict here — @page { margin: 0; } handles it.
        page.pdf(
            path=output_path,
            landscape=True,
            print_background=True,
            prefer_css_page_size=True,
        )

        browser.close()
