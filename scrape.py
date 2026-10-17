#!/usr/bin/env python3
"""
Haalt de wekelijkse aanbiedingen van Nederlandse supermarkten op en schrijft
ze naar site/data/offers.json, dat door site/index.html wordt getoond.

Werkwijze per supermarkt:
  1. Open de aanbiedingenpagina in een echte (headless) Chromium-browser.
  2. Vang alle JSON-antwoorden op die de pagina zelf ophaalt (de meeste
     supermarktsites laden hun aanbiedingen via een interne API).
  3. Lees ook ingebedde JSON uit de pagina (JSON-LD, __NEXT_DATA__, enz.).
  4. Zoek in al die JSON naar objecten die op een product-met-aanbieding
     lijken (naam + prijs en/of actietekst).
  5. Levert dat te weinig op, dan valt hij terug op het uitlezen van de
     zichtbare productkaartjes op de pagina.

Gebruik:
  python scraper/scrape.py                 # alle actieve supermarkten
  python scraper/scrape.py --only ah,jumbo # alleen deze
  python scraper/scrape.py --debug         # bewaar ruwe data in debug/
  python scraper/scrape.py --headful       # laat het browservenster zien
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parent.parent
CONFIG = Path(__file__).with_name("stores.json")
OUT = ROOT / "site" / "data" / "offers.json"
DEBUG_DIR = ROOT / "debug"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

# Sleutels waaronder sites doorgaans naam, prijs, actie en afbeelding opslaan.
NAME_KEYS = ("title", "name", "productName", "displayName", "productTitle", "headline")
PRICE_KEYS = (
    "promotionPrice", "bonusPrice", "offerPrice", "salePrice", "currentPrice",
    "newPrice", "priceNow", "now", "price", "amount",
)
WAS_KEYS = (
    "priceBeforeBonus", "wasPrice", "oldPrice", "regularPrice", "originalPrice",
    "fromPrice", "was", "listPrice", "normalPrice", "basePrice",
)
PROMO_KEYS = (
    "bonusMechanism", "promotionLabel", "discountLabel", "promotionText",
    "mechanism", "shieldText", "offerText", "sticker", "promotion", "discount",
    "label", "tag", "tags",
)
IMAGE_KEYS = ("image", "imageUrl", "imageURL", "img", "thumbnail", "images", "picture", "media")
URL_KEYS = ("url", "link", "href", "productUrl", "slug")

SKIP_HOSTS = re.compile(
    r"google|facebook|doubleclick|hotjar|cookiebot|onetrust|adobe|demdex|datadog|"
    r"sentry|newrelic|tiktok|pinterest|bing|criteo|optimizely|usercentrics|didomi|"
    r"cloudflareinsights|trustpilot|kiyoh|clarity",
    re.I,
)
PROMO_RE = re.compile(
    r"(\d\s*\+\s*\d\s*gratis|\d+\s*voor\s*€?\s*\d+(?:[.,]\d{1,2})?|(?<![\d.,])\d{1,2}\s*%\s*korting|"
    r"\d+e\s+halve\s+prijs|2e\s+halve\s+prijs|halve\s+prijs|\d+e\s+gratis|gratis)",
    re.I,
)
PRICE_RE = re.compile(r"€\s*(\d{1,3}(?:[.,]\d{1,2})?)|(?<![\d.,])(\d{1,3}[.,]\d{2})(?![\d])")
STOP_NAMES = {
    "home", "menu", "aanbiedingen", "inloggen", "zoeken", "winkelmand", "account",
    "bonus", "acties", "folder", "cookies", "meer", "bekijk", "alle aanbiedingen",
}

CATEGORIES = [
    ("Snoep, chips & koek", r"chips|snoep|chocola|koek|biscuit|drop|winegum|lays|doritos|pringles|m&m|milka|tony|verkade|nootjes|noten|popcorn|ijs\b|magnum|ben & jerry|look o look|haribo|cake|taart|gebak|stroopwafel"),
    ("Groente & fruit", r"appel|peer|banaan|druif|aardbei|tomaat|komkommer|paprika|sla\b|ui\b|uien|wortel|aardappel|champignon|broccoli|bloemkool|spinazie|avocado|mango|citroen|sinaasappel|mandarijn|fruit|groente|bessen|meloen|kiwi|prei|courgette"),
    ("Vlees, vis & vega", r"kip|gehakt|vlees|biefstuk|varken|rund|worst|spek|ham\b|filet|schnitzel|burger|zalm|tonijn|vis\b|garnal|kabeljauw|vega|vegetar|tofu|shoarma|rookworst|salami|kalkoen"),
    ("Zuivel & eieren", r"melk|yoghurt|kwark|vla|kaas|boter|room|ei\b|eieren|zuivel|skyr|cottage|campina|optimel|arla|danone|alpro|leerdammer|président|president|beemster|gouda|goudse"),
    ("Brood & ontbijt", r"brood|bol\b|bollen|croissant|beschuit|cracker|ontbijt|muesli|granola|cornflakes|havermout|pindakaas|hagelslag|jam\b|nutella|ontbijtkoek"),
    ("Dranken", r"cola|pepsi|fanta|sap\b|sappen|limonade|water\b|spa\b|frisdrank|bier|heineken|amstel|grolsch|wijn|koffie|thee|ice tea|energy|siroop|radler|jupiler|hertog jan|nespresso|senseo|douwe egberts"),
    ("Diepvries & maaltijden", r"pizza|diepvries|maaltijd|lasagne|soep|noodle|pasta|spaghetti|rijst|knorr|unox|conimex|saus|mix\b|wereldgerecht|friet|patat|kroket|snack|salade"),
    ("Huishouden", r"wasmiddel|afwas|schoonmaak|toiletpapier|keukenrol|vuilniszak|ariel|robijn|dreft|vanish|cif\b|wc-|witte reus|ecover|wasverzachter|vaatwas|finish|sun\b|batterij|wc eend|antikal|swiffer|ambi pur|toiletblok|tissue|zakdoek|seepje"),
    ("Verzorging & baby", r"shampoo|douche|deodorant|deo\b|tandpasta|scheer|gillette|nivea|dove|axe|zeep|luier|pampers|billendoekjes|maandverband|tampon|creme|crème|parfum|oral-b|colgate|prodent|zendium|smyle|andrélon|andrelon|douche"),
]
CATEGORY_RES = [(name, re.compile(rx, re.I)) for name, rx in CATEGORIES]


# ---------------------------------------------------------------- helpers

def classify(name: str) -> str:
    for cat, rx in CATEGORY_RES:
        if rx.search(name):
            return cat
    return "Overig"


def parse_number(text: str) -> float | None:
    m = re.search(r"(\d{1,4}(?:[.,]\d{1,2})?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_price(v, key: str = "", cents: bool = False, depth: int = 0) -> float | None:
    if v is None or isinstance(v, bool) or depth > 2:
        return None
    if isinstance(v, dict):
        for k in ("now", "amount", "value", "price", "current", "unitPrice", "promotionPrice"):
            if k in v:
                p = parse_price(v[k], k, cents, depth + 1)
                if p is not None:
                    return p
        return None
    if isinstance(v, (int, float)):
        p = float(v)
        if "cent" in key.lower() or (cents and isinstance(v, int)):
            p = p / 100
    elif isinstance(v, str):
        if len(v) > 30:
            return None
        p = parse_number(v)
        if p is None:
            return None
    else:
        return None
    return round(p, 2) if 0 < p < 1000 else None


def text_of(v, depth: int = 0) -> str | None:
    if v is None or isinstance(v, bool) or depth > 2:
        return None
    if isinstance(v, str):
        s = " ".join(v.split())
        return s or None
    if isinstance(v, dict):
        for k in ("text", "label", "title", "name", "description", "value"):
            t = text_of(v.get(k), depth + 1)
            if t:
                return t
        return None
    if isinstance(v, list):
        parts = [t for t in (text_of(x, depth + 1) for x in v[:3]) if t]
        return " · ".join(parts) or None
    return None


def image_of(v, depth: int = 0) -> str | None:
    if v is None or depth > 3:
        return None
    if isinstance(v, str):
        if v.startswith("//"):
            v = "https:" + v
        return v if v.startswith("http") else None
    if isinstance(v, dict):
        for k in ("url", "src", "href", "imageUrl", "large", "medium", "small"):
            u = image_of(v.get(k), depth + 1)
            if u:
                return u
        return None
    if isinstance(v, list) and v:
        return image_of(v[0], depth + 1)
    return None


def clean_name(s: str | None) -> str | None:
    if not s or not isinstance(s, str):
        return None
    s = " ".join(s.split())
    if not (3 <= len(s) <= 140):
        return None
    if s.lower() in STOP_NAMES or s.startswith(("http", "/")) or not re.search(r"[a-zA-Z]", s):
        return None
    return s


def candidate_from(d: dict, store: dict) -> dict | None:
    cents = bool(store.get("cents"))
    name = None
    for k in NAME_KEYS:
        name = clean_name(d.get(k)) if isinstance(d.get(k), str) else None
        if name:
            break
    if not name:
        return None

    price = None
    for k in PRICE_KEYS:
        if k in d:
            price = parse_price(d[k], k, cents)
            if price is not None:
                break
    offers = d.get("offers")  # JSON-LD
    if price is None and offers:
        o = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(o, dict):
            price = parse_price(o.get("price") or o.get("lowPrice"), "price", cents)

    was = None
    for k in WAS_KEYS:
        if k in d:
            was = parse_price(d[k], k, cents)
            if was is not None:
                break
    if was is None:  # bv. {"price": {"now": 1.49, "was": 2.29}}
        for k in PRICE_KEYS:
            v = d.get(k)
            if isinstance(v, dict):
                for wk in WAS_KEYS:
                    if wk in v:
                        was = parse_price(v[wk], wk, cents)
                        if was is not None:
                            break
            if was is not None:
                break

    promo = None
    for k in PROMO_KEYS:
        if k not in d:
            continue
        v = d[k]
        if k == "discount" and isinstance(v, (int, float)) and 0 < v < 100:
            promo = f"{int(v)}% korting"
        else:
            t = text_of(v)
            if t and len(t) <= 80 and t.lower() != name.lower():
                promo = t
        if promo:
            break
    if not promo and d.get("isBonus") is True:
        promo = "Bonus"

    if price is None and not promo:
        return None
    if was is not None and price is not None and was <= price:
        was = None

    image = None
    for k in IMAGE_KEYS:
        image = image_of(d.get(k))
        if image:
            break
    url = None
    for k in URL_KEYS:
        u = d.get(k)
        if isinstance(u, str) and u and not u.lower().endswith((".jpg", ".png", ".webp", ".svg")):
            url = u
            break
    return {"name": name, "price": price, "was_price": was, "promo": promo, "image": image, "url": url}


def walk(obj, store: dict, out: list, depth: int = 0) -> None:
    if depth > 30:
        return
    if isinstance(obj, dict):
        c = candidate_from(obj, store)
        if c:
            out.append(c)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                walk(v, store, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                walk(v, store, out, depth + 1)


def from_dom_items(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        name = clean_name(it.get("name"))
        if not name:
            continue
        text = it.get("text") or ""
        prices = []
        for m in PRICE_RE.finditer(text):
            p = parse_number(m.group(1) or m.group(2))
            if p and 0 < p < 1000:
                prices.append(p)
        pm = PROMO_RE.search(text)
        promo = " ".join(pm.group(1).split()) if pm else None
        price = min(prices) if prices else None
        was = max(prices) if len(prices) > 1 and max(prices) > min(prices) else None
        if price is None and not promo:
            continue
        out.append({"name": name, "price": price, "was_price": was, "promo": promo,
                    "image": it.get("image"), "url": it.get("url")})
    return out


def absolutize(url: str | None, base: str) -> str | None:
    if not url:
        return None
    if url.startswith("http"):
        return url
    from urllib.parse import urljoin
    if url.startswith("/") or "/" in url:
        return urljoin(base, url)
    return None


def dedupe(items: list[dict], store: dict) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for it in items:
        key = (it["name"].lower(), it.get("price"))
        cur = seen.get(key)
        if cur is None:
            seen[key] = dict(it)
        else:  # vul ontbrekende velden aan
            for f in ("was_price", "promo", "image", "url"):
                if not cur.get(f) and it.get(f):
                    cur[f] = it[f]
    result = []
    for it in seen.values():
        it["url"] = absolutize(it.get("url"), store["url"]) or store["url"]
        it["image"] = it.get("image") if (it.get("image") or "").startswith("http") else None
        it["store"] = store["id"]
        it["category"] = classify(it["name"])
        result.append(it)
    return result


DOM_JS = r"""
() => {
  const priceRe = /€\s?\d+[.,]\d{1,2}|\b\d{1,3}[.,]\d{2}\b/;
  const promoRe = /(\d\s*\+\s*\d\s*gratis|\d+\s*voor\s*€?\s*\d|\d+\s*%\s*korting|halve\s+prijs|gratis)/i;
  const sel = 'article, li, [class*="product" i], [class*="card" i], [class*="tile" i], ' +
              '[data-testid*="product" i], [class*="offer" i], [class*="promo" i]';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const parts = [];
    const tw = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    while (tw.nextNode()) { const v = tw.currentNode.nodeValue.trim(); if (v) parts.push(v); }
    const t = parts.join(' | ');
    if (t.length < 5 || t.length > 500) continue;
    if (!priceRe.test(t) && !promoRe.test(t)) continue;
    const img = el.querySelector('img');
    const h = el.querySelector('h2,h3,h4,[class*="title" i],[class*="name" i]');
    let name = (h && h.innerText && h.innerText.trim()) || (img && img.alt && img.alt.trim()) ||
      parts.find(l => l.length > 3 && !priceRe.test(l) && !promoRe.test(l));
    if (!name) continue;
    const a = el.querySelector('a[href]') || el.closest('a[href]');
    out.push({ name: name.slice(0, 140), text: t,
               image: img ? (img.currentSrc || img.src || null) : null,
               url: a ? a.href : null });
  }
  return out;
}
"""

EMBEDDED_JS = r"""
() => Array.from(document.querySelectorAll(
  'script[type="application/ld+json"], script[type="application/json"], script#__NEXT_DATA__, script#__NUXT_DATA__'
)).map(s => s.textContent).filter(Boolean)
"""


# ---------------------------------------------------------------- scraping

async def accept_cookies(page) -> None:
    pattern = re.compile(r"(alles\s+)?accepte?r|akkoord|alles\s+toestaan|toestaan|accept", re.I)
    for frame in [page.main_frame, *page.frames]:
        try:
            btn = frame.get_by_role("button", name=pattern).first
            await btn.click(timeout=2500)
            await page.wait_for_timeout(800)
            return
        except Exception:
            continue


async def load_everything(page, scrolls: int) -> None:
    more = re.compile(r"(toon|laad|bekijk)\s+meer|meer\s+(laden|aanbiedingen|producten)", re.I)
    for _ in range(scrolls):
        await page.mouse.wheel(0, 2500)
        await page.wait_for_timeout(700)
        try:
            btn = page.get_by_role("button", name=more).first
            if await btn.is_visible(timeout=300):
                await btn.click(timeout=1500)
                await page.wait_for_timeout(1200)
        except Exception:
            pass


async def scrape_store(browser, store: dict, debug: bool) -> dict:
    context = await browser.new_context(
        user_agent=USER_AGENT, locale="nl-NL", timezone_id="Europe/Amsterdam",
        viewport={"width": 1366, "height": 900},
    )
    page = await context.new_page()
    payloads: list[tuple[str, object]] = []
    pending: list[asyncio.Task] = []

    async def grab(resp):
        try:
            ct = resp.headers.get("content-type", "")
            if "json" not in ct or SKIP_HOSTS.search(resp.url):
                return
            body = await resp.body()
            if len(body) > 15_000_000:
                return
            payloads.append((resp.url, json.loads(body)))
        except Exception:
            pass

    page.on("response", lambda r: pending.append(asyncio.ensure_future(grab(r))))

    result = {"id": store["id"], "name": store["name"], "url": store["url"],
              "color": store.get("color"), "status": "ok", "method": None, "error": None, "offers": []}
    try:
        await page.goto(store["url"], wait_until="domcontentloaded", timeout=45_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except PWTimeout:
            pass
        await accept_cookies(page)
        await load_everything(page, int(store.get("scrolls", 12)))
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except PWTimeout:
            pass
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        found: list[dict] = []
        for _, data in payloads:
            walk(data, store, found)
        for raw in await page.evaluate(EMBEDDED_JS):
            try:
                walk(json.loads(raw), store, found)
            except Exception:
                pass
        offers = dedupe(found, store)
        result["method"] = "json"

        if len(offers) < int(store.get("min_offers", 8)):
            dom_raw = from_dom_items(await page.evaluate(DOM_JS))
            merged = dedupe(found + dom_raw, store)
            if len(merged) > len(offers):
                offers = merged
                result["method"] = "pagina" if not found else "json+pagina"

        result["offers"] = offers
        if not offers:
            result["status"] = "leeg"
            result["error"] = "Geen aanbiedingen herkend. Draai met --debug en bekijk debug/" + store["id"]

        if debug:
            d = DEBUG_DIR / store["id"]
            d.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(d / "screenshot.png"), full_page=True)
            (d / "page.html").write_text(await page.content(), encoding="utf-8")
            for i, (url, data) in enumerate(payloads):
                (d / f"response_{i:03d}.json").write_text(
                    json.dumps({"url": url, "data": data}, ensure_ascii=False, indent=1)[:5_000_000],
                    encoding="utf-8")
            (d / "offers.json").write_text(json.dumps(offers, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        result["status"] = "fout"
        result["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
    finally:
        await context.close()
    return result


def load_previous() -> dict:
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def main() -> int:
    ap = argparse.ArgumentParser(description="Haal supermarktaanbiedingen op.")
    ap.add_argument("--only", help="komma-gescheiden store-id's, bv. ah,jumbo")
    ap.add_argument("--debug", action="store_true", help="bewaar ruwe data in debug/")
    ap.add_argument("--headful", action="store_true", help="toon het browservenster")
    args = ap.parse_args()

    stores = json.loads(CONFIG.read_text(encoding="utf-8"))
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        stores = [s for s in stores if s["id"] in wanted]
    else:
        stores = [s for s in stores if s.get("enabled", True)]

    previous = load_previous()
    prev_offers = {}
    for o in previous.get("offers", []):
        prev_offers.setdefault(o["store"], []).append(o)
    prev_status = {s["id"]: s for s in previous.get("stores", [])}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_offers, statuses = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headful,
                                           args=["--disable-blink-features=AutomationControlled"])
        for store in stores:  # één voor één: vriendelijk voor de sites
            r = await scrape_store(browser, store, args.debug)
            offers = r.pop("offers")
            if offers:
                r["fetched_at"] = now
                r["stale"] = False
            elif store["id"] in prev_offers:  # oude data bewaren, maar markeren
                offers = [dict(o, stale=True) for o in prev_offers[store["id"]]]
                r["fetched_at"] = prev_status.get(store["id"], {}).get("fetched_at")
                r["stale"] = True
            else:
                r["fetched_at"] = None
                r["stale"] = False
            r["count"] = len(offers)
            all_offers.extend(offers)
            statuses.append(r)
            flag = {"ok": "✓", "leeg": "∅", "fout": "✗"}[r["status"]]
            print(f"{flag} {store['name']:<14} {r['count']:>4} aanbiedingen"
                  f"{'  (oude data)' if r['stale'] else ''}"
                  f"{'  [' + r['method'] + ']' if r['method'] else ''}"
                  f"{'  ' + r['error'] if r['error'] else ''}")
        await browser.close()

    # Supermarkten die niet in deze run zaten (bij --only) blijven staan.
    ran = {s["id"] for s in statuses}
    for sid, st in prev_status.items():
        if sid not in ran:
            statuses.append(st)
            all_offers.extend(prev_offers.get(sid, []))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"generated_at": now, "stores": statuses, "offers": all_offers},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\n{len(all_offers)} aanbiedingen opgeslagen in {OUT.relative_to(ROOT)}")
    return 0 if any(s["status"] == "ok" for s in statuses) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
