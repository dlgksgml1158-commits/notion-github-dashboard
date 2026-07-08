"""임시 진단 스크립트: ord01/search API의 실제 응답(상품명/수량 포함 여부)을 확인한다."""
import json
import os

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_HISTORY_PAGE = "https://partner.musinsa.com/order/history?S_ORD_STATE=50&summary_info=Y"
SEARCH_URL = "https://bizest.musinsa.com/po/order-group-admin/api/order/ord01/search"


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""

    captured_body = {"data": None}

    with sync_playwright() as p:
        browser, context, page = new_authenticated_context(
            p, partner_id, partner_pw, mss_mac, totp_secret
        )

        def on_response(res):
            if "ord01/search" in res.url:
                try:
                    captured_body["data"] = res.text()
                except Exception as e:
                    captured_body["data"] = f"ERROR: {e}"

        page.on("response", on_response)

        page.goto(ORDER_HISTORY_PAGE, wait_until="networkidle")
        page.wait_for_timeout(4000)

        bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)
        bizest_frame.on("response", on_response)

        page.wait_for_timeout(2000)

        if captured_body["data"]:
            print("SEARCH_RESPONSE_LEN:", len(captured_body["data"]))
            print("SEARCH_RESPONSE_SAMPLE:", captured_body["data"][:4000])
        else:
            print("NO_RESPONSE_CAPTURED_YET - trying direct fetch")
            result = bizest_frame.evaluate(
                """async () => {
                    const params = new URLSearchParams({
                        MENU_ID: '/po/order-group-admin/order/ord01',
                        USR_SEARCH_ITEM_CNT: '8', PAGE_CNT: '10', LIMIT: '20', PAGE: '1',
                        SORT: 'desc', ORD_FIELD: 'a.ord_date', MFS_YN: 'N', LIST_DATA: '',
                        S_SDATE: '2026-06-08', S_EDATE: '2026-07-08',
                    });
                    const r = await fetch('https://bizest.musinsa.com/po/order-group-admin/api/order/ord01/search', {
                        method: 'POST',
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: params.toString(),
                    });
                    const text = await r.text();
                    return { status: r.status, text: text.slice(0, 4000) };
                }"""
            )
            print("DIRECT_FETCH_RESULT:", json.dumps(result, ensure_ascii=False))

        browser.close()


if __name__ == "__main__":
    main()
