"""무신사 파트너센터 '쿠폰 마케팅 > 프로모션 쿠폰 신청' 목록 수집.

기존 fetch_musinsa_partner_promo.py와 동일한 Playwright 로그인 세션을
재사용해 api.musinsa.com의 쿠폰 프로모션 검색 API를 직접 호출한다.
셀러별 참여 카운트(appliedBrandCount, cnt)는 계정별 정보일 수 있어
기존 fetch_musinsa_partner_promo.py와 동일한 원칙으로 제외하고,
모든 파트너에게 공통인 쿠폰 일정/할인 정보만 저장한다.
"""
import json
import os
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

COUPON_PAGE = "https://partner.musinsa.com/growth/promotion/partner/coupon/promotion"
API_URL = "https://api.musinsa.com/api2/coupon/po/promotion-coupons/search"
OUT_PATH = "data-b53e82ab173f/musinsa_coupon_promotions.json"
MONTHS_AHEAD = 3


def _month_str(dt):
    return dt.strftime("%Y%m")


def _add_months(dt, months):
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    return dt.replace(year=year, month=month, day=1)


def fetch_coupon_promotions(page):
    # 같은 오리진에서 쿠키가 자연스럽게 실리도록 목록 화면을 먼저 방문한다.
    page.goto(COUPON_PAGE, wait_until="networkidle")
    page.wait_for_timeout(1000)

    now = datetime.now(timezone.utc)
    publish_from = _month_str(now)
    publish_to = _month_str(_add_months(now, MONTHS_AHEAD))

    all_items = []
    fetch_page = 1
    while True:
        params = {
            "pageable.page": str(fetch_page),
            "pageable.limit": "100",
            "title": "",
            "status": "0",
            "type": "ALL",
            "publishFrom": publish_from,
            "publishTo": publish_to,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        result = page.evaluate(
            """async (url) => {
                const r = await fetch(url, { credentials: 'include' });
                return { status: r.status, body: await r.text() };
            }""",
            f"{API_URL}?{query}",
        )
        if result["status"] != 200:
            raise RuntimeError(f"HTTP {result['status']}: {result['body'][:500]}")

        payload = json.loads(result["body"])
        all_items.extend(payload.get("data", []))

        pagination = payload.get("pagination", {})
        total_pages = pagination.get("totalPages", 1)
        if fetch_page >= total_pages:
            break
        fetch_page += 1

    return all_items


def extract_items(raw_items):
    items = []
    for c in raw_items:
        items.append({
            "id": c.get("id"),
            "title": c.get("title", ""),
            "status": c.get("status", ""),
            "type": c.get("type", ""),
            "typeName": c.get("typeName", ""),
            "couponDcType": c.get("couponDcType"),
            "couponDcPercent": c.get("couponDcPercent"),
            "couponDcPrice": c.get("couponDcPrice"),
            "couponData": c.get("couponData", ""),
            "rangeFrom": c.get("rangeFrom", ""),
            "rangeTo": c.get("rangeTo", ""),
            "rangeDate": c.get("rangeDate", ""),
            "publishFrom": c.get("publishFrom", ""),
            "publishTo": c.get("publishTo", ""),
            "publishDate": c.get("publishDate", ""),
            "expiredFrom": c.get("expiredFrom", ""),
            "expiredTo": c.get("expiredTo", ""),
            "usedDate": c.get("usedDate", ""),
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
            with sync_playwright() as p:
                browser, context, page = new_authenticated_context(
                    p, partner_id, partner_pw, mss_mac, totp_secret
                )
                raw_items = fetch_coupon_promotions(page)
                browser.close()
            items = extract_items(raw_items)
        except Exception as e:
            print(f"Failed to fetch coupon promotions: {e}")
    else:
        print("MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set, skipping")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} coupon promotion items to {OUT_PATH}")


if __name__ == "__main__":
    main()
