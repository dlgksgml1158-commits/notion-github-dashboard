"""임시 진단 스크립트: 일별 주문 통계 화면에서 상품별(개별 주문 라인) 데이터를
제공하는 API가 있는지 탐색한다. 완료 후 삭제 예정."""
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

    search_requests = []

    with sync_playwright() as p:
        browser, context, page = new_authenticated_context(
            p, partner_id, partner_pw, mss_mac, totp_secret
        )

        def on_request(req):
            if "/ord07/search" in req.url:
                search_requests.append({
                    "url": req.url,
                    "method": req.method,
                    "post_data": req.post_data,
                })

        page.on("request", on_request)

        page.goto(ORDER_DAILY_PAGE, wait_until="networkidle")
        page.wait_for_timeout(3000)

        bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)

        # search_list()가 만든 search/ 요청 캡처
        bizest_frame.evaluate("app_ord07_grid.search_list()")
        page.wait_for_timeout(3000)

        print("SEARCH_REQUESTS:", json.dumps(search_requests, ensure_ascii=False))

        # open_order_list 함수 시그니처/동작 확인 (인자 없이 호출 시 에러 메시지로 힌트 추정)
        try:
            open_list_result = bizest_frame.evaluate(
                """() => {
                    try {
                        return { fnString: window.app_ord07_grid.open_order_list.toString().slice(0, 1500) };
                    } catch (e) { return { error: String(e) }; }
                }"""
            )
            print("OPEN_ORDER_LIST_FN:", json.dumps(open_list_result, ensure_ascii=False))
        except Exception as e:
            print("OPEN_ORDER_LIST_FN_ERROR:", e)

        # search_list 함수 소스도 같이 덤프 (search/ 요청 URL 패턴/파라미터 이해용)
        try:
            search_list_src = bizest_frame.evaluate(
                """() => {
                    try {
                        return { fnString: window.app_ord07_grid.search_list.toString().slice(0, 2000) };
                    } catch (e) { return { error: String(e) }; }
                }"""
            )
            print("SEARCH_LIST_FN:", json.dumps(search_list_src, ensure_ascii=False))
        except Exception as e:
            print("SEARCH_LIST_FN_ERROR:", e)

        # 직접 재요청해서 응답 바디까지 확인 (최근 캡처된 search 요청 재현)
        if search_requests:
            last = search_requests[-1]
            fetch_result = bizest_frame.evaluate(
                """async ({url, method, postData}) => {
                    const opts = { method, credentials: 'include' };
                    if (postData) {
                        opts.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
                        opts.body = postData;
                    }
                    const r = await fetch(url, opts);
                    const text = await r.text();
                    return { status: r.status, text: text.slice(0, 3000) };
                }""",
                {"url": last["url"], "method": last["method"], "postData": last.get("post_data")},
            )
            print("SEARCH_RESPONSE_SAMPLE:", json.dumps(fetch_result, ensure_ascii=False))

        browser.close()


if __name__ == "__main__":
    main()
