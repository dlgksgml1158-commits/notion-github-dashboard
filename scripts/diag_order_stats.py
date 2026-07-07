"""일별 주문 통계(iframe 내부 API) 발견용 1회성 진단 스크립트."""
import os

import pyotp
from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import resolve_totp_secret

LOGIN_URL = (
    "https://partner-sso.one.musinsa.com/oauth/login"
    "?clientId=MUSINSA_PARTNER&platform=mss&redirectUri=https%3A%2F%2Fpartner.musinsa.com"
)


def main():
    partner_id = os.environ["MUSINSA_PARTNER_ID"]
    partner_pw = os.environ["MUSINSA_PARTNER_PW"]
    totp_secret = resolve_totp_secret(os.environ["MUSINSA_PARTNER_TOTP_SECRET"])

    seen_urls = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()

        def on_request(req):
            seen_urls.append(req.url)

        page.on("request", on_request)

        page.goto(LOGIN_URL, wait_until="networkidle")
        page.wait_for_selector('input[name="id"]', timeout=15000)
        page.fill('input[name="id"]', partner_id)
        page.fill('input[name="password"]', partner_pw)
        page.click('button[type="submit"]')

        try:
            page.wait_for_url("https://partner.musinsa.com/**", timeout=8000)
        except Exception:
            otp_input = page.locator('input[name="code"]')
            if otp_input.count() > 0:
                radios = page.locator('input[type="radio"]')
                if radios.count() > 0:
                    radios.first.check()
                code = pyotp.TOTP(totp_secret).now()
                otp_input.fill(code)
                page.click('button[type="submit"]:has-text("인증하기")')
                page.wait_for_url("https://partner.musinsa.com/**", timeout=20000)

        page.wait_for_load_state("networkidle")

        # 진단 대상 페이지로 이동
        seen_urls.clear()
        page.goto("https://partner.musinsa.com/statistics/order-daily", wait_until="networkidle")
        page.wait_for_timeout(3000)

        print("=== ALL REQUESTS AFTER NAVIGATING TO order-daily ===")
        for u in seen_urls:
            print(u)

        browser.close()


if __name__ == "__main__":
    main()
