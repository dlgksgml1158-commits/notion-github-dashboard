"""임시 진단 스크립트: 일별 행을 클릭했을 때 열리는 상품별 주문 목록 팝업을 찾는다."""
import json
import os

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_DAILY_PAGE = "https://partner.musinsa.com/statistics/order-daily"


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

        # 그리드의 실제 DOM 행을 찾아 클릭 (open_order_list 트리거 목적)
        try:
            rows = bizest_frame.locator("table tr, .ag-row, [class*='row']")
            print("ROW_COUNT_CANDIDATES:", rows.count())
            clicked = False
            for i in range(min(rows.count(), 40)):
                row = rows.nth(i)
                txt = row.inner_text() if row.count() else ""
                if "2026" in txt or "." in txt:
                    print(f"CLICKING_ROW[{i}]:", txt[:80].replace(chr(10), ' | '))
                    row.click(timeout=3000)
                    clicked = True
                    page.wait_for_timeout(2500)
                    break
            if not clicked:
                print("NO_ROW_CLICKED")
        except Exception as e:
            print("ROW_CLICK_ERROR:", e)

        print("NEW_PAGES_OPENED:", len(new_pages))
        for np_ in new_pages:
            print("  new page url:", np_.url)

        print("CAPTURED_REQUESTS_AFTER_CLICK:")
        for u in sorted(set(captured))[-40:]:
            print(" -", u)

        # 팝업/모달이 같은 페이지 내 iframe으로 열렸을 가능성도 체크
        print("ALL_FRAME_URLS:")
        for fr in page.frames:
            print(" -", fr.url)

        browser.close()


if __name__ == "__main__":
    main()
