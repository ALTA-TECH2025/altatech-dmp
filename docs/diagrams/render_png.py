"""SVG → PNG：用 Playwright（Chromium）開 SVG 截圖，給 docs/12 的 PNG 預覽用。用法：python3 docs/diagrams/render_png.py phase1-*.svg（不帶參數則全部 *.svg）。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
names = sys.argv[1:] or sorted(p.name for p in HERE.glob("*.svg"))
with sync_playwright() as p:
    b = p.chromium.launch()
    for n in names:
        svg = (HERE / n).read_text(encoding="utf-8")
        w = int(svg.split('width="')[1].split('"')[0]); h = int(svg.split('height="')[1].split('"')[0])
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        pg.set_content(f"<html><body style='margin:0'>{svg}</body></html>")
        pg.wait_for_timeout(300)
        out = HERE / n.replace(".svg", ".png"); pg.screenshot(path=str(out)); print("wrote", out.name); pg.close()
    b.close()
