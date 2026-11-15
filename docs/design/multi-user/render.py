"""把 docs/design/multi-user/sheet.html 渲染成 PNG 设计稿。

用法：.venv/bin/python docs/design/multi-user/render.py
输出：docs/design/multi-user/preview.png（2x）

注意：Chromium 不会绘制视口外的 iframe，所以先按内容高度撑开视口，
等所有画面进入视口后再整页截图，否则第 2、3 张会是空白。
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "preview.png"
WIDTH = 1520
SCALE = 2


def main() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": 900}, device_scale_factor=SCALE)
        page.goto(HERE.joinpath("sheet.html").as_uri())
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(600)
        height = page.evaluate("document.body.scrollHeight")
        page.set_viewport_size({"width": WIDTH, "height": height})
        page.wait_for_timeout(900)
        page.screenshot(path=str(OUT), full_page=True)
        browser.close()
    print(f"{OUT}  {OUT.stat().st_size // 1024} KB  ({WIDTH}x{height})")


if __name__ == "__main__":
    main()
