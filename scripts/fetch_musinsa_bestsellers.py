"""무신사 파트너센터 구매확정 주문 내역(ord01)에서 상품별 판매 수량을
전체 기간 및 일자별로 집계한다.

'주문/배송 > 구매확정' 목록 화면(/order/history?S_ORD_STATE=50)이 사용하는
bizest.musinsa.com/po/order-group-admin/api/order/ord01/search POST API를
직접 호출한다. LIMIT을 매우 크게 주면 페이지네이션 없이 한 번에 전체
데이터를 받을 수 있음을 확인했다(실측: 90일치 2977건이 단일 응답으로 옴).
원본 요청 바디를 그대로 재사용하고 S_SDATE/S_EDATE/LIMIT/PAGE만 치환하는
방식이라 S_LOGISTICS_BUSINESS_TYPES[] 같은 배열 파라미터도 그대로 보존된다.

각 주문 행이 날짜(ord_date)/상품명(goods_nm)/수량(qty)을 포함하므로,
'주문 현황' 차트에서 날짜를 클릭했을 때 그날 판매된 상품을 보여줄 수 있게
날짜별 집계(byDate)도 함께 만든다.
"""
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_HISTORY_PAGE = "https://partner.musinsa.com/order/history?S_ORD_STATE=50&summary_info=Y"
SEARCH_URL = "https://bizest.musinsa.com/po/order-group-admin/api/order/ord01/search"
OUT_PATH = "data-b53e82ab173f/musinsa_bestsellers.json"
DAYS = 90
FETCH_LIMIT = 5000
TOP_N_OVERALL = 30
TOP_N_PER_DAY = 15


def fetch_all_orders(page):
    captured_body = {"raw": None}

    def on_request(req):
        if "ord01/search" in req.url and req.post_data and not captured_body["raw"]:
            captured_body["raw"] = req.post_data

    page.on("request", on_request)
    page.goto(ORDER_HISTORY_PAGE, wait_until="networkidle")
    page.wait_for_timeout(3000)

    bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)

    if not captured_body["raw"]:
        raise RuntimeError("초기 ord01/search 요청을 캡처하지 못했습니다")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    body = captured_body["raw"]
    body = re.sub(r"S_SDATE=[^&]*", f"S_SDATE={start_str}", body)
    body = re.sub(r"S_EDATE=[^&]*", f"S_EDATE={end_str}", body)
    body = re.sub(r"LIMIT=\d+", f"LIMIT={FETCH_LIMIT}", body)
    body = re.sub(r"PAGE=\d+", "PAGE=1", body)

    result = bizest_frame.evaluate(
        """async ({url, body}) => {
            const r = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body,
            });
            const text = await r.text();
            return { status: r.status, text };
        }""",
        {"url": SEARCH_URL, "body": body},
    )
    if result["status"] != 200:
        raise RuntimeError(f"HTTP {result['status']}: {result['text'][:500]}")

    parsed = json.loads(result["text"])
    total = int(parsed.get("total", 0))
    rows = parsed.get("data", [])
    print(f"  총 {total}건 중 {len(rows)}건 수신 ({start_str} ~ {end_str})")
    if len(rows) < total:
        print(f"  [WARN] FETCH_LIMIT({FETCH_LIMIT})이 총 건수보다 작습니다 — 일부 누락 가능")

    return rows, total, start_str, end_str


def _agg_key(r):
    return r.get("goods_no") or r.get("style_no") or r.get("goods_nm")


def _new_entry():
    return {"goodsNo": "", "name": "", "styleNo": "", "qty": 0, "salesAmt": 0}


def _rank(agg, top_n):
    ranked = sorted(agg.values(), key=lambda x: x["qty"], reverse=True)[:top_n]
    return [{"rank": i + 1, **item} for i, item in enumerate(ranked)]


def aggregate_bestsellers(rows, top_n=TOP_N_OVERALL):
    agg = defaultdict(_new_entry)
    for r in rows:
        key = _agg_key(r)
        if not key:
            continue
        entry = agg[key]
        entry["goodsNo"] = r.get("goods_no", "")
        entry["name"] = r.get("goods_nm", "")
        entry["styleNo"] = r.get("style_no", "")
        entry["qty"] += int(r.get("qty") or 0)
        entry["salesAmt"] += int(r.get("sales_amt") or 0)
    return _rank(agg, top_n)


def aggregate_by_date(rows, top_n=TOP_N_PER_DAY):
    by_date = defaultdict(lambda: defaultdict(_new_entry))
    for r in rows:
        key = _agg_key(r)
        if not key:
            continue
        raw_date = (r.get("ord_date") or "")[:10]  # "2026.07.05 08:33:23" -> "2026.07.05"
        if not raw_date:
            continue
        date_iso = raw_date.replace(".", "-")
        entry = by_date[date_iso][key]
        entry["goodsNo"] = r.get("goods_no", "")
        entry["name"] = r.get("goods_nm", "")
        entry["styleNo"] = r.get("style_no", "")
        entry["qty"] += int(r.get("qty") or 0)
        entry["salesAmt"] += int(r.get("sales_amt") or 0)

    return {date: _rank(agg, top_n) for date, agg in by_date.items()}


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    items = []
    by_date = {}
    start_date = end_date = ""
    if partner_id and partner_pw:
        try:
            totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""
            with sync_playwright() as p:
                browser, context, page = new_authenticated_context(
                    p, partner_id, partner_pw, mss_mac, totp_secret
                )
                rows, total, start_date, end_date = fetch_all_orders(page)
                browser.close()
            print(f"수집 완료: {len(rows)}/{total}건")
            items = aggregate_bestsellers(rows)
            by_date = aggregate_by_date(rows)
        except Exception as e:
            print(f"Failed to fetch bestsellers: {e}")
    else:
        print("MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set, skipping")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "startDate": start_date,
        "endDate": end_date,
        "items": items,
        "byDate": by_date,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} bestseller items + {len(by_date)} days to {OUT_PATH}")


if __name__ == "__main__":
    main()
