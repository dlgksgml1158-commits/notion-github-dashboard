"""메일플러그(원풍물산) 받은편지함에서 무신사·29CM 프로모션/쿠폰 메일 수집.

담당 MD들이 보내는 프로모션·쿠폰 공지 메일은 형식이 저마다 달라 완벽한
파싱을 보장할 수 없다. 그래서 제목/본문에서 정규식으로 캠페인명·진행
기간·할인율을 최대한 추출하되, 기간을 찾지 못한 메일은 버리지 않고
상태를 '확인필요'로 표시해 정보 누락 대신 검토 신호를 남긴다.

만료(종료) 여부 판정과 마감임박(D-3) 표시는 대시보드가 매번 화면을 그릴
때 현재 시각 기준으로 계산한다(다른 프로모션 카드와 동일한 방식) — 이
스크립트는 하루 한 번만 실행되므로 만료 판정을 여기서 고정해버리면
다음 실행 전까지 화면이 부정확해지기 때문이다.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://gw.mailplug.com/"
INBOX_URL = "https://gw.mailplug.com/mail/inbox"
MESSAGE_URL = "https://gw.mailplug.com/mail/inbox/messages/{}"
OUT_PATH = "data-b53e82ab173f/mail_promotions.json"

KST = timezone(timedelta(hours=9))
LOOKBACK_DAYS = 30
SEARCH_KEYWORDS = ["쿠폰", "프로모션", "기획전", "특가", "세일"]
RELEVANT_DOMAINS = ("musinsa.com", "29cm.co.kr", "29cm.com")

DATE_TOKEN = re.compile(
    r"(?:(?P<year>20\d{2}|\d{2})\s*[./]\s*)?"
    r"(?P<month>\d{1,2})\s*[./]\s*(?P<day>\d{1,2})\s*"
    r"(?:\([^)]{0,4}\)\s*)?"
    r"(?:(?P<hour>\d{1,2})\s*:\s*(?P<minute>\d{2}))?"
)
RANGE_SEP = re.compile(r"^\s*[~\-–〜]\s*$")
PERCENT = re.compile(r"(\d{1,2})\s*%")
EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
NOISE_WORDS = ["리마인드", "안내드립니다", "안내", "취합", "제안드립니다", "제안",
               "요청드립니다", "요청", "참여", "드립니다", "공지", "의 건", "건"]
LEADING_DATE_RANGE = re.compile(r"^\s*\d{1,2}[./]\d{1,2}\s*[~\-–]\s*\d{1,2}[./]\d{1,2}\s*")
LEADING_TAG = re.compile(r"^(\s*\[[^\]]*\]\s*)+")


def normalize_year(y, ref_year):
    if y is None:
        return ref_year
    y = int(y)
    return 2000 + y if y < 100 else y


def to_datetime(m, ref_year, start=None):
    year = normalize_year(m.group("year"), ref_year)
    month = int(m.group("month"))
    day = int(m.group("day"))
    hour = int(m.group("hour") or 0)
    minute = int(m.group("minute") or 0)
    extra_day = 0
    if hour == 24:
        hour = 0
        extra_day = 1
    dt = datetime(year, month, day, hour, minute, tzinfo=KST) + timedelta(days=extra_day)
    if start and dt < start:
        dt = dt.replace(year=dt.year + 1)
    return dt


def find_date_range(text, ref_year):
    period_kw = re.search(r"(진행\s*기간|행사\s*기간|기간)\s*[:：]?\s*", text)
    windows = []
    if period_kw:
        windows.append(text[period_kw.end(): period_kw.end() + 150])
    windows.append(text[:500])

    for window in windows:
        matches = list(DATE_TOKEN.finditer(window))
        for i in range(len(matches) - 1):
            a, b = matches[i], matches[i + 1]
            between = window[a.end():b.start()]
            if RANGE_SEP.match(between):
                try:
                    start = to_datetime(a, ref_year)
                    end = to_datetime(b, ref_year, start=start)
                    if end >= start:
                        return start, end
                except Exception:
                    continue
    return None, None


def find_discount(text):
    pcts = sorted(set(int(x) for x in PERCENT.findall(text)))
    if not pcts:
        return ""
    if len(pcts) == 1:
        return f"{pcts[0]}%"
    return f"{pcts[0]}~{pcts[-1]}%"


def clean_title(subject):
    s = subject
    s = re.sub(r"^\s*(RE\s*:|FW\s*:|★+|긴급)\s*", "", s, flags=re.IGNORECASE)
    s = LEADING_TAG.sub("", s)
    s = LEADING_DATE_RANGE.sub("", s)
    s = LEADING_TAG.sub("", s)
    return s.strip(" -‧·")[:60]


def title_key(title):
    t = title.lower()
    for w in NOISE_WORDS:
        t = t.replace(w.lower(), "")
    t = re.sub(r"[『』「」【】\[\]()_/·\-\s]", "", t)
    return t[:20]


def detect_brand(sender_email):
    e = (sender_email or "").lower()
    if "29cm" in e:
        return "29CM"
    if "musinsa" in e:
        return "무신사"
    return "기타"


def login(page, mail_id, mail_pw):
    # 메일함 SPA는 실시간 알림용 폴링/웹소켓이 계속 떠 있어 networkidle이
    # 잘 발생하지 않는다. DOM 로드 완료만 기다리고 필요한 만큼 짧게 쉰다.
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if "/mail/inbox" in page.url:
        return

    # 1단계: login.mailplug.com — 이메일(id@domain) 또는 도메인 입력.
    # MAILPLUG_ID는 반드시 전체 이메일 주소(예: name@company.com) 형식이어야
    # 다음 화면에서 계정이 특정되어 비밀번호만 입력하면 되는 흐름으로 이어진다.
    step1_input = page.locator('#login_input')
    try:
        step1_input.wait_for(timeout=15000)
    except Exception:
        raise RuntimeError(
            f"Step1 login input not found. current_url={page.url} "
            f"body_snippet={page.inner_text('body')[:500]!r}"
        ) from None

    step1_input.fill(mail_id)
    page.locator('.login_btn').click()

    # 2단계: 계정이 특정된 비밀번호 입력 화면으로 넘어간다. 이 화면의 URL 경로는
    # 계정 상태에 따라 달라질 수 있으므로(예: login.mailplug.com/auth/login,
    # mc*.mailplug.com/member/login 등) URL 패턴이 아니라 #password 필드의
    # 등장 자체를 신호로 삼는다.
    pw_loc = page.locator('#password')
    try:
        pw_loc.wait_for(timeout=15000)
    except Exception:
        raise RuntimeError(
            f"Step2 password input not found. current_url={page.url} "
            f"body_snippet={page.inner_text('body')[:500]!r}"
        ) from None
    pw_loc.fill(mail_pw)
    # Cloudflare Turnstile가 자동(비개입) 모드로 백그라운드 검증을 마칠 시간을
    # 준다 — 위젯을 조작하거나 우회하지 않고, 자연스러운 완료를 기다릴 뿐이다.
    page.wait_for_timeout(4000)
    page.locator('#loginButton').click()

    try:
        page.wait_for_url("**/mail/inbox**", timeout=20000)
    except Exception:
        error_text = ""
        try:
            err_loc = page.locator('#errorMessage')
            if err_loc.count() > 0:
                error_text = err_loc.first.inner_text()
        except Exception:
            pass
        button_disabled = None
        try:
            button_disabled = page.locator('#loginButton').get_attribute('disabled')
        except Exception:
            pass
        raise RuntimeError(
            f"Login did not redirect to inbox after step2 submit. current_url={page.url} "
            f"error_message={error_text!r} login_button_disabled={button_disabled!r} "
            f"body_snippet={page.inner_text('body')[:800]!r}"
        ) from None
    page.wait_for_timeout(1500)


def login_with_cookies(context, page, cookies_json):
    """MAILPLUG_COOKIES(로그인된 브라우저에서 내보낸 쿠키 JSON)를 주입해 로그인을
    대체한다. gw.mailplug.com은 Cloudflare Turnstile 캡차가 걸려 있어 GitHub
    Actions 같은 클라우드 IP에서는 아이디/비밀번호 로그인 자체가 사람 확인을
    요구해 자동화가 불가능하다 — 이를 우회하는 대신, 사람이 이미 인증을 마친
    세션 쿠키를 재사용한다.
    """
    try:
        cookies = json.loads(cookies_json)
    except Exception as e:
        raise RuntimeError(f"MAILPLUG_COOKIES is not valid JSON: {e}") from None

    normalized = []
    for c in cookies:
        if not c.get("name") or "value" not in c:
            continue
        entry = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain") or ".mailplug.com",
            "path": c.get("path") or "/",
        }
        if isinstance(c.get("expirationDate"), (int, float)):
            entry["expires"] = c["expirationDate"]
        elif isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
            entry["expires"] = c["expires"]
        if isinstance(c.get("httpOnly"), bool):
            entry["httpOnly"] = c["httpOnly"]
        if isinstance(c.get("secure"), bool):
            entry["secure"] = c["secure"]
        same_site = c.get("sameSite")
        if isinstance(same_site, str):
            same_site = same_site.capitalize()
            if same_site == "No_restriction":
                same_site = "None"
            if same_site in ("Strict", "Lax", "None"):
                entry["sameSite"] = same_site
        normalized.append(entry)

    if not normalized:
        raise RuntimeError("MAILPLUG_COOKIES parsed to zero usable cookies")

    context.add_cookies(normalized)
    page.goto(INBOX_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    if "/mail/inbox" not in page.url:
        raise RuntimeError(
            "MAILPLUG_COOKIES 세션이 만료된 것 같습니다(로그인 화면으로 리다이렉트됨). "
            f"브라우저에서 다시 로그인한 뒤 쿠키를 갱신해 주세요. current_url={page.url}"
        )


def collect_candidate_ids(page):
    seen = {}
    for kw in SEARCH_KEYWORDS:
        page.goto(f"{INBOX_URL}?search={quote(kw)}&searchTarget=all", wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        hrefs = page.eval_on_selector_all(
            'a[href*="/mail/inbox/messages/"]',
            "els => els.map(e => e.getAttribute('href'))",
        )
        for href in hrefs or []:
            m = re.search(r"/messages/(\d+)", href or "")
            if m:
                seen[m.group(1)] = True
    return list(seen.keys())


def parse_message(page, msg_id):
    page.goto(MESSAGE_URL.format(msg_id), wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    body_text = page.inner_text("main")

    lines = [l for l in body_text.splitlines() if l.strip()]
    subject = lines[0].strip() if lines else ""

    emails = EMAIL_ADDR.findall(body_text[:1000])
    sender_email = emails[0] if emails else ""

    date_m = re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", body_text[:1500])
    received_at = date_m.group(0) if date_m else ""

    return {
        "id": msg_id,
        "subject": subject,
        "sender_email": sender_email,
        "received_at": received_at,
        "body": body_text,
    }


def build_items(raw_messages):
    now = datetime.now(KST)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    grouped = {}

    for raw in raw_messages:
        sender_email = raw["sender_email"]
        if not any(d in sender_email.lower() for d in RELEVANT_DOMAINS):
            continue

        received_at = None
        if raw["received_at"]:
            try:
                received_at = datetime.strptime(raw["received_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            except Exception:
                received_at = None
        if received_at and received_at < cutoff:
            continue

        ref_year = received_at.year if received_at else now.year
        title = clean_title(raw["subject"])
        start, end = find_date_range(raw["body"], ref_year)
        discount = find_discount(raw["body"])
        brand = detect_brand(sender_email)

        if start and end:
            if now < start:
                status = "예정"
            elif start <= now <= end:
                status = "진행중"
            else:
                status = "종료"
        else:
            status = "확인필요"

        item = {
            "id": raw["id"],
            "title": title or raw["subject"][:60],
            "brand": brand,
            "discountText": discount,
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "status": status,
            "sender": sender_email,
            "subject": raw["subject"],
            "receivedAt": received_at.isoformat() if received_at else None,
        }

        key = title_key(title)
        prev = grouped.get(key)
        if prev is None or (item["receivedAt"] or "") >= (prev["receivedAt"] or ""):
            grouped[key] = item

    items = list(grouped.values())
    items.sort(key=lambda it: it["receivedAt"] or "", reverse=True)
    return items


def main():
    mail_cookies = os.environ.get("MAILPLUG_COOKIES", "")
    mail_id = os.environ.get("MAILPLUG_ID", "")
    mail_pw = os.environ.get("MAILPLUG_PW", "")
    items = []

    if mail_cookies or (mail_id and mail_pw):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ))
                page = context.new_page()

                # Turnstile 캡차 때문에 아이디/비밀번호 로그인은 GitHub Actions
                # 클라우드 IP에서 사람 확인을 요구해 실패한다. 쿠키가 있으면
                # 그것을 우선 쓰고, 없을 때만(진단 목적으로) 로그인을 시도한다.
                if mail_cookies:
                    login_with_cookies(context, page, mail_cookies)
                else:
                    login(page, mail_id, mail_pw)

                candidate_ids = collect_candidate_ids(page)
                raw_messages = []
                for msg_id in candidate_ids:
                    try:
                        raw_messages.append(parse_message(page, msg_id))
                    except Exception as e:
                        print(f"Skip message {msg_id}: {e}")

                browser.close()

            items = build_items(raw_messages)
        except Exception as e:
            print(f"Failed to fetch mail promotions: {e}")
    else:
        print("MAILPLUG_COOKIES/MAILPLUG_ID/MAILPLUG_PW not set, skipping")

    # 상위 스크래핑이 일시적으로 실패해 빈 데이터가 나오면, 화면이 갑자기
    # 텅 비지 않도록 기존 데이터를 그대로 유지한다.
    if not items and os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                prev = json.load(f)
            if prev.get("items"):
                print("Fetched 0 items; keeping previous mail promotions")
                return
        except Exception:
            pass

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} mail promotion items to {OUT_PATH}")


if __name__ == "__main__":
    main()
