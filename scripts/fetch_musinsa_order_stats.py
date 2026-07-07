"""무신사 파트너센터 일별 주문 통계(주문건수/매출) 수집.

'일별 주문 통계' 화면은 bizest.musinsa.com이라는 별도 레거시 시스템을
iframe으로 임베드해서 보여준다. 같은 로그인 세션의 쿠키가 그대로
통하므로, Playwright로 그 iframe(frame) 컨텍스트 안에서 직접
get_chart API를 호출한다.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_DAILY_PAGE = "https://partner.musinsa.com/statistics/order-daily"
GET_CHART_URL = "https://bizest.musinsa.com/po/order-group-admin/order/ord07/get_chart"
OUT_PATH = "data-b53e82ab173f/musinsa_order_stats.json"
DAYS = 90


def fetch_order_stats(page, days=DAYS):
    page.goto(ORDER_DAILY_PAGE, wait_until="networkidle")
    page.wait_for_timeout(3000)
    bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "DATA": "", "AC_ID": "", "CMD": "", "ISLD": "",
        "MENU_ID": "/po/order-group-admin/order/ord07",
        "USR_SEARCH_ITEM_CNT": "0", "PAGE_CNT": "10", "LIMIT": "100", "PAGE": "1",
        "LIST_DATA": "",
        "S_SDATE": start.strftime("%Y-%m-%d"),
        "S_EDATE": end.strftime("%Y-%m-%d"),
        "S_STYLE_NO": "", "S_STYLE_NOS": "", "S_GOODS_NO": "", "S_GOODS_SUB": "",
        "S_GOODS_NM": "", "S_OPT_KIND_CD": "", "S_BRAND_NM": "", "S_BRAND_CD": "",
        "S_SALE_PLACE": "", "S_MD_ID": "",
    }
    url = f"{GET_CHART_URL}?{urlencode(params)}"

    result = bizest_frame.evaluate(
        """async (url) => {
            const r = await fetch(url, { credentials: 'include' });
            return { status: r.status, text: await r.text() };
        }""",
        url,
    )
    if result["status"] != 200:
        raise RuntimeError(f"HTTP {result['status']}: {result['text'][:500]}")
    return json.loads(result["text"])


def extract_items(raw_list):
    items = []
    for row in raw_list:
        ord_date = row.get("ord_date", "")
        date_iso = ord_date.replace(".", "-") if ord_date else ""
        items.append({
            "date": date_iso,
            "qtyAll": int(row.get("qty_all") or 0),
            "priceAll": int(row.get("price_all") or 0),
            "qtySale": int(row.get("qty_sale") or 0),
            "priceSale": int(row.get("price_sale") or 0),
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
                raw_list = fetch_order_stats(page)
                browser.close()
            items = extract_items(raw_list)
        except Exception as e:
            print(f"Failed to fetch order stats: {e}")
    else:
        print("MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set, skipping")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} order stat days to {OUT_PATH}")


if __name__ == "__main__":
    main()
