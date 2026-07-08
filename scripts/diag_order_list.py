"""임시 진단 스크립트: 파트너센터 전체 메뉴 구조를 덤프해서 상품별
판매/주문 데이터를 제공할 만한 화면 후보를 찾는다."""
import json
import os

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

HOME_PAGE = "https://partner.musinsa.com/"


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""

    with sync_playwright() as p:
        browser, context, page = new_authenticated_context(
            p, partner_id, partner_pw, mss_mac, totp_secret
        )

        page.goto(HOME_PAGE, wait_until="networkidle")
        page.wait_for_timeout(3000)

        # 모든 <a> 링크의 텍스트+href 덤프 (사이드 메뉴 포함)
        links = page.evaluate(
            """() => {
                const as = Array.from(document.querySelectorAll('a[href]'));
                return as.map(a => ({
                    text: a.textContent.trim().replace(/\\s+/g, ' '),
                    href: a.getAttribute('href'),
                })).filter(l => l.text);
            }"""
        )
        print("ALL_LINKS_COUNT:", len(links))
        for l in links:
            print(f"  [{l['text']}] -> {l['href']}")

        # 통계/판매/정산/상품 관련 키워드로 필터링해서 한번 더 강조 출력
        keywords = ["통계", "판매", "정산", "상품", "매출", "주문", "베스트", "랭킹"]
        print("FILTERED_CANDIDATES:")
        for l in links:
            if any(k in l["text"] for k in keywords):
                print(f"  ★ [{l['text']}] -> {l['href']}")

        browser.close()


if __name__ == "__main__":
    main()
