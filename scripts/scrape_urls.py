from __future__ import annotations

import csv
import html
import json
import random
import re
import time
import http.cookiejar
import unicodedata
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

CATEGORY_KEYWORDS = [
    ("procesadores", ("procesador", "procesadores", "ryzen", "core i", "core ultra", "pentium", "celeron")),
    ("mothers", ("mother", "motherboard", "am4", "am5", "s1700", "b550", "b650", "b760", "x670", "x870")),
    ("memorias", ("memoria", "memorias", "ram", "ddr4", "ddr5")),
    ("almacenamiento", ("ssd", "disco", "almacenamiento", "nvme", "m.2", "sata")),
    ("placas_video", ("placa de video", "placas de video", "geforce", "radeon", "rtx", "gtx", "rx ")),
    ("fuentes", ("fuente", "fuentes", "psu", "80 plus")),
    ("refrigeracion", ("cooler", "refrigeracion", "watercooler", "fan")),
    ("monitores", ("monitor", "monitores")),
]

BRANDS = [
    "Adata",
    "AMD",
    "Asrock",
    "Asus",
    "Biostar",
    "Cooler Master",
    "Corsair",
    "Crucial",
    "Deepcool",
    "Evolabs",
    "Gigabyte",
    "Hiksemi",
    "Intel",
    "Kingston",
    "MSI",
    "Nvidia",
    "Sapphire",
    "Sentey",
    "Thermaltake",
    "Western Digital",
    "XPG",
    "Zotac",
]

MAXIMUS_CATEGORY_GROUPS = {
    52: "procesadores",
    48: "placas_video",
    4113: "mothers",
    53: "memorias",
    47: "almacenamiento",
    4: "fuentes",
    50: "refrigeracion",
    49: "monitores",
}

COMPRAGAMER_PRODUCTS_URL = "https://static.compragamer.com/productos"
COMPRAGAMER_BRANDS_URL = "https://static.compragamer.com/marcas"
COMPRAGAMER_CATEGORY_GROUPS = {
    27: "procesadores",
    48: "procesadores",
    5: "monitores",
    6: "placas_video",
    62: "placas_video",
    26: "mothers",
    49: "mothers",
    15: "memorias",
    81: "almacenamiento",
    34: "fuentes",
    35: "refrigeracion",
}


@dataclass
class WebProduct:
    source: str
    source_label: str
    category: str
    name: str
    price_ars: int | None
    url: str
    article: str
    brand: str
    availability: str
    page: int
    scraped_at: str


def request_html(url: str, retries: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(retries):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-AR,es;q=0.9,en;q=0.7",
            "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
        }
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"No se pudo descargar {url}: {last_error}")


def request_html_with_opener(url: str, opener: urllib.request.OpenerDirector) -> str:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.7",
        "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
    }
    with opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def clean_text(value: object) -> str:
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_price(value: object) -> int | None:
    text = clean_text(value)
    patterns = [
        r"(?:\$|ARS|AR\$)\s*([0-9]{1,3}(?:[.\s][0-9]{3})*(?:,[0-9]{1,2})?|[0-9]+)",
        r"\b([0-9]{1,3}(?:[.\s][0-9]{3})+(?:,[0-9]{1,2})?)\b",
        r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            number = match.group(1).replace(".", "").replace(" ", "").split(",")[0]
            return int(number) if number.isdigit() else None
    return None


def has_truncated_ars_prices(products: list[WebProduct]) -> bool:
    prices = [product.price_ars for product in products if product.price_ars is not None]
    return bool(prices) and max(prices) < 1000


def parse_number(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = clean_text(value).replace(".", "").replace(",", ".")
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", text)
    return int(float(match.group(0))) if match else None


def infer_category(*values: str) -> str:
    text = " ".join(clean_text(value).lower() for value in values)
    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return category
    return "otros"


def infer_brand(name: str, fallback: object = "") -> str:
    fallback_text = clean_text(fallback)
    if fallback_text:
        return fallback_text
    normalized = clean_text(name).lower()
    for brand in BRANDS:
        if re.search(rf"\b{re.escape(brand.lower())}\b", normalized):
            return brand
    return ""


def url_param(url: str, name: str, default: int = -1) -> int:
    match = re.search(rf"(?:[/?&]|^){re.escape(name)}=(-?\d+)", url, flags=re.IGNORECASE)
    return int(match.group(1)) if match else default


def source_label_for_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    netloc = netloc[4:] if netloc.startswith("www.") else netloc
    aliases = {
        "compragamer.com": "cg",
        "maximus.com.ar": "maximus",
        "mexx.com.ar": "mexx",
    }
    if netloc in aliases:
        return aliases[netloc]
    host = netloc.split(":")[0]
    parts = [part for part in host.split(".") if part not in {"com", "com.ar", "net", "org", "ar"}]
    return re.sub(r"[^a-z0-9]+", "_", (parts[0] if parts else host).lower())


def maximus_product_url(page_url: str, item: dict) -> str:
    desc = clean_text(item.get("item_desc4link"))
    item_id = clean_text(item.get("item_id"))
    code = clean_text(item.get("item_code4web"))
    if desc and item_id:
        return f"https://www.maximus.com.ar/Producto/{desc}/ITEM={item_id}/maximus.aspx?PN={code}"
    return page_url


def compragamer_product_url(item: dict) -> str:
    normalized = unicodedata.normalize("NFKD", clean_text(item.get("nombre")))
    slug = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    product_id = clean_text(item.get("id_producto"))
    cate = clean_text(item.get("id_subcategoria"))
    return f"https://compragamer.com/producto/{slug}_{product_id}?cate={cate}"


def scrape_compragamer(url: str) -> list[WebProduct]:
    cate = url_param(url, "cate")
    with urllib.request.urlopen(COMPRAGAMER_PRODUCTS_URL, timeout=30) as response:
        products = json.load(response)
    with urllib.request.urlopen(COMPRAGAMER_BRANDS_URL, timeout=30) as response:
        brands_data = json.load(response)

    brands = {brand.get("id"): clean_text(brand.get("nombre")) for brand in brands_data}
    scraped_at = datetime.now().isoformat(timespec="seconds")
    source_label = source_label_for_url(url)
    rows = []
    for idx, item in enumerate(products, start=1):
        item_cate = item.get("id_subcategoria")
        if cate != -1 and item_cate != cate:
            continue
        group = COMPRAGAMER_CATEGORY_GROUPS.get(item_cate)
        price = parse_number(item.get("precioEspecial"))
        if not group or price is None or price <= 0:
            continue
        name = clean_text(item.get("nombre"))
        if not name:
            continue
        brand = brands.get(item.get("id_marca"), "")
        rows.append(
            WebProduct(
                source=urlparse(url).netloc,
                source_label=source_label,
                category=group,
                name=name,
                price_ars=price,
                url=compragamer_product_url(item),
                article=clean_text(item.get("id_producto") or idx),
                brand=infer_brand(name, brand),
                availability="vendible" if item.get("vendible") else "",
                page=1,
                scraped_at=scraped_at,
            )
        )
    return rows


def scrape_maximus(url: str) -> list[WebProduct]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    page_html = request_html_with_opener(url, opener)
    ws_match = re.search(r'id=["\']hidWebSiteID["\'][^>]+value=["\']([^"\']+)["\']', page_html)
    if not ws_match:
        return []

    ws_id = ws_match.group(1)
    cat_id = url_param(url, "CAT")
    subcat_id = url_param(url, "SCAT")
    brand_id = url_param(url, "M")
    order = url_param(url, "OR", 1)
    page = url_param(url, "PAGE", 1)
    search_match = re.search(r"(?:[/?&]|^)BUS=([^/]+)", url, flags=re.IGNORECASE)
    search = unquote(search_match.group(1)) if search_match else ""
    params = {
        "ws_id": ws_id,
        "comp_id": 1,
        "prli_id": 17,
        "cust_id": -1,
        "page": page,
        "cat_id": cat_id,
        "subcat_id": subcat_id,
        "brand_id": brand_id,
        "local": 0,
        "search": search,
        "order": order,
        "price_min": "",
        "price_max": "",
        "wco_tV": [],
    }
    body = json.dumps(
        {
            "guidWS_Id": ws_id,
            "strScriptLabel": "web.MAX.GetItemList4Search_v5",
            "JSonParameters": json.dumps(params, separators=(",", ":")),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://www.maximus.com.ar/wfmWebSite2.aspx/wsNRW_Script",
        data=body,
        headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": url,
        },
    )
    with opener.open(req, timeout=30) as response:
        outer = json.loads(response.read().decode("utf-8", errors="replace"))
    inner = json.loads(outer.get("d", "{}"))
    items = inner.get("data", {}).get("items", [])
    scraped_at = datetime.now().isoformat(timespec="seconds")
    category = MAXIMUS_CATEGORY_GROUPS.get(cat_id) or infer_category(url)
    source_label = source_label_for_url(url)
    rows = []
    for idx, item in enumerate(items, start=1):
        name = clean_text(item.get("item_desc"))
        price = parse_number(item.get("prli_price_original") or item.get("prli_price"))
        if not name or price is None:
            continue
        rows.append(
            WebProduct(
                source=urlparse(url).netloc,
                source_label=source_label,
                category=category,
                name=name,
                price_ars=price,
                url=maximus_product_url(url, item),
                article=clean_text(item.get("item_code4web") or item.get("item_id") or idx),
                brand=infer_brand(name),
                availability="",
                page=page,
                scraped_at=scraped_at,
            )
        )
    return rows


def iter_jsonld_products(page_html: str) -> list[dict]:
    products: list[dict] = []
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        page_html,
        flags=re.IGNORECASE,
    )
    for script in scripts:
        try:
            data = json.loads(html.unescape(script.strip()))
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type")
            if isinstance(item_type, list):
                is_product = any(str(value).lower() == "product" for value in item_type)
            else:
                is_product = str(item_type).lower() == "product"
            if is_product:
                products.append(item)
            for value in item.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
    return products


def products_from_jsonld(page_html: str, page_url: str, scraped_at: str) -> list[WebProduct]:
    rows = []
    source_label = source_label_for_url(page_url)
    for idx, product in enumerate(iter_jsonld_products(page_html), start=1):
        offers = product.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        name = clean_text(product.get("name"))
        price = parse_price(offers.get("price") if isinstance(offers, dict) else "")
        if not name or price is None:
            continue
        url = ""
        if isinstance(offers, dict):
            url = clean_text(offers.get("url"))
        url = url or clean_text(product.get("url")) or page_url
        brand = product.get("brand") or ""
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        rows.append(
            WebProduct(
                source=urlparse(page_url).netloc,
                source_label=source_label,
                category=infer_category(page_url, name),
                name=name,
                price_ars=price,
                url=urljoin(page_url, url),
                article=clean_text(product.get("sku") or product.get("mpn") or idx),
                brand=infer_brand(name, brand),
                availability=clean_text(offers.get("availability", "") if isinstance(offers, dict) else ""),
                page=1,
                scraped_at=scraped_at,
            )
        )
    return rows


def products_from_data_attrs(page_html: str, page_url: str, scraped_at: str) -> list[WebProduct]:
    rows = []
    seen: set[tuple[str, int]] = set()
    source_label = source_label_for_url(page_url)
    card_pattern = r"<[A-Za-z0-9]+\b(?=[^>]*\bdata-nombre=)(?=[^>]*\bdata-precio=)[^>]*>"
    attr_pattern = r"([A-Za-z0-9_-]+)\s*=\s*([\"'])(.*?)\2"
    for idx, match in enumerate(re.finditer(card_pattern, page_html, flags=re.IGNORECASE | re.DOTALL), start=1):
        attrs = {key.lower(): html.unescape(value) for key, _, value in re.findall(attr_pattern, match.group(0), flags=re.DOTALL)}
        name = clean_text(attrs.get("data-nombre"))
        price = parse_number(attrs.get("data-precio"))
        if not name or price is None:
            continue
        key = (name.lower(), price)
        if key in seen:
            continue
        seen.add(key)
        following_html = page_html[match.end() : match.end() + 2500]
        href = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\']', following_html, flags=re.IGNORECASE)
        rows.append(
            WebProduct(
                source=urlparse(page_url).netloc,
                source_label=source_label,
                category=infer_category(page_url, attrs.get("data-cat", ""), name),
                name=name,
                price_ars=price,
                url=urljoin(page_url, href.group(1) if href else page_url),
                article=clean_text(attrs.get("data-id") or idx),
                brand=infer_brand(name, attrs.get("data-marca", "")),
                availability="",
                page=1,
                scraped_at=scraped_at,
            )
        )
    return rows


def candidate_blocks(page_html: str) -> list[str]:
    blocks = re.findall(
        r"<(?P<tag>article|li|div)\b[^>]*(?:product|producto|item|card|catalog|listado)[^>]*>[\s\S]*?</(?P=tag)>",
        page_html,
        flags=re.IGNORECASE,
    )
    if blocks:
        return [match.group(0) if hasattr(match, "group") else match for match in []]
    return re.findall(r"<(?:article|li|div)\b[\s\S]{0,7000}?\$\s*[0-9][\s\S]{0,1800}?</(?:article|li|div)>", page_html, flags=re.IGNORECASE)


def product_blocks(page_html: str) -> list[str]:
    specific = re.findall(
        r'<div class="productos\b[\s\S]*?(?=<div class="productos\b|<div class="col-lg-12 mb-2 pull-right"|</div>\s*</div>\s*<style)',
        page_html,
        flags=re.IGNORECASE,
    )
    generic = re.findall(
        r"<(?:article|li|div)\b[^>]*(?:product|producto|card|item)[^>]*>[\s\S]{0,7000}?\$\s*[0-9][\s\S]{0,2000}?</(?:article|li|div)>",
        page_html,
        flags=re.IGNORECASE,
    )
    if specific:
        return specific
    return generic or re.findall(r"<a\b[\s\S]{0,2500}?\$\s*[0-9][\s\S]{0,1200}?</a>", page_html, flags=re.IGNORECASE)


def name_from_block(block: str) -> tuple[str, str]:
    anchor_matches = re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>', block, flags=re.IGNORECASE)
    best_name = ""
    best_url = ""
    for href, text in anchor_matches:
        name = clean_text(text)
        if len(name) > len(best_name):
            best_name = name
            best_url = href
    if best_name:
        return best_name, best_url
    title = re.search(r"<h[1-6]\b[^>]*>([\s\S]*?)</h[1-6]>", block, flags=re.IGNORECASE)
    return (clean_text(title.group(1)), "") if title else ("", "")


def products_from_html_blocks(page_html: str, source_url: str, scraped_at: str) -> list[WebProduct]:
    rows = []
    seen: set[tuple[str, int]] = set()
    source_label = source_label_for_url(source_url)
    for idx, block in enumerate(product_blocks(page_html), start=1):
        name, href = name_from_block(block)
        price = parse_price(block)
        if not name or price is None:
            continue
        key = (name.lower(), price)
        if key in seen:
            continue
        seen.add(key)
        article = re.search(r"(?:Art|SKU|Codigo|Código)\s*:?\s*([A-Za-z0-9._-]+)", clean_text(block), flags=re.IGNORECASE)
        rows.append(
            WebProduct(
                source=urlparse(source_url).netloc,
                source_label=source_label,
                category=infer_category(source_url, name),
                name=name,
                price_ars=price,
                url=urljoin(source_url, href or source_url),
                article=article.group(1) if article else str(idx),
                brand=infer_brand(name),
                availability="",
                page=1,
                scraped_at=scraped_at,
            )
        )
    return rows


def scrape_url(url: str) -> list[WebProduct]:
    netloc = urlparse(url).netloc.lower()
    if "compragamer.com" in netloc:
        products = scrape_compragamer(url)
        if products:
            return products
    if "maximus.com.ar" in netloc:
        products = scrape_maximus(url)
        if products:
            return products
    page_html = request_html(url)
    scraped_at = datetime.now().isoformat(timespec="seconds")
    products = products_from_data_attrs(page_html, url, scraped_at)
    if products and not has_truncated_ars_prices(products):
        return products
    products = products_from_jsonld(page_html, url, scraped_at)
    if products and not has_truncated_ars_prices(products):
        return products
    html_products = products_from_html_blocks(page_html, url, scraped_at)
    if html_products:
        return html_products
    return products


def scrape_urls(urls: list[str]) -> list[WebProduct]:
    products: list[WebProduct] = []
    for url in urls:
        products.extend(scrape_url(url))
    return products


def write_outputs(products: list[WebProduct], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"web_products_{timestamp}.json"
    csv_path = output_dir / f"web_products_{timestamp}.csv"
    rows = [asdict(product) for product in products]

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()) if rows else WebProduct.__annotations__.keys())
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path
