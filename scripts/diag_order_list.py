"""임시 진단 스크립트: /order/history 페이지(개별 주문 내역)의 구조와
API를 확인해서 상품별 주문 데이터를 뽑을 수 있는지 확인한다."""
import json
import os

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_HISTORY_PAGE = "https://partner.musinsa.com/order/history?S_ORD_STATE=50&summary_info=Y"


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

        def on_request(req):
            if "musinsa.com" in req.url and req.method in ("GET", "POST"):
                if any(k in req.url.lower() for k in ("order", "goods", "style", "history")):
                    captured.append(f"{req.method} {req.url}" + (f" | BODY: {req.post_data}" if req.post_data else ""))

        page.on("request", on_request)

        page.goto(ORDER_HISTORY_PAGE, wait_until="networkidle")
        page.wait_for_timeout(4000)

        print("PAGE_URL_AFTER_LOAD:", page.url)
        print("FRAMES:")
        for fr in page.frames:
            print(" -", fr.url)
            fr.on("request", on_request)

        # 페이지 안의 텍스트 일부 덤프 (테이블 헤더/상품명 존재 확인용)
        body_text = page.inner_text("body")
        print("BODY_TEXT_SNIPPET:", body_text[:1500].replace("\n", " | "))

        # 새로고침 트리거해서 목록 API 재요청 유도
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(3000)

        print("CAPTURED_REQUESTS:")
        for u in sorted(set(captured))[:60]:
            print(" -", u[:300])

        browser.close()


if __name__ == "__main__":
    main()
