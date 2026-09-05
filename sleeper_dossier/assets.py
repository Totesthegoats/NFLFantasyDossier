"""
assets.py — Screenshot each award card and chart from the weekly report HTML
as individual PNGs, suitable for use on a website.

Uses the same Playwright/Chromium setup as pdf.py so Chart.js charts are
fully rendered before any element is captured.

Output naming:
  card_<slug>.png          — one per .award-card element (title slugified)
  chart_<canvas-id>.png    — one per <canvas> element (keyed by its HTML id)
  table_<label>.png        — one per <table> element (nearest section-label heading slugified)
"""

from __future__ import annotations
import os
import re
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

_INSTALL_HINT = ("Asset export needs playwright: "
                 "pip install playwright && playwright install chromium")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def generate_assets(html: str, out_dir: str, *, timeout_ms: int = 20_000) -> list[str]:
    """Screenshot every award card and chart canvas from `html` to `out_dir`.

    Returns the list of file paths written.
    """
    if sync_playwright is None:
        raise RuntimeError(_INSTALL_HINT)

    os.makedirs(out_dir, exist_ok=True)
    saved: list[str] = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            raise RuntimeError(f"{_INSTALL_HINT} ({e})") from e

        # Wide viewport + 2× device scale for high-DPI output (~retina quality)
        page = browser.new_page(viewport={"width": 1400, "height": 900},
                                device_scale_factor=2)
        page.set_content(html, wait_until="networkidle")

        # Wait for all Chart.js charts to finish (same mechanism as pdf.py)
        try:
            page.wait_for_function(
                ("() => { "
                 "  const e = window.__chartsExpected || 0; "
                 "  const g = window.__chartsRendered || 0; "
                 "  return e === 0 || g >= e; "
                 "}"),
                timeout=timeout_ms,
            )
        except Exception:
            print("  [assets warn] chart wait timed out — chart PNGs may be blank",
                  file=sys.stderr)

        page.evaluate("async () => { await document.fonts.ready; }")

        # ── Award cards ───────────────────────────────────────────────────────
        cards = page.query_selector_all(".award-card")
        seen: dict[str, int] = {}
        for card in cards:
            head = card.query_selector(".head")
            title = head.inner_text().strip() if head else "card"
            slug = _slug(title)
            n = seen.get(slug, 0)
            seen[slug] = n + 1
            suffix = f"_{n}" if n else ""
            path = os.path.join(out_dir, f"card_{slug}{suffix}.png")
            card.screenshot(path=path)
            saved.append(path)
            print(f"  [assets] {path}", file=sys.stderr)

        # ── Tables ───────────────────────────────────────────────────────────
        # Tag each table with its nearest preceding section-label heading so
        # the filename is human-readable rather than just a sequential index.
        page.evaluate("""
() => {
  const findLabel = (el) => {
    let sib = el ? el.previousElementSibling : null;
    while (sib) {
      if (sib.classList.contains('section-label')) return sib.innerText.trim();
      const lbl = sib.querySelector && sib.querySelector('.section-label');
      if (lbl) return lbl.innerText.trim();
      if (/^H[2-4]$/.test(sib.tagName)) return sib.innerText.trim();
      sib = sib.previousElementSibling;
    }
    return null;
  };
  document.querySelectorAll('table').forEach((tbl, i) => {
    const name = findLabel(tbl) || findLabel(tbl.parentElement) || ('table_' + (i + 1));
    tbl.dataset.assetName = name;
  });
}
        """)
        tables = page.query_selector_all("table")
        seen_tbl: dict[str, int] = {}
        for table in tables:
            raw = table.get_attribute("data-asset-name") or "table"
            slug = _slug(raw)
            n = seen_tbl.get(slug, 0)
            seen_tbl[slug] = n + 1
            suffix = f"_{n}" if n else ""
            path = os.path.join(out_dir, f"table_{slug}{suffix}.png")
            table.screenshot(path=path)
            saved.append(path)
            print(f"  [assets] {path}", file=sys.stderr)

        # ── Charts ───────────────────────────────────────────────────────────
        canvases = page.query_selector_all("canvas")
        for canvas in canvases:
            cid = canvas.get_attribute("id") or "chart"
            path = os.path.join(out_dir, f"chart_{cid}.png")
            canvas.screenshot(path=path)
            saved.append(path)
            print(f"  [assets] {path}", file=sys.stderr)

        browser.close()

    print(f"  [assets] {len(saved)} files written to {out_dir}", file=sys.stderr)
    return saved
