from __future__ import annotations

import argparse
import csv
import html
import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse


DEFAULT_CATEGORIES = {
    "mothers": "https://www.mexx.com.ar/productos-rubro/motherboards/",
    "procesadores": "https://www.mexx.com.ar/productos-rubro/procesadores/",
    "memorias": "https://www.mexx.com.ar/productos-rubro/memorias-ram/",
    "almacenamiento": "https://www.mexx.com.ar/productos-rubro/almacenamiento/",
    "placas_video": "https://www.mexx.com.ar/productos-rubro/placas-de-video/",
    "fuentes": "https://www.mexx.com.ar/productos-rubro/fuentes-de-poder/",
    "refrigeracion": "https://www.mexx.com.ar/productos-rubro/refrigeracion-pc/",
    "monitores": "https://www.mexx.com.ar/productos-rubro/monitores/",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


@dataclass
class MexxProduct:
    source: str
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
            "Referer": "https://www.mexx.com.ar/",
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


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def parse_price(value: str) -> int | None:
    value = clean_text(value)
    match = re.search(r"\$\s*([0-9\.\,]+)", value)
    if not match:
        return None
    number = match.group(1).replace(".", "").replace(",", "")
    return int(number) if number.isdigit() else None


def extract_max_page(page_html: str) -> int:
    pages = [int(match) for match in re.findall(r"enviarPagina\((\d+)\)", page_html)]
    return max(pages) if pages else 1


def extract_product_blocks(page_html: str) -> list[str]:
    return re.findall(r'<div class="productos\b[\s\S]*?(?=<div class="productos\b|<div class="col-lg-12 mb-2 pull-right"|</div>\s*</div>\s*<style)', page_html)


def parse_product(block: str, category: str, page: int, scraped_at: str) -> MexxProduct | None:
    title_match = re.search(r'<h4 class="card-title[\s\S]*?<a href="([^"]+)">([\s\S]*?)</a>', block)
    if not title_match:
        return None

    url = title_match.group(1)
    name = clean_text(title_match.group(2))
    article_match = re.search(r"Art:\s*([^<]+)", block)
    brand_match = re.search(r'fa-bookmark"></i>\s*([^<]+)</span>', block)
    availability_match = re.search(r'<div class="[^"]*\benstocklistado\b[^"]*">([\s\S]*?)</div>', block)
    price_match = re.search(r'<div class="price">([\s\S]*?)</div>', block)

    return MexxProduct(
        source="mexx",
        category=category,
        name=name,
        price_ars=parse_price(price_match.group(1) if price_match else ""),
        url=urljoin("https://www.mexx.com.ar/", url),
        article=clean_text(article_match.group(1)) if article_match else "",
        brand=clean_text(brand_match.group(1)) if brand_match else "",
        availability=clean_text(availability_match.group(1)) if availability_match else "",
        page=page,
        scraped_at=scraped_at,
    )


def page_url(category_url: str, page: int) -> str:
    if page == 1:
        return category_url
    separator = "&" if urlparse(category_url).query else "?"
    return f"{category_url}{separator}pagina={page}"


def load_categories(config_path: Path | None) -> dict[str, str]:
    if not config_path or not config_path.exists():
        return DEFAULT_CATEGORIES

    text = config_path.read_text(encoding="utf-8")
    categories: dict[str, str] = {}
    current_key: str | None = None
    for line in text.splitlines():
        category_match = re.match(r"\s{2}([a-zA-Z0-9_]+):\s*$", line)
        if category_match:
            current_key = category_match.group(1)
            continue
        url_match = re.match(r"\s{4}url:\s*(\S+)\s*$", line)
        if current_key and url_match:
            categories[current_key] = url_match.group(1)
    return categories or DEFAULT_CATEGORIES


def scrape_category(category: str, url: str, delay: tuple[float, float], limit_pages: int | None) -> list[MexxProduct]:
    first_html = request_html(url)
    max_page = extract_max_page(first_html)
    if limit_pages:
        max_page = min(max_page, limit_pages)

    products: list[MexxProduct] = []
    scraped_at = datetime.now().isoformat(timespec="seconds")
    for page in range(1, max_page + 1):
        html_text = first_html if page == 1 else request_html(page_url(url, page))
        for block in extract_product_blocks(html_text):
            product = parse_product(block, category, page, scraped_at)
            if product:
                products.append(product)
        if page < max_page:
            time.sleep(random.uniform(*delay))
    return products


def write_outputs(products: list[MexxProduct], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"mexx_products_{timestamp}.json"
    csv_path = output_dir / f"mexx_products_{timestamp}.csv"
    rows = [asdict(product) for product in products]

    json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()) if rows else MexxProduct.__annotations__.keys())
        writer.writeheader()
        writer.writerows(rows)
    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrapea categorías de Mexx.")
    parser.add_argument("--config", type=Path, default=Path("config/mexx_categories.yaml"))
    parser.add_argument("--out", type=Path, default=Path("data/raw/mexx"))
    parser.add_argument("--category", action="append", help="Categoría puntual a scrapear. Puede repetirse.")
    parser.add_argument("--limit-pages", type=int, default=None, help="Límite opcional de páginas por categoría.")
    parser.add_argument("--min-delay", type=float, default=0.6)
    parser.add_argument("--max-delay", type=float, default=1.8)
    args = parser.parse_args()

    categories = load_categories(args.config)
    if args.category:
        requested = set(args.category)
        categories = {key: value for key, value in categories.items() if key in requested}
        missing = requested - set(categories)
        if missing:
            raise SystemExit(f"Categorías no configuradas: {', '.join(sorted(missing))}")

    all_products: list[MexxProduct] = []
    for category, url in categories.items():
        products = scrape_category(category, url, (args.min_delay, args.max_delay), args.limit_pages)
        all_products.extend(products)
        print(f"{category}: {len(products)} productos")

    json_path, csv_path = write_outputs(all_products, args.out)
    print(f"Total: {len(all_products)} productos")
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")


if __name__ == "__main__":
    main()
