from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd


NB_CATEGORY_GROUPS = {
    "PROCESADORES": "procesadores",
    "MONITORES": "monitores",
    "PLACA DE VIDEO": "placas_video",
    "MOTHER ASUS": "mothers",
    "MOTHER GIGABYTE": "mothers",
    "MOTHER ASROCK": "mothers",
    "MEMORIAS": "memorias",
    "DISCOS SSD": "almacenamiento",
    "FUENTES": "fuentes",
    "COOLERS": "refrigeracion",
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
    "CPU",
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
    "GHZ",
    "HDMI",
    "HEATSINK",
    "INTEL",
    "KIT",
    "LED",
    "MEMORIA",
    "MONITOR",
    "MOTHER",
    "MOTHERBOARD",
    "NVME",
    "PARA",
    "PCI",
    "PCIE",
    "PLUS",
    "PRO",
    "PROCESADOR",
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
        "COOLER MASTER": "COOLERMASTER",
        "GIGABYTE TECHNOLOGY": "GIGABYTE",
        "KINGSTON TECHNOLOGY": "KINGSTON",
        "WESTERN DIGITAL": "WD",
        "WESTERN DIGITAL WD": "WD",
        "MSI": "MSI",
        "Msi": "MSI",
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


def cpu_key(name: str, brand_hint: str = "") -> str:
    text = normalize_text(f"{brand_hint} {name}")
    text = text.replace("CORE ULTRA", "COREULTRA")
    text = re.sub(r"\b([0-9]{4,5}[A-Z]*)\s+3D\b", r"\g<1>3D", text)

    amd = re.search(r"\bRYZEN\s*([3579])?\s*([0-9]{4,5}(?:[A-Z]{0,3}|3D|X3D|XT|X|F|G|GE|GT)*)\b", text)
    if amd:
        return f"procesadores|AMD|RYZEN|{amd.group(1) or ''}|{amd.group(2)}"

    core_ultra = re.search(r"\bCOREULTRA\s*([3579])\s*([0-9]{3,4}[A-Z]*)\b", text)
    if core_ultra:
        return f"procesadores|INTEL|COREULTRA|{core_ultra.group(1)}|{core_ultra.group(2)}"

    intel_core = re.search(r"\bCORE\s*I([3579])\s*[- ]?\s*([0-9]{4,5}[A-Z]*)\b", text)
    if intel_core:
        return f"procesadores|INTEL|COREI|{intel_core.group(1)}|{intel_core.group(2)}"

    pentium = re.search(r"\bPENTIUM(?:\s+GOLD)?\s*([A-Z]?[0-9]{4,5}[A-Z]*)\b", text)
    if pentium:
        return f"procesadores|INTEL|PENTIUM||{pentium.group(1)}"

    celeron = re.search(r"\bCELERON\s*([A-Z]?[0-9]{4,5}[A-Z]*)\b", text)
    if celeron:
        return f"procesadores|INTEL|CELERON||{celeron.group(1)}"

    return ""


def model_key(group: str, name: str, brand: str = "") -> str:
    if group == "procesadores":
        return cpu_key(name, brand)

    text = normalize_text(name)
    brand_token = compact_brand(brand)
    parts = [group, brand_token]

    if group == "memorias":
        capacity = re.search(r"\b([0-9]{1,3}GB)\b", text)
        ddr = re.search(r"\b(DDR[345])\b", text)
        speed = re.search(r"\b([0-9]{4,5})(?:MHZ|MT/S|MTS)?\b", text)
        latency = re.search(r"\b(?:CL|C)\s*([0-9]{2})\b", text)
        return "|".join(
            [
                group,
                capacity.group(1) if capacity else "",
                ddr.group(1) if ddr else "",
                speed.group(1) if speed else "",
                f"CL{latency.group(1)}" if latency else "",
            ]
        )

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

    if group == "almacenamiento":
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

    if group == "refrigeracion":
        fan_size = re.search(r"\b([0-9]{2,3}MM)\b", text)
        model_tokens = tokens_for_match(name, brand)[:6]
        return "|".join(parts + [fan_size.group(1) if fan_size else "", *model_tokens])

    return "|".join(parts + tokens_for_match(name, brand)[:6])


def fuzzy_score(left: str, right: str) -> int:
    plain = SequenceMatcher(None, str(left), str(right)).ratio()
    left_tokens = " ".join(sorted(set(str(left).split())))
    right_tokens = " ".join(sorted(set(str(right).split())))
    token_sorted = SequenceMatcher(None, left_tokens, right_tokens).ratio()
    return round(max(plain, token_sorted) * 100)


def load_nb(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, sep=";", dtype=str, encoding="utf-8", engine="python")
    df["nb_categoria"] = df["CATEGORIA"].fillna("")
    df = df[df["nb_categoria"].isin(NB_CATEGORY_GROUPS)].copy()
    df["grupo"] = df["nb_categoria"].map(NB_CATEGORY_GROUPS)
    df["nb_codigo"] = df["CODIGO"].fillna("")
    df["nb_id_fabricante"] = df["ID FABRICANTE"].fillna("")
    df["nb_nombre"] = df["DETALLE"].fillna("")
    df["nb_marca"] = df["MARCA"].fillna("")
    df["nb_brand_key"] = df["nb_marca"].map(compact_brand)
    df["nb_precio_ars"] = pd.to_numeric(df["PRECIO PESOS CON IVA"], errors="coerce")
    df["nb_stock"] = pd.to_numeric(df["STOCK"], errors="coerce").fillna(0).astype(int)
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


def load_mexx(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
    columns = [
        "source_label",
        "grupo",
        "match_key",
        "mexx_articulo",
        "mexx_nombre",
        "mexx_marca",
        "mexx_brand_key",
        "mexx_precio_ars",
        "mexx_disponibilidad",
        "mexx_url",
        "mexx_normalizado",
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)
    df["source_label"] = df.get("source_label", "").fillna("")
    df["grupo"] = df["category"].fillna("")
    df["mexx_nombre"] = df["name"].fillna("")
    df["mexx_marca"] = df["brand"].fillna("")
    df["mexx_brand_key"] = df["mexx_marca"].map(compact_brand)
    df["mexx_precio_ars"] = pd.to_numeric(df["price_ars"], errors="coerce")
    df["mexx_articulo"] = df["article"].fillna("")
    df["mexx_disponibilidad"] = df["availability"].fillna("")
    df["mexx_url"] = df["url"].fillna("")
    df["mexx_normalizado"] = df.apply(lambda row: " ".join(tokens_for_match(row["mexx_nombre"], row["mexx_marca"])), axis=1)
    df["match_key"] = df.apply(lambda row: model_key(row["grupo"], row["mexx_nombre"], row["mexx_marca"]), axis=1)
    return df[columns]


def best_match(nb_row: pd.Series, candidates: pd.DataFrame, used_indexes: set[int]) -> tuple[int | None, int]:
    available = candidates[~candidates.index.isin(used_indexes)]
    if available.empty:
        return None, 0

    if nb_row["grupo"] != "memorias":
        nb_brand = nb_row.get("nb_brand_key", "")
        same_brand = available[available["mexx_brand_key"].eq(nb_brand)] if nb_brand else available
        available = same_brand if not same_brand.empty else available.iloc[0:0]
        if available.empty:
            return None, 0

    exact = available[available["match_key"].eq(nb_row["match_key"])]
    if not exact.empty and nb_row["match_key"]:
        idx = exact.index[0]
        return idx, fuzzy_score(nb_row["nb_normalizado"], available.loc[idx, "mexx_normalizado"])

    # These families have small model-name differences that can mean different products.
    # Keep them strict unless the structured key matches.
    if nb_row["grupo"] in {"procesadores", "memorias", "mothers", "placas_video", "almacenamiento"}:
        return None, 0

    scores = available["mexx_normalizado"].map(lambda value: fuzzy_score(nb_row["nb_normalizado"], value))
    if scores.empty:
        return None, 0
    idx = scores.idxmax()
    score = int(scores.loc[idx])
    thresholds = {
        "procesadores": 70,
        "placas_video": 78,
        "mothers": 76,
        "almacenamiento": 66,
        "memorias": 68,
        "fuentes": 70,
        "monitores": 72,
        "refrigeracion": 72,
    }
    return (idx, score) if score >= thresholds.get(nb_row["grupo"], 74) else (None, score)


def merged_row(status: str, nb_row: pd.Series | None, mexx_row: pd.Series | None, score: int | None) -> dict:
    group = nb_row["grupo"] if nb_row is not None else mexx_row["grupo"]
    return {
        "estado": status,
        "grupo": group,
        "producto_normalizado": nb_row["match_key"] if nb_row is not None else mexx_row["match_key"],
        "match_score": score,
        "nb_categoria": nb_row["nb_categoria"] if nb_row is not None else None,
        "nb_codigo": nb_row["nb_codigo"] if nb_row is not None else None,
        "nb_id_fabricante": nb_row["nb_id_fabricante"] if nb_row is not None else None,
        "nb_nombre": nb_row["nb_nombre"] if nb_row is not None else None,
        "nb_marca": nb_row["nb_marca"] if nb_row is not None else None,
        "nb_precio_ars": nb_row["nb_precio_ars"] if nb_row is not None else None,
        "nb_stock": nb_row["nb_stock"] if nb_row is not None else None,
        "mexx_articulo": mexx_row["mexx_articulo"] if mexx_row is not None else None,
        "mexx_nombre": mexx_row["mexx_nombre"] if mexx_row is not None else None,
        "mexx_marca": mexx_row["mexx_marca"] if mexx_row is not None else None,
        "mexx_precio_ars": mexx_row["mexx_precio_ars"] if mexx_row is not None else None,
        "mexx_disponibilidad": mexx_row["mexx_disponibilidad"] if mexx_row is not None else None,
        "mexx_url": mexx_row["mexx_url"] if mexx_row is not None else None,
    }


def build_comparison(nb: pd.DataFrame, mexx: pd.DataFrame) -> pd.DataFrame:
    rows = []
    used_mexx: set[int] = set()

    for _, nb_row in nb.iterrows():
        candidates = mexx[mexx["grupo"].eq(nb_row["grupo"])]
        mexx_idx, score = best_match(nb_row, candidates, used_mexx)
        if mexx_idx is not None:
            used_mexx.add(mexx_idx)
            rows.append(merged_row("repetido", nb_row, mexx.loc[mexx_idx], score))
        else:
            rows.append(merged_row("solo_distribuidor", nb_row, None, None))

    for mexx_idx, mexx_row in mexx[~mexx.index.isin(used_mexx)].iterrows():
        rows.append(merged_row("solo_mexx", None, mexx_row, None))

    result = pd.DataFrame(rows)
    result["diferencia_ars"] = pd.to_numeric(result["mexx_precio_ars"], errors="coerce") - pd.to_numeric(result["nb_precio_ars"], errors="coerce")
    result["diferencia_pct"] = (result["diferencia_ars"] / pd.to_numeric(result["nb_precio_ars"], errors="coerce")) * 100
    sort_profit = pd.to_numeric(result["diferencia_ars"], errors="coerce").fillna(float("-inf"))
    return (
        result.assign(_sort_profit=sort_profit)
        .sort_values(["_sort_profit", "grupo", "estado", "producto_normalizado"], ascending=[False, True, True, True])
        .drop(columns="_sort_profit")
        .fillna("-")
    )


def write_report(result: pd.DataFrame, nb: pd.DataFrame, mexx: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"comparativa_nb_vs_mexx_{timestamp}.csv"
    xlsx_path = output_dir / f"comparativa_nb_vs_mexx_{timestamp}.xlsx"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name="resumen", index=False)
        for group, group_df in result.groupby("grupo", sort=True):
            group_df.to_excel(writer, sheet_name=group[:31], index=False)
        nb.to_excel(writer, sheet_name="distribuidor_nb", index=False)
        mexx.to_excel(writer, sheet_name="mexx", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for col in worksheet.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                worksheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 64)
    return csv_path, xlsx_path


def newest_mexx_csv(path: Path) -> Path:
    files = sorted(path.glob("mexx_products_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No encontré archivos mexx_products_*.csv en {path}")
    return files[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara NB vs productos scrapeados de Mexx.")
    parser.add_argument("--nb-csv", required=True, type=Path)
    parser.add_argument("--mexx-csv", type=Path, default=None)
    parser.add_argument("--mexx-dir", type=Path, default=Path("data/raw/mexx"))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()

    mexx_csv = args.mexx_csv or newest_mexx_csv(args.mexx_dir)
    nb = load_nb(args.nb_csv)
    mexx = load_mexx(mexx_csv)
    result = build_comparison(nb, mexx)
    csv_path, xlsx_path = write_report(result, nb, mexx, args.out)

    print(f"NB: {len(nb)} productos")
    print(f"Mexx: {len(mexx)} productos ({mexx_csv})")
    print(result.groupby(["grupo", "estado"]).size().unstack(fill_value=0).to_string())
    print(f"CSV: {csv_path}")
    print(f"Excel: {xlsx_path}")


if __name__ == "__main__":
    main()
