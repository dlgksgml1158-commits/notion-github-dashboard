"""임시 진단: ord01/search를 직접 fetch로 호출해 LIMIT을 높이고 90일 범위로
가져올 때 더 빠르게(페이지 수 줄여서) 수집 가능한지 확인."""
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_HISTORY_PAGE = "https://partner.musinsa.com/order/history?S_ORD_STATE=50&summary_info=Y"


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""

    captured = {"body": None}

    with sync_playwright() as p:
        browser, context, page = new_authenticated_context(
            p, partner_id, partner_pw, mss_mac, totp_secret
        )

        def on_request(req):
            if "ord01/search" in req.url and req.post_data and not captured["body"]:
                captured["body"] = req.post_data

        page.on("request", on_request)
        page.goto(ORDER_HISTORY_PAGE, wait_until="networkidle")
        page.wait_for_timeout(3000)

        bizest_frame = next(f for f in page.frames if "bizest.musinsa.com" in f.url)

        print("FULL_CAPTURED_BODY:", captured["body"])

        params = parse_qs(captured["body"] or "")
        params_flat = {k: v[0] for k, v in params.items()}
        print("PARSED_PARAMS:", json.dumps(params_flat, ensure_ascii=False))

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=90)
        params_flat["S_SDATE"] = start.strftime("%Y-%m-%d")
        params_flat["S_EDATE"] = end.strftime("%Y-%m-%d")

        for test_limit in [100, 500, 1000, 3000]:
            params_flat["LIMIT"] = str(test_limit)
            params_flat["PAGE"] = "1"
            body = urlencode(params_flat)
            result = bizest_frame.evaluate(
                """async (body) => {
                    const r = await fetch('https://bizest.musinsa.com/po/order-group-admin/api/order/ord01/search', {
                        method: 'POST',
                        credentials: 'include',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body,
                    });
                    const text = await r.text();
                    let total = null, dataLen = null;
                    try {
                        const j = JSON.parse(text);
                        total = j.total;
                        dataLen = (j.data || []).length;
                    } catch (e) {}
                    return { status: r.status, total, dataLen, textLen: text.length };
                }""",
                body,
            )
            print(f"LIMIT={test_limit} (90-day range):", json.dumps(result, ensure_ascii=False))

        browser.close()


if __name__ == "__main__":
    main()
