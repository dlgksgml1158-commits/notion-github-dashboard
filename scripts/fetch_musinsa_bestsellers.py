"""무신사 파트너센터 주문 내역(ord01)에서 상품별 판매 수량을 전체 기간 및
일자별로 집계한다.

'주문/배송' 목록 화면(/order/history)이 사용하는
bizest.musinsa.com/po/order-group-admin/api/order/ord01/search POST API를
직접 호출한다. LIMIT을 매우 크게 주면 페이지네이션 없이 한 번에 전체
데이터를 받을 수 있음을 확인했다(실측: 90일치 2977건이 단일 응답으로 옴).
원본 요청 바디를 그대로 재사용하고 S_SDATE/S_EDATE/S_ORD_STATE/LIMIT/PAGE만
치환하는 방식이라 S_LOGISTICS_BUSINESS_TYPES[] 같은 배열 파라미터도 그대로
보존된다.

S_ORD_STATE는 "현재 정확히 이 상태인 주문"만 돌려주는 스냅샷 필터라(예:
40=배송완료로 조회하면 이미 50=구매확정으로 넘어간 주문은 더 이상 잡히지
않음), 구매확정(50) 단독으로만 조회하면 매우 안정적이지만 확정까지 걸리는
기간(체감상 ~8일)만큼 최신 날짜가 항상 비어 있게 된다. 그래서 배송완료(40)
와 구매확정(50) 두 상태를 모두 조회해 날짜별로 합치되, 구매확정 데이터가
있으면 그걸 우선하고(더 확정적) 없는 최근 날짜만 배송완료 데이터로 채운다.
이렇게 하면 최근 날짜는 배송완료 기준 잠정치로라도 매일 채워지고, 시간이
지나 구매확정 데이터가 생기면 다음 실행에서 자연히 더 정확한 값으로
덮어써진다.

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

# S_ORD_STATE 코드 (order/history 페이지의 상태 select에서 확인):
# 10=결제완료 20=상품준비중 30=배송시작 35=배송중 40=배송완료 50=구매확정
CONFIRMED_STATE = "50"  # 구매확정 — 최종 확정, 이후 절대 안 바뀜(가장 안정적)
DELIVERED_STATE = "40"  # 배송완료 — 아직 확정 전이지만 지연이 훨씬 짧음(최신성 채움용)


def capture_request_template(page):
    """order/history 페이지 로드 시 나가는 최초 ord01/search 요청 바디를
    캡처해 이후 상태값만 바꿔가며 재사용할 수 있게 한다."""
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

    return bizest_frame, captured_body["raw"]


def search_orders(bizest_frame, template_body, ord_state, days=DAYS):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    body = template_body
    body = re.sub(r"S_SDATE=[^&]*", f"S_SDATE={start_str}", body)
    body = re.sub(r"S_EDATE=[^&]*", f"S_EDATE={end_str}", body)
    body = re.sub(r"LIMIT=\d+", f"LIMIT={FETCH_LIMIT}", body)
    body = re.sub(r"PAGE=\d+", "PAGE=1", body)
    body = re.sub(r"S_ORD_STATE=[^&]*", f"S_ORD_STATE={ord_state}", body)

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
    print(f"  [S_ORD_STATE={ord_state}] 총 {total}건 중 {len(rows)}건 수신 ({start_str} ~ {end_str})")
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
                bizest_frame, template = capture_request_template(page)
                confirmed_rows, _, start_date, end_date = search_orders(
                    bizest_frame, template, CONFIRMED_STATE
                )
                delivered_rows, _, _, _ = search_orders(
                    bizest_frame, template, DELIVERED_STATE
                )
                browser.close()

            items = aggregate_bestsellers(confirmed_rows)
            confirmed_by_date = aggregate_by_date(confirmed_rows)
            delivered_by_date = aggregate_by_date(delivered_rows)
            # 구매확정 데이터가 있는 날짜는 그걸 우선하고, 아직 확정 전이라
            # 구매확정 쪽에 없는 최근 날짜만 배송완료 데이터로 채운다.
            by_date = {**delivered_by_date, **confirmed_by_date}
        except Exception as e:
            print(f"Failed to fetch bestsellers: {e}")
    else:
        print("MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set, skipping")

    # 로그인/스크래핑이 일시적으로 실패해 빈 데이터가 나오면, 기존에 쌓아둔
    # 데이터를 빈 값으로 덮어써서 날려버리지 않도록 이전 파일을 그대로 유지한다.
    if not items and os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("items"):
                print("Fetched 0 items; keeping previous bestsellers")
                return
        except Exception:
            pass

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
