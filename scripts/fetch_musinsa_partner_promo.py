"""무신사 파트너센터 프로모션 할인 일정 수집 (Playwright 실제 로그인).

비밀번호 기반 정적 쿠키는 IP/세션 바인딩으로 재사용이 안 되기 때문에,
매 실행마다 Playwright로 실제 로그인을 수행해 그 실행 시점에 유효한
세션을 새로 발급받는다.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pyotp
from playwright.sync_api import sync_playwright


def resolve_totp_secret(raw):
    """MUSINSA_PARTNER_TOTP_SECRET 값에서 실제 TOTP 비밀키를 추출한다.

    Authenticator 확장 프로그램의 "전체 export" 형식(JSON 배열), 단일
    otpauth:// URI, 순수 base32 비밀키를 모두 지원한다.
    """
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        for entry in data:
            if not isinstance(entry, dict):
                continue
            label = " ".join(str(entry.get(k, "")) for k in ("issuer", "label", "name", "account")).lower()
            if "musinsa" in label or "partner" in label:
                secret = entry.get("secret") or entry.get("key")
                if secret:
                    return secret
        raise RuntimeError(
            f"MUSINSA_PARTNER_TOTP_SECRET is a JSON list ({len(data)} entries) "
            "but none matched 'musinsa'/'partner' in issuer/label/name/account"
        )
    if isinstance(data, dict):
        secret = data.get("secret") or data.get("key")
        if secret:
            return secret

    otp_uris = [line.strip() for line in raw.splitlines() if line.strip().startswith("otpauth://")]
    for uri in otp_uris:
        if "musinsa" in uri.lower() or len(otp_uris) == 1:
            m = re.search(r"[?&]secret=([A-Za-z2-7]+)", uri)
            if m:
                return m.group(1)

    compact = raw.replace(" ", "").replace("\n", "")
    if re.fullmatch(r"[A-Za-z2-7]+=*", compact):
        return compact

    raise RuntimeError("Could not parse TOTP secret from MUSINSA_PARTNER_TOTP_SECRET (unrecognized format)")

LOGIN_URL = (
    "https://partner-sso.one.musinsa.com/oauth/login"
    "?clientId=MUSINSA_PARTNER&platform=mss&redirectUri=https%3A%2F%2Fpartner.musinsa.com"
)
API_URL = "https://itgg-api.musinsa.com/po/sale/promotions"
OUT_PATH = "data-b53e82ab173f/musinsa_partner_promotions.json"


def fetch_promotions(partner_id, partner_pw, mss_mac, totp_secret):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ))
        if mss_mac:
            # "신뢰된 기기" 쿠키를 미리 심어서 매 실행마다 낯선 기기로 인식되어
            # 2차 인증(OTP)이 뜨는 것을 방지 시도.
            for domain in (".musinsa.com", "partner-sso.one.musinsa.com", "partner.musinsa.com"):
                context.add_cookies([{
                    "name": "mss_mac",
                    "value": mss_mac,
                    "domain": domain,
                    "path": "/",
                }])
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.wait_for_selector('input[name="id"]', timeout=15000)
        page.fill('input[name="id"]', partner_id)
        page.fill('input[name="password"]', partner_pw)
        page.click('button[type="submit"]')

        try:
            page.wait_for_url("https://partner.musinsa.com/**", timeout=8000)
        except Exception:
            otp_input = page.locator('input[name="code"]')
            if not totp_secret or otp_input.count() == 0:
                current_url = page.url
                body_text = page.inner_text("body")[:800]
                raise RuntimeError(
                    f"Login did not redirect and no OTP input found or no TOTP secret set. "
                    f"current_url={current_url} body_snippet={body_text!r}"
                ) from None

            # "OTP" 라디오(첫 번째 옵션)를 선택해 앱 기반 OTP로 인증
            radios = page.locator('input[type="radio"]')
            if radios.count() > 0:
                radios.first.check()

            code = pyotp.TOTP(totp_secret).now()
            otp_input.fill(code)
            page.click('button[type="submit"]:has-text("인증하기")')

            try:
                page.wait_for_url("https://partner.musinsa.com/**", timeout=20000)
            except Exception:
                current_url = page.url
                body_text = page.inner_text("body")[:800]
                raise RuntimeError(
                    f"Login did not redirect after OTP submission. "
                    f"current_url={current_url} body_snippet={body_text!r}"
                ) from None

        page.wait_for_load_state("networkidle")

        now = datetime.now(timezone.utc)
        params = {
            "rangeFrom": now.isoformat(),
            "rangeTo": (now + timedelta(days=60)).isoformat(),
            "page": "1",
            "limit": "100",
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        result = page.evaluate(
            """async (url) => {
                const r = await fetch(url, { credentials: 'include' });
                return { status: r.status, body: await r.text() };
            }""",
            f"{API_URL}?{query}",
        )
        browser.close()

    if result["status"] != 200:
        raise RuntimeError(f"HTTP {result['status']}: {result['body'][:500]}")
    return json.loads(result["body"])


def extract_items(payload):
    # 계정별 참여 현황(productCount 등)은 이 저장소가 public이라 절대 포함하지 않는다 —
    # 모든 셀러에게 공통인 프로모션 일정 정보만 저장한다.
    items = []
    for p in payload.get("data", {}).get("promotions", []):
        items.append({
            "promotionId": p.get("promotionId"),
            "name": p.get("promotionName", ""),
            "status": p.get("status", ""),
            "from": p.get("rangeFrom", ""),
            "to": p.get("rangeTo", ""),
            "minPercent": p.get("minPercent"),
            "recruitDeadline": p.get("recruitDeadline", ""),
        })
    return items


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    items = []
    if partner_id and partner_pw:
        try:
            totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""
            payload = fetch_promotions(partner_id, partner_pw, mss_mac, totp_secret)
            items = extract_items(payload)
        except Exception as e:
            print(f"Failed to fetch partner promotions: {e}")
    else:
        print("MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set, skipping")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} partner promotion items to {OUT_PATH}")


if __name__ == "__main__":
    main()
