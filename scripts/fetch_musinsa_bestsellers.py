"""무신사 파트너센터 구매확정 주문 내역(ord01)에서 상품별 판매 수량을 집계한다.

'주문/배송 > 구매확정' 목록 화면(/order/history?S_ORD_STATE=50)이 페이지네이션
되는 개별 주문 라인 데이터를 bizest.musinsa.com/po/order-group-admin/api/order/ord01/search
POST API로 받아온다. 각 행이 상품명(goods_nm)/수량(qty)을 포함하므로 이를
상품 단위로 합산해 베스트셀러 랭킹을 만든다.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_HISTORY_PAGE = "https://partner.musinsa.com/order/history?S_ORD_STATE=50&summary_info=Y"
OUT_PATH = "data-b53e82ab173f/musinsa_bestsellers.json"
TOP_N = 30
MAX_PAGES = 40


def fetch_all_orders(page):
    page.goto(ORDER_HISTORY_PAGE, wait_until="networkidle")

    responses = []

    def on_response(res):
        if "ord01/search" in res.url:
            try:
                body = json.loads(res.text())
                responses.append(body)
            except Exception as e:
                print(f"  [WARN] 응답 파싱 실패: {e}")

    page.on("response", on_response)
    page.wait_for_timeout(4000)

    bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)

    if not responses:
        raise RuntimeError("초기 ord01/search 응답을 받지 못했습니다")

    total = int(responses[0].get("total", 0))
    collected = list(responses[0].get("data", []))
    page_size = len(collected) or 1
    num_pages = -(-total // page_size)
    print(f"  총 {total}건, 페이지당 {page_size}건, 예상 {num_pages}페이지")

    for pnum in range(2, min(num_pages, MAX_PAGES) + 1):
        before = len(responses)
        try:
            bizest_frame.evaluate(f"app_ord01_grid.switch_page({pnum})")
        except Exception as e:
            print(f"  [WARN] switch_page({pnum}) 실패, 페이지네이션 중단: {e}")
            break
        page.wait_for_timeout(1500)
        if len(responses) > before:
            new_rows = responses[-1].get("data", [])
            collected.extend(new_rows)
            print(f"  페이지 {pnum}: {len(new_rows)}건 수집 (누적 {len(collected)}건)")
        else:
            print(f"  [WARN] 페이지 {pnum} 응답 없음, 중단")
            break

    return collected, total


def aggregate_bestsellers(rows, top_n=TOP_N):
    agg = defaultdict(lambda: {"goodsNo": "", "name": "", "styleNo": "", "qty": 0, "salesAmt": 0})
    for r in rows:
        key = r.get("goods_no") or r.get("style_no") or r.get("goods_nm")
        if not key:
            continue
        entry = agg[key]
        entry["goodsNo"] = r.get("goods_no", "")
        entry["name"] = r.get("goods_nm", "")
        entry["styleNo"] = r.get("style_no", "")
        entry["qty"] += int(r.get("qty") or 0)
        entry["salesAmt"] += int(r.get("sales_amt") or 0)
    ranked = sorted(agg.values(), key=lambda x: x["qty"], reverse=True)[:top_n]
    return [{"rank": i + 1, **item} for i, item in enumerate(ranked)]


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
                rows, total = fetch_all_orders(page)
                browser.close()
            print(f"수집 완료: {len(rows)}/{total}건")
            items = aggregate_bestsellers(rows)
        except Exception as e:
            print(f"Failed to fetch bestsellers: {e}")
    else:
        print("MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set, skipping")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} bestseller items to {OUT_PATH}")


if __name__ == "__main__":
    main()
