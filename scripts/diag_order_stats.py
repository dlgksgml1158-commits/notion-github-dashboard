"""일별 주문 통계(iframe 내부 API) 발견용 1회성 진단 스크립트."""
import os
from playwright.sync_api import sync_playwright

LOGIN_URL = (
    "https://partner-sso.one.musinsa.com/oauth/login"
    "?clientId=MUSINSA_PARTNER&platform=mss&redirectUri=https%3A%2F%2Fpartner.musinsa.com"
)


def main():
    partner_id = os.environ["MUSINSA_PARTNER_ID"]
    partner_pw = os.environ["MUSINSA_PARTNER_PW"]

    import pyotp
    import json
    import base64
    from urllib.parse import parse_qs, urlparse

    def _read_varint(data, pos):
        result = 0
        shift = 0
        while True:
            b = data[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        return result, pos

    def _parse_protobuf(data):
        fields = {}
        pos = 0
        while pos < len(data):
            tag, pos = _read_varint(data, pos)
            field_num, wire_type = tag >> 3, tag & 0x7
            if wire_type == 0:
                value, pos = _read_varint(data, pos)
            elif wire_type == 2:
                length, pos = _read_varint(data, pos)
                value = data[pos:pos + length]
                pos += length
            elif wire_type == 5:
                value, pos = data[pos:pos + 4], pos + 4
            elif wire_type == 1:
                value, pos = data[pos:pos + 8], pos + 8
            else:
                raise ValueError(f"bad wire type {wire_type}")
            fields.setdefault(field_num, []).append(value)
        return fields

    def decode_migration_uri(uri):
        qs = parse_qs(urlparse(uri).query)
        raw = base64.b64decode(qs["data"][0])
        top = _parse_protobuf(raw)
        entries = []
        for otp_bytes in top.get(1, []):
            sub = _parse_protobuf(otp_bytes)
            secret_bytes = sub.get(1, [b""])[0]
            name = sub.get(2, [b""])[0].decode("utf-8", errors="replace")
            issuer = sub.get(3, [b""])[0].decode("utf-8", errors="replace")
            secret_b32 = base64.b32encode(secret_bytes).decode("ascii").rstrip("=")
            entries.append({"name": name, "issuer": issuer, "secret": secret_b32})
        return entries

    totp_raw = os.environ["MUSINSA_PARTNER_TOTP_SECRET"].strip()
    if totp_raw.startswith("otpauth-migration://"):
        entries = decode_migration_uri(totp_raw)
        totp_secret = entries[0]["secret"]
    else:
        totp_secret = totp_raw

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
