import json
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.musinsa.com/api2/hm/web/v3/pans/sale/modules?storeCode=musinsa"
OUT_PATH = "data-b53e82ab173f/musinsa_promo.json"


def fetch_modules():
    req = urllib.request.Request(
        API_URL,
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.load(resp)


def extract_items(payload):
    items = []
    for module in payload.get("data", {}).get("modules", []):
        section = ((module.get("title") or {}).get("title") or {}).get("text", "")
        for it in module.get("items", []) or []:
            info = it.get("info") or {}
            image = it.get("image") or {}
            on_click = it.get("onClick") or {}
            name = info.get("productName")
            if not name:
                continue
            items.append({
                "section": section,
                "brand": info.get("brandName", ""),
                "name": name,
                "discountRate": info.get("discountRatio", 0),
                "finalPrice": info.get("finalPrice", 0),
                "imgUrl": image.get("url", ""),
                "productUrl": on_click.get("url", ""),
            })
    return items


def main():
    payload = fetch_modules()
    items = extract_items(payload)
    output = {
        "platform": "musinsa",
        "source": API_URL,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} promo items to {OUT_PATH}")


if __name__ == "__main__":
    main()
