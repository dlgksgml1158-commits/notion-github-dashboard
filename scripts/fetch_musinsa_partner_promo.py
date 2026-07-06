"""무신사 파트너센터 프로모션 할인 일정 수집 (Playwright 실제 로그인).

비밀번호 기반 정적 쿠키는 IP/세션 바인딩으로 재사용이 안 되기 때문에,
매 실행마다 Playwright로 실제 로그인을 수행해 그 실행 시점에 유효한
세션을 새로 발급받는다.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

LOGIN_URL = (
    "https://partner-sso.one.musinsa.com/oauth/login"
    "?clientId=MUSINSA_PARTNER&platform=mss&redirectUri=https%3A%2F%2Fpartner.musinsa.com"
)
API_URL = "https://itgg-api.musinsa.com/po/sale/promotions"
OUT_PATH = "data-b53e82ab173f/musinsa_partner_promotions.json"


def fetch_promotions(partner_id, partner_pw):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(LOGIN_URL, wait_until="networkidle")
        page.fill('input[name="id"]', partner_id)
        page.fill('input[name="password"]', partner_pw)
        page.click('button[type="submit"]')
        page.wait_for_url("https://partner.musinsa.com/**", timeout=20000)
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
    items = []
    if partner_id and partner_pw:
        try:
            payload = fetch_promotions(partner_id, partner_pw)
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
