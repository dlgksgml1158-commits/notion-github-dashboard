"""무신사 파트너센터 프로모션 할인 일정 수집 (Playwright 실제 로그인).

비밀번호 기반 정적 쿠키는 IP/세션 바인딩으로 재사용이 안 되기 때문에,
매 실행마다 Playwright로 실제 로그인을 수행해 그 실행 시점에 유효한
세션을 새로 발급받는다.
"""
import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pyotp
from playwright.sync_api import sync_playwright


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
    """최소한의 protobuf wire-format 파서: field_num -> [values] 딕셔너리 반환."""
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
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
        fields.setdefault(field_num, []).append(value)
    return fields


def decode_migration_uri(uri):
    """구글 Authenticator류 'otpauth-migration://offline?data=...' export 형식을 디코딩."""
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


def resolve_totp_secret(raw):
    """MUSINSA_PARTNER_TOTP_SECRET 값에서 실제 TOTP 비밀키를 추출한다.

    Authenticator 확장 프로그램의 "전체 export" 형식(JSON 배열 또는
    otpauth-migration:// 마이그레이션 링크), 단일 otpauth:// URI,
    순수 base32 비밀키를 모두 지원한다.
    """
    raw = raw.strip()

    if raw.startswith("otpauth-migration://"):
        entries = decode_migration_uri(raw)
        for entry in entries:
            label = f"{entry['issuer']} {entry['name']}".lower()
            if "musinsa" in label or "partner" in label:
                return entry["secret"]
        if len(entries) == 1:
            return entries[0]["secret"]
        raise RuntimeError(
            f"otpauth-migration URI has {len(entries)} entries but none matched 'musinsa'/'partner'"
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None

    def _collect_candidates(node, depth=0, acc=None):
        """dict/list를 재귀 탐색해 (label, secret) 후보를 전부 모은다."""
        if acc is None:
            acc = []
        if depth > 5:
            return acc
        if isinstance(node, dict):
            label = " ".join(
                str(node.get(k, "")) for k in ("issuer", "label", "name", "account", "title")
            ).lower()
            secret = node.get("secret") or node.get("key") or node.get("otp_secret") or node.get("otpSecret")
            if isinstance(secret, str) and secret:
                acc.append((label, secret))
            for v in node.values():
                _collect_candidates(v, depth + 1, acc)
        elif isinstance(node, list):
            for item in node:
                _collect_candidates(item, depth + 1, acc)
        return acc

    def _key_shape(node, depth=0):
        """값은 절대 노출하지 않고 구조(키 이름/타입)만 안전하게 덤프."""
        if depth > 3:
            return "..."
        if isinstance(node, dict):
            return {k: _key_shape(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [f"list[{len(node)}]"] + ([_key_shape(node[0], depth + 1)] if node else [])
        return type(node).__name__

    if isinstance(data, (list, dict)):
        candidates = _collect_candidates(data)
        matched = [s for label, s in candidates if "musinsa" in label or "partner" in label]
        if matched:
            return matched[0]
        if len(candidates) == 1:
            return candidates[0][1]
        raise RuntimeError(
            f"MUSINSA_PARTNER_TOTP_SECRET is JSON with {len(candidates)} secret candidates, "
            f"none matched 'musinsa'/'partner'. shape={_key_shape(data)!r}"
        )

    otp_uris = [line.strip() for line in raw.splitlines() if line.strip().startswith("otpauth://")]
    for uri in otp_uris:
        if "musinsa" in uri.lower() or len(otp_uris) == 1:
            m = re.search(r"[?&]secret=([A-Za-z2-7]+)", uri)
            if m:
                return m.group(1)

    compact = raw.replace(" ", "").replace("\n", "")
    if re.fullmatch(r"[A-Za-z2-7]+=*", compact):
        return compact

    lines = raw.splitlines()
    diag = {
        "length": len(raw),
        "line_count": len(lines),
        "first_line_length": len(lines[0]) if lines else 0,
        "first_10_chars_repr": repr(raw[:10]),
        "contains_otpauth": "otpauth" in raw.lower(),
        "contains_migration": "migration" in raw.lower(),
        "contains_braces": raw.strip().startswith(("{", "[")),
        "contains_comma": "," in raw,
        "char_classes": sorted(set(
            "digit" if c.isdigit() else
            "upper" if c.isupper() else
            "lower" if c.islower() else
            repr(c)
            for c in raw[:50]
        )),
    }
    raise RuntimeError(f"Could not parse TOTP secret from MUSINSA_PARTNER_TOTP_SECRET. diag={diag!r}")

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
