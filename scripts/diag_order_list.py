"""임시 진단 스크립트: 실제 날짜 데이터 행을 정확히 찾아 클릭해서
상품별 주문 목록 팝업/API를 확인한다."""
import json
import os
import re

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_DAILY_PAGE = "https://partner.musinsa.com/statistics/order-daily"
DATE_RE = re.compile(r"20\d\d[.\-]\d\d[.\-]\d\d")


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""

    captured = []

    with sync_playwright() as p:
        browser, context, page = new_authenticated_context(
            p, partner_id, partner_pw, mss_mac, totp_secret
        )

        new_pages = []
        context.on("page", lambda pg: new_pages.append(pg))

        def on_request(req):
            if "musinsa.com" in req.url and req.method in ("GET", "POST"):
                if any(k in req.url.lower() for k in ("ord0", "order", "goods", "style")):
                    captured.append(f"{req.method} {req.url}")

        page.on("request", on_request)

        page.goto(ORDER_DAILY_PAGE, wait_until="networkidle")
        page.wait_for_timeout(3000)

        bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)
        bizest_frame.on("request", on_request)

        bizest_frame.evaluate("app_ord07_grid.search_list()")
        page.wait_for_timeout(2000)

        rows = bizest_frame.locator("table tr")
        n = rows.count()
        print("TABLE_TR_COUNT:", n)
        date_row_idx = None
        for i in range(n):
            row = rows.nth(i)
            txt = row.inner_text().replace("\n", " | ")
            print(f"ROW[{i}]:", txt[:150])
            if date_row_idx is None and DATE_RE.search(txt):
                date_row_idx = i

        if date_row_idx is not None:
            print("DATE_ROW_FOUND_AT:", date_row_idx)
            # 그 행 안에서 클릭 가능한(날짜) 셀을 우선 클릭
            row = rows.nth(date_row_idx)
            cells = row.locator("td")
            clicked = False
            for j in range(cells.count()):
                cell = cells.nth(j)
                ctxt = cell.inner_text()
                if DATE_RE.search(ctxt):
                    print(f"CLICKING_CELL[{j}]:", ctxt)
                    try:
                        cell.click(timeout=3000)
                        clicked = True
                    except Exception as e:
                        print("CELL_CLICK_ERROR:", e)
                    break
            if not clicked:
                print("FALLBACK_CLICK_WHOLE_ROW")
                row.click(timeout=3000)
            page.wait_for_timeout(3000)
        else:
            print("NO_DATE_ROW_FOUND")

        print("NEW_PAGES_OPENED:", len(new_pages))
        for np_ in new_pages:
            print("  new page url:", np_.url)
            try:
                np_.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            for fr in np_.frames:
                print("    frame:", fr.url)

        print("CAPTURED_REQUESTS_AFTER_CLICK:")
        for u in sorted(set(captured))[-40:]:
            print(" -", u)

        print("ALL_FRAME_URLS_MAIN_PAGE:")
        for fr in page.frames:
            print(" -", fr.url)

        browser.close()


if __name__ == "__main__":
    main()
