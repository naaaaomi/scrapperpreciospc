from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.request
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


COMPRAGAMER_PRODUCTS_URL = "https://static.compragamer.com/productos"
COMPRAGAMER_BRANDS_URL = "https://static.compragamer.com/marcas"
COMPRAGAMER_SUBCATEGORIES = {
    5: ("monitores", "compragamer_monitores"),
    6: ("placas_video", "compragamer_geforce"),
    62: ("placas_video", "compragamer_radeon"),
    26: ("mothers", "compragamer_mothers_amd"),
    49: ("mothers", "compragamer_mothers_intel"),
    15: ("memorias", "compragamer_memorias"),
    81: ("discos_ssd", "compragamer_ssd"),
    34: ("fuentes", "compragamer_fuentes"),
    35: ("coolers", "compragamer_coolers_fan"),
}

NB_CATEGORY_GROUPS = {
    "MONITORES": "monitores",
    "PLACA DE VIDEO": "placas_video",
    "MOTHER ASUS": "mothers",
    "MOTHER GIGABYTE": "mothers",
    "MOTHER ASROCK": "mothers",
    "MEMORIAS": "memorias",
    "DISCOS SSD": "discos_ssd",
    "FUENTES": "fuentes",
    "COOLERS": "coolers",
}

GENERIC_WORDS = {
    "AMD",
    "ARGB",
    "ATX",
    "BLACK",
    "BLANCO",
    "BLUE",
    "COOLER",
    "CON",
    "DDR4",
    "DDR5",
    "DISCO",
    "DUAL",
    "FAN",
    "FUENTE",
    "GAMER",
    "GAMING",
    "GB",
    "GEFORCE",
    "GIGABIT",
    "HDMI",
    "HEATSINK",
    "INTEL",
    "KIT",
    "LED",
    "MEMORIA",
    "MONITOR",
    "MOTHER",
    "NVME",
    "PARA",
    "PCI",
    "PCIE",
    "PLUS",
    "PRO",
    "RADEON",
    "RGB",
    "SATA",
    "SIN",
    "SSD",
    "USB",
    "VGA",
    "VIDEO",
    "WIFI",
    "WHITE",
}


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    value = strip_accents(str(value)).upper()
    value = value.replace("NVIDIA", "GEFORCE")
    value = re.sub(r"(\d+)\s*TB\b", lambda m: f"{int(m.group(1)) * 1000}GB", value)
    value = re.sub(r"(\d+)\s*MM\b", r"\1MM", value)
    value = re.sub(r"(\d+)\s*W\b", r"\1W", value)
    value = re.sub(r"(\d+)\s*HZ\b", r"\1HZ", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compact_brand(value: str) -> str:
    brand = normalize_text(value)
    aliases = {
        "A DATA": "ADATA",
        "WESTERN DIGITAL": "WD",
        "COOLER MASTER": "COOLERMASTER",
        "THERMALTAKE": "THERMALTAKE",
        "GIGABYTE TECHNOLOGY": "GIGABYTE",
        "KINGSTON TECHNOLOGY": "KINGSTON",
        "INNO3D": "INNO3D",
        "PALIT": "PALIT",
    }
    return aliases.get(brand, brand.replace(" ", ""))


def tokens_for_match(name: str, brand: str = "") -> list[str]:
    brand_token = compact_brand(brand)
    tokens = normalize_text(name).split()
    return [
        token
        for token in tokens
        if token not in GENERIC_WORDS
        and token != brand_token
        and len(token) > 1
        and not token.endswith("MB")
    ]


def model_key(group: str, name: str, brand: str = "") -> str:
    text = normalize_text(name)
    brand_token = compact_brand(brand)
    parts = [group, brand_token]

    if group == "placas_video":
        gpu = re.search(r"\b(RTX|GTX|RX)\s*([0-9]{3,5})(?:\s*(TI|SUPER|XT|XTX))?\b", text)
        memory = re.search(r"\b([0-9]{1,2}GB)\b", text)
        model_tokens = tokens_for_match(name, brand)[:6]
        if gpu:
            return "|".join(parts + [gpu.group(1), gpu.group(2), gpu.group(3) or "", memory.group(1) if memory else "", *model_tokens])

    if group == "mothers":
        chipset = re.search(r"\b([ABHXZ][0-9]{3,4}[A-Z]?)\b", text)
        socket = re.search(r"\b(AM4|AM5|S1700|1700|S1851|1851)\b", text)
        model_tokens = tokens_for_match(name, brand)[:5]
        if chipset:
            return "|".join(parts + [chipset.group(1), socket.group(1) if socket else "", *model_tokens])

    if group == "memorias":
        capacity = re.search(r"\b([0-9]{1,3}GB)\b", text)
        ddr = re.search(r"\b(DDR[345])\b", text)
        speed = re.search(r"\b([0-9]{4,5})(?:MHZ|MT/S|MTS)?\b", text)
        latency = re.search(r"\b(?:CL|C)\s*([0-9]{2})\b", text)
        # For RAM, the commercial opportunity depends mostly on comparable specs.
        # Brand is intentionally ignored per user request.
        return "|".join(
            [
                group,
                capacity.group(1) if capacity else "",
                ddr.group(1) if ddr else "",
                speed.group(1) if speed else "",
                f"CL{latency.group(1)}" if latency else "",
            ]
        )

    if group == "discos_ssd":
        capacity = re.search(r"\b([0-9]{3,5}GB)\b", text)
        model_tokens = tokens_for_match(name, brand)[:5]
        return "|".join(parts + [capacity.group(1) if capacity else "", *model_tokens])

    if group == "fuentes":
        watts = re.search(r"\b([0-9]{3,4}W)\b", text)
        rating = re.search(r"\b(80\s*PLUS\s*(BRONZE|GOLD|PLATINUM|WHITE)?)\b", text)
        model_tokens = tokens_for_match(name, brand)[:5]
        return "|".join(parts + [watts.group(1) if watts else "", normalize_text(rating.group(1)) if rating else "", *model_tokens])

    if group == "monitores":
        size = re.search(r"\b([0-9]{2}(?:\.[0-9])?)\s*(?:PULGADAS|PULG|INCH|IN)?\b", text)
        hz = re.search(r"\b([0-9]{2,3}HZ)\b", text)
        model_tokens = tokens_for_match(name, brand)[:6]
        return "|".join(parts + [size.group(1) if size else "", hz.group(1) if hz else "", *model_tokens])

    if group == "coolers":
        fan_size = re.search(r"\b([0-9]{2,3}MM)\b", text)
        model_tokens = tokens_for_match(name, brand)[:6]
        return "|".join(parts + [fan_size.group(1) if fan_size else "", *model_tokens])

    return "|".join(parts + tokens_for_match(name, brand)[:6])


def fuzzy_score(left: str, right: str) -> int:
    plain = SequenceMatcher(None, left, right).ratio()
    left_tokens = " ".join(sorted(set(str(left).split())))
    right_tokens = " ".join(sorted(set(str(right).split())))
    token_sorted = SequenceMatcher(None, left_tokens, right_tokens).ratio()
    return round(max(plain, token_sorted) * 100)


def product_slug(name: str, product_id: int) -> str:
    slug = strip_accents(name)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    return f"https://compragamer.com/producto/{slug}_{product_id}"


def load_distributor(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, sep=";", dtype=str, encoding="utf-8", engine="python")
    df["nb_categoria"] = df["CATEGORIA"].fillna("")
    df = df[df["nb_categoria"].isin(NB_CATEGORY_GROUPS)].copy()
    df["grupo"] = df["nb_categoria"].map(NB_CATEGORY_GROUPS)
    df["nb_codigo"] = df["CODIGO"].fillna("")
    df["nb_id_fabricante"] = df["ID FABRICANTE"].fillna("")
    df["nb_nombre"] = df["DETALLE"].fillna("")
    df["nb_marca"] = df["MARCA"].fillna("")
    df["nb_stock"] = pd.to_numeric(df["STOCK"], errors="coerce").fillna(0).astype(int)
    df["nb_precio_ars"] = pd.to_numeric(df["PRECIO PESOS CON IVA"], errors="coerce")
    df["nb_brand_key"] = df["nb_marca"].map(compact_brand)
    df["nb_normalizado"] = df.apply(lambda row: " ".join(tokens_for_match(row["nb_nombre"], row["nb_marca"])), axis=1)
    df["match_key"] = df.apply(lambda row: model_key(row["grupo"], row["nb_nombre"], row["nb_marca"]), axis=1)
    return df[
        [
            "grupo",
            "match_key",
            "nb_categoria",
            "nb_codigo",
            "nb_id_fabricante",
            "nb_nombre",
            "nb_marca",
            "nb_brand_key",
            "nb_precio_ars",
            "nb_stock",
            "nb_normalizado",
        ]
    ]


def load_compragamer() -> pd.DataFrame:
    with urllib.request.urlopen(COMPRAGAMER_PRODUCTS_URL, timeout=30) as response:
        data = json.load(response)
    with urllib.request.urlopen(COMPRAGAMER_BRANDS_URL, timeout=30) as response:
        brands_data = json.load(response)
    brands = {brand.get("id"): brand.get("nombre", "") for brand in brands_data}

    rows = []
    for item in data:
        subcategory = item.get("id_subcategoria")
        price = item.get("precioEspecial") or 0
        if subcategory not in COMPRAGAMER_SUBCATEGORIES or price <= 0:
            continue
        group, source = COMPRAGAMER_SUBCATEGORIES[subcategory]
        name = (item.get("nombre") or "").strip()
        product_id = item.get("id_producto")
        brand = brands.get(item.get("id_marca"), "")
        rows.append(
            {
                "grupo": group,
                "match_key": model_key(group, name, brand),
                "cg_id": product_id,
                "cg_cate": subcategory,
                "cg_fuente": source,
                "cg_nombre": name,
                "cg_marca": brand,
                "cg_brand_key": compact_brand(brand),
                "cg_precio_ars": float(price),
                "cg_disponible": bool(item.get("vendible")),
                "cg_url": product_slug(name, product_id) + f"?cate={subcategory}",
                "cg_normalizado": " ".join(tokens_for_match(name, brand)),
            }
        )
    return pd.DataFrame(rows)


def best_match(nb_row: pd.Series, candidates: pd.DataFrame, used_indexes: set[int]) -> tuple[int | None, int]:
    available = candidates[~candidates.index.isin(used_indexes)]
    if available.empty:
        return None, 0

    if nb_row["grupo"] != "memorias":
        nb_brand = nb_row.get("nb_brand_key", "")
        same_brand = available[available["cg_brand_key"].eq(nb_brand)] if nb_brand else available
        available = same_brand if not same_brand.empty else available.iloc[0:0]
        if available.empty:
            return None, 0

    exact = available[available["match_key"].eq(nb_row["match_key"])]
    if not exact.empty:
        idx = exact.index[0]
        return idx, fuzzy_score(nb_row["nb_normalizado"], candidates.loc[idx, "cg_normalizado"])

    scores = available["cg_normalizado"].map(lambda value: fuzzy_score(nb_row["nb_normalizado"], value))
    if scores.empty:
        return None, 0
    idx = scores.idxmax()
    score = int(scores.loc[idx])
    thresholds = {
        "placas_video": 78,
        "mothers": 76,
        "discos_ssd": 66,
        "memorias": 68,
        "fuentes": 70,
        "monitores": 72,
        "coolers": 72,
    }
    threshold = thresholds.get(nb_row["grupo"], 74)
    return (idx, score) if score >= threshold else (None, score)


def build_comparison(nb: pd.DataFrame, cg: pd.DataFrame) -> pd.DataFrame:
    rows = []
    used_cg: set[int] = set()

    for _, nb_row in nb.iterrows():
        candidates = cg[cg["grupo"].eq(nb_row["grupo"])]
        cg_idx, score = best_match(nb_row, candidates, used_cg)
        if cg_idx is not None:
            used_cg.add(cg_idx)
            cg_row = cg.loc[cg_idx]
            rows.append(merged_row("repetido", nb_row, cg_row, score))
        else:
            rows.append(merged_row("solo_distribuidor", nb_row, None, None))

    for cg_idx, cg_row in cg[~cg.index.isin(used_cg)].iterrows():
        rows.append(merged_row("solo_compragamer", None, cg_row, None))

    result = pd.DataFrame(rows)
    result["diferencia_ars"] = pd.to_numeric(result["cg_precio_ars"], errors="coerce") - pd.to_numeric(result["nb_precio_ars"], errors="coerce")
    result["diferencia_pct"] = (result["diferencia_ars"] / pd.to_numeric(result["nb_precio_ars"], errors="coerce")) * 100
    sort_profit = pd.to_numeric(result["diferencia_ars"], errors="coerce").fillna(float("-inf"))
    result = (
        result.assign(_sort_profit=sort_profit)
        .sort_values(["_sort_profit", "grupo", "estado", "producto_normalizado"], ascending=[False, True, True, True])
        .drop(columns="_sort_profit")
    )
    return result.fillna("-")


def merged_row(status: str, nb_row: pd.Series | None, cg_row: pd.Series | None, score: int | None) -> dict:
    group = nb_row["grupo"] if nb_row is not None else cg_row["grupo"]
    return {
        "estado": status,
        "grupo": group,
        "producto_normalizado": nb_row["match_key"] if nb_row is not None else cg_row["match_key"],
        "match_score": score,
        "nb_categoria": nb_row["nb_categoria"] if nb_row is not None else None,
        "nb_codigo": nb_row["nb_codigo"] if nb_row is not None else None,
        "nb_id_fabricante": nb_row["nb_id_fabricante"] if nb_row is not None else None,
        "nb_nombre": nb_row["nb_nombre"] if nb_row is not None else None,
        "nb_marca": nb_row["nb_marca"] if nb_row is not None else None,
        "nb_brand_key": nb_row["nb_brand_key"] if nb_row is not None else None,
        "nb_precio_ars": nb_row["nb_precio_ars"] if nb_row is not None else None,
        "nb_stock": nb_row["nb_stock"] if nb_row is not None else None,
        "cg_fuente": cg_row["cg_fuente"] if cg_row is not None else None,
        "cg_cate": cg_row["cg_cate"] if cg_row is not None else None,
        "cg_nombre": cg_row["cg_nombre"] if cg_row is not None else None,
        "cg_marca": cg_row["cg_marca"] if cg_row is not None else None,
        "cg_precio_ars": cg_row["cg_precio_ars"] if cg_row is not None else None,
        "cg_disponible": cg_row["cg_disponible"] if cg_row is not None else None,
        "cg_url": cg_row["cg_url"] if cg_row is not None else None,
    }


def write_report(result: pd.DataFrame, nb: pd.DataFrame, cg: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"comparativa_categorias_compragamer_{timestamp}.csv"
    xlsx_path = output_dir / f"comparativa_categorias_compragamer_{timestamp}.xlsx"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name="resumen", index=False)
        for group, group_df in result.groupby("grupo", sort=True):
            group_df.to_excel(writer, sheet_name=group[:31], index=False)
        nb.to_excel(writer, sheet_name="distribuidor_nb", index=False)
        cg.to_excel(writer, sheet_name="compragamer", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for col in worksheet.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                worksheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 64)

    return csv_path, xlsx_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara categorías NB vs Compragamer.")
    parser.add_argument("--csv", required=True, type=Path, help="Ruta al CSV de lista de precios NB.")
    parser.add_argument("--out", default=Path("reports"), type=Path, help="Directorio de reportes.")
    args = parser.parse_args()

    nb = load_distributor(args.csv)
    cg = load_compragamer()
    result = build_comparison(nb, cg)
    csv_path, xlsx_path = write_report(result, nb, cg, args.out)

    print(f"Distribuidor NB: {len(nb)} productos")
    print(f"Compragamer: {len(cg)} productos")
    print(result.groupby(["grupo", "estado"]).size().unstack(fill_value=0).to_string())
    print(f"CSV: {csv_path}")
    print(f"Excel: {xlsx_path}")


if __name__ == "__main__":
    main()
