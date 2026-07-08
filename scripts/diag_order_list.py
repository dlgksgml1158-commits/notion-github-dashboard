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

    seen = []

    with sync_playwright() as p:
        browser, context, page = new_authenticated_context(
            p, partner_id, partner_pw, mss_mac, totp_secret
        )

        def on_request(req):
            if "bizest.musinsa.com" in req.url or "musinsa.com" in req.url:
                if req.method in ("GET", "POST") and (
                    "order" in req.url.lower() or "ord0" in req.url.lower()
                ):
                    seen.append(req.url)

        page.on("request", on_request)

        page.goto(ORDER_DAILY_PAGE, wait_until="networkidle")
        page.wait_for_timeout(3000)

        bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)

        # 1) 그리드 객체 자체를 덤프해서 사용 가능한 함수/데이터 소스 이름 확인
        grid_info = bizest_frame.evaluate(
            """() => {
                try {
                    const g = window.app_ord07_grid;
                    if (!g) return { error: 'no app_ord07_grid' };
                    const keys = Object.keys(g).filter(k => typeof g[k] === 'function');
                    return { keys };
                } catch (e) { return { error: String(e) }; }
            }"""
        )
        print("GRID_FUNCTION_KEYS:", json.dumps(grid_info, ensure_ascii=False))

        # 2) 실제 검색 트리거
        bizest_frame.evaluate("app_ord07_grid.search_list()")
        page.wait_for_timeout(3000)

        print("CAPTURED_ORDER_URLS:")
        for u in sorted(set(seen)):
            print(" -", u)

        # 3) 그리드 내부 rowData(있다면) 샘플 1건 덤프 -> 상품명 필드 존재 여부 확인
        row_sample = bizest_frame.evaluate(
            """() => {
                try {
                    const g = window.app_ord07_grid;
                    const data = g && (g.gridOptions?.api?.getDisplayedRowAtIndex?.(0)?.data
                        || g.rowData?.[0] || g.list?.[0] || g.data?.[0]);
                    return data || null;
                } catch (e) { return { error: String(e) }; }
            }"""
        )
        print("ROW_SAMPLE:", json.dumps(row_sample, ensure_ascii=False))

        browser.close()


if __name__ == "__main__":
    main()
