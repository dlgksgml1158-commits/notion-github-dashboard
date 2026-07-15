"""임시 진단: 주문/배송 화면의 상태 탭(전체/배송완료/구매확정 등)이 어떤
S_ORD_STATE 값에 매핑되는지 조사해 커밋되는 데이터 파일로 남긴다.

결과를 확인한 뒤 fetch_musinsa_bestsellers.py의 상태 코드를 바꾸고 나면
이 스크립트와 워크플로 단계는 삭제한다.
"""
import json
import os
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from fetch_musinsa_partner_promo import new_authenticated_context, resolve_totp_secret

ORDER_HISTORY_PAGE = "https://partner.musinsa.com/order/history"
OUT_PATH = "data-b53e82ab173f/_diag_order_status_tabs.json"

TAB_FINDER_JS = """
() => {
    const out = [];
    const seen = new Set();
    const attrs = ['href', 'onclick', 'data-value', 'data-status', 'data-ord-state', 'data-tab', 'value'];
    document.querySelectorAll('a, button, li, [onclick], [data-value], option').forEach(el => {
        for (const a of attrs) {
            const v = el.getAttribute(a);
            if (v && v.includes('ORD_STATE')) {
                const key = a + ':' + v;
                if (!seen.has(key)) {
                    seen.add(key);
                    out.push({ text: (el.textContent || '').trim().slice(0, 40), tag: el.tagName, attr: a, value: v });
                }
            }
        }
    });
    return out;
}
"""


def main():
    partner_id = os.environ.get("MUSINSA_PARTNER_ID", "")
    partner_pw = os.environ.get("MUSINSA_PARTNER_PW", "")
    mss_mac = os.environ.get("MUSINSA_MSS_MAC", "")
    totp_secret_raw = os.environ.get("MUSINSA_PARTNER_TOTP_SECRET", "")
    result = {"error": None, "tabs": [], "frame_urls": [], "html_snippets": {}}

    if partner_id and partner_pw:
        try:
            totp_secret = resolve_totp_secret(totp_secret_raw) if totp_secret_raw else ""
            with sync_playwright() as p:
                browser, context, page = new_authenticated_context(
                    p, partner_id, partner_pw, mss_mac, totp_secret
                )
                page.goto(ORDER_HISTORY_PAGE, wait_until="networkidle")
                page.wait_for_timeout(3000)

                result["frame_urls"] = [f.url for f in page.frames]
                for frame in page.frames:
                    try:
                        found = frame.evaluate(TAB_FINDER_JS)
                    except Exception as e:
                        found = [{"error": str(e)}]
                    if found:
                        result["tabs"].extend(
                            {**t, "frameUrl": frame.url} for t in found
                        )
                    try:
                        result["html_snippets"][frame.url] = frame.content()[:15000]
                    except Exception:
                        pass

                browser.close()
        except Exception as e:
            result["error"] = str(e)
    else:
        result["error"] = "MUSINSA_PARTNER_ID/MUSINSA_PARTNER_PW not set"

    result["updatedAt"] = datetime.now(timezone.utc).isoformat()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Wrote diag to {OUT_PATH}: {len(result['tabs'])} tabs found, error={result['error']}")


if __name__ == "__main__":
    main()
