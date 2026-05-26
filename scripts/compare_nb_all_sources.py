from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from compare_mexx_nb import (  # noqa: E402
    best_match,
    compact_brand,
    load_mexx,
    load_nb,
    newest_mexx_csv,
    tokens_for_match,
)


COMPRAGAMER_PRODUCTS_URL = "https://static.compragamer.com/productos"
COMPRAGAMER_BRANDS_URL = "https://static.compragamer.com/marcas"
COMPRAGAMER_CATEGORIES = {
    27: ("procesadores", "cg"),
    48: ("procesadores", "cg"),
    5: ("monitores", "cg"),
    6: ("placas_video", "cg"),
    62: ("placas_video", "cg"),
    26: ("mothers", "cg"),
    49: ("mothers", "cg"),
    15: ("memorias", "cg"),
    81: ("almacenamiento", "cg"),
    34: ("fuentes", "cg"),
    35: ("refrigeracion", "cg"),
}


def product_slug(name: str, product_id: int) -> str:
    normalized = unicodedata.normalize("NFKD", name)
    slug = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    return f"https://compragamer.com/producto/{slug}_{product_id}"


def load_compragamer() -> pd.DataFrame:
    with urllib.request.urlopen(COMPRAGAMER_PRODUCTS_URL, timeout=30) as response:
        products = json.load(response)
    with urllib.request.urlopen(COMPRAGAMER_BRANDS_URL, timeout=30) as response:
        brands_data = json.load(response)

    brands = {brand.get("id"): brand.get("nombre", "") for brand in brands_data}
    rows = []
    for item in products:
        cate = item.get("id_subcategoria")
        price = item.get("precioEspecial") or 0
        if cate not in COMPRAGAMER_CATEGORIES or price <= 0:
            continue

        group, source_label = COMPRAGAMER_CATEGORIES[cate]
        name = (item.get("nombre") or "").strip()
        brand = brands.get(item.get("id_marca"), "")
        product_id = item.get("id_producto")
        rows.append(
            {
                "source_label": source_label,
                "grupo": group,
                "match_key": "",
                "cg_id": product_id,
                "cg_nombre": name,
                "cg_marca": brand,
                "cg_brand_key": compact_brand(brand),
                "cg_precio_ars": float(price),
                "cg_disponible": bool(item.get("vendible")),
                "cg_url": product_slug(name, product_id) + f"?cate={cate}",
                "cg_normalizado": " ".join(tokens_for_match(name, brand)),
            }
        )
    return pd.DataFrame(rows)


def web_as_source(web: pd.DataFrame) -> pd.DataFrame:
    renamed = web.rename(
        columns={
            "mexx_articulo": "src_articulo",
            "mexx_nombre": "src_nombre",
            "mexx_marca": "src_marca",
            "mexx_brand_key": "src_brand_key",
            "mexx_precio_ars": "src_precio_ars",
            "mexx_disponibilidad": "src_disponibilidad",
            "mexx_url": "src_url",
            "mexx_normalizado": "src_normalizado",
        }
    ).copy()
    renamed["source_label"] = renamed.get("source_label", "").fillna("web")
    return renamed


def compragamer_as_source(cg: pd.DataFrame) -> pd.DataFrame:
    renamed = cg.rename(
        columns={
            "cg_id": "src_articulo",
            "cg_nombre": "src_nombre",
            "cg_marca": "src_marca",
            "cg_brand_key": "src_brand_key",
            "cg_precio_ars": "src_precio_ars",
            "cg_disponible": "src_disponibilidad",
            "cg_url": "src_url",
            "cg_normalizado": "src_normalizado",
        }
    ).copy()
    renamed["source_label"] = renamed.get("source_label", "").fillna("cg")
    return renamed


def best_match_source(nb_row: pd.Series, candidates: pd.DataFrame, used_indexes: set[int]) -> tuple[int | None, int]:
    source_candidates = candidates.rename(
        columns={
            "src_brand_key": "mexx_brand_key",
            "src_normalizado": "mexx_normalizado",
        }
    )
    return best_match(nb_row, source_candidates, used_indexes)


def match_source(nb: pd.DataFrame, source: pd.DataFrame, prefix: str) -> tuple[dict[int, dict], pd.DataFrame]:
    matches: dict[int, dict] = {}
    used: set[int] = set()

    for nb_idx, nb_row in nb.iterrows():
        candidates = source[source["grupo"].eq(nb_row["grupo"])]
        src_idx, score = best_match_source(nb_row, candidates, used)
        if src_idx is None:
            continue
        used.add(src_idx)
        src_row = source.loc[src_idx]
        price_diff = src_row["src_precio_ars"] - nb_row["nb_precio_ars"]
        pct_diff = (price_diff / nb_row["nb_precio_ars"]) * 100 if nb_row["nb_precio_ars"] else None
        matches[nb_idx] = {
            f"{prefix}_match_score": score,
            f"{prefix}_articulo": src_row.get("src_articulo"),
            f"{prefix}_nombre": src_row.get("src_nombre"),
            f"{prefix}_marca": src_row.get("src_marca"),
            f"{prefix}_precio_ars": src_row.get("src_precio_ars"),
            f"{prefix}_disponibilidad": src_row.get("src_disponibilidad"),
            f"{prefix}_diferencia_ars": price_diff,
            f"{prefix}_diferencia_pct": pct_diff,
            f"{prefix}_url": src_row.get("src_url"),
        }

    unmatched = source[~source.index.isin(used)].copy()
    return matches, unmatched


def combine_sources(web: pd.DataFrame, cg: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    source_columns = [
        "source_label",
        "grupo",
        "match_key",
        "src_articulo",
        "src_nombre",
        "src_marca",
        "src_brand_key",
        "src_precio_ars",
        "src_disponibilidad",
        "src_url",
        "src_normalizado",
    ]
    source_frames: dict[str, pd.DataFrame] = {}
    if web is not None and not web.empty:
        prepared_web = web_as_source(web)
        for label, label_df in prepared_web.groupby("source_label", sort=False):
            source_frames[label] = label_df.copy()
    if cg is not None and not cg.empty:
        prepared_cg = compragamer_as_source(cg)
        for label, label_df in prepared_cg.groupby("source_label", sort=False):
            if label in source_frames:
                source_frames[label] = pd.concat([source_frames[label], label_df], ignore_index=True)
            else:
                source_frames[label] = label_df.copy()
    for label in list(source_frames):
        source_frames[label] = source_frames[label].drop_duplicates(
            subset=["grupo", "match_key", "src_nombre", "src_precio_ars"],
            keep="first",
        )
    if not source_frames:
        source_frames["web"] = pd.DataFrame(columns=source_columns)
    return source_frames


def build_report(nb: pd.DataFrame, web: pd.DataFrame, cg: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    source_frames = combine_sources(web, cg)
    all_matches: dict[str, dict[int, dict]] = {}
    all_unmatched: dict[str, pd.DataFrame] = {}

    for label, source_df in source_frames.items():
        matches, unmatched = match_source(nb, source_df, label)
        all_matches[label] = matches
        all_unmatched[label] = unmatched

    rows = []
    source_labels = list(source_frames.keys())
    for nb_idx, nb_row in nb.iterrows():
        if not any(nb_idx in matches for matches in all_matches.values()):
            continue
        row = {
            "estado": "repetido",
            "grupo": nb_row["grupo"],
            "producto_normalizado": nb_row["match_key"],
            "nb_categoria": nb_row["nb_categoria"],
            "nb_codigo": nb_row["nb_codigo"],
            "nb_id_fabricante": nb_row["nb_id_fabricante"],
            "nb_nombre": nb_row["nb_nombre"],
            "nb_marca": nb_row["nb_marca"],
            "nb_precio_ars": nb_row["nb_precio_ars"],
            "nb_stock": nb_row["nb_stock"],
        }
        for label in source_labels:
            row.update(all_matches[label].get(nb_idx, {}))
        rows.append(row)

    result = pd.DataFrame(rows)
    if result.empty:
        return result, all_unmatched, source_frames

    price_columns = [pd.to_numeric(result.get(f"{label}_precio_ars"), errors="coerce") for label in source_labels]
    result["precio_competencia_min"] = pd.concat(price_columns, axis=1).min(axis=1)
    result["mayor_profit"] = result["precio_competencia_min"] - pd.to_numeric(result["nb_precio_ars"], errors="coerce")
    result = result.sort_values(["mayor_profit", "grupo", "nb_nombre"], ascending=[False, True, True])
    return result.fillna("-"), all_unmatched, source_frames


def write_report(
    result: pd.DataFrame,
    nb: pd.DataFrame,
    web: pd.DataFrame,
    cg: pd.DataFrame | None,
    output_dir: Path,
    source_frames: dict[str, pd.DataFrame] | None = None,
) -> tuple[Path, Path]:
    if cg is None:
        cg = pd.DataFrame()
    if source_frames is None:
        source_frames = combine_sources(web, cg)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"comparativa_nb_vs_web_{timestamp}.csv"
    xlsx_path = output_dir / f"comparativa_nb_vs_web_{timestamp}.xlsx"
    result.to_csv(csv_path, index=False, encoding="utf-8-sig")

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        result.to_excel(writer, sheet_name="resumen", index=False)
        for group, group_df in result.groupby("grupo", sort=True):
            group_df.to_excel(writer, sheet_name=group[:31], index=False)
        nb.to_excel(writer, sheet_name="distribuidor_nb", index=False)
        for label, source_df in source_frames.items():
            source_df.to_excel(writer, sheet_name=label[:31], index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for col in worksheet.columns:
                max_len = max(len(str(cell.value or "")) for cell in col)
                worksheet.column_dimensions[col[0].column_letter].width = min(max(max_len + 2, 12), 64)
    return csv_path, xlsx_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara NB contra multiples fuentes web.")
    parser.add_argument("--nb-csv", required=True, type=Path)
    parser.add_argument("--mexx-csv", type=Path, default=None)
    parser.add_argument("--mexx-dir", type=Path, default=Path("data/raw/mexx"))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()

    mexx_csv = args.mexx_csv or newest_mexx_csv(args.mexx_dir)
    nb = load_nb(args.nb_csv)
    web = load_mexx(mexx_csv)
    result, _, source_frames = build_report(nb, web)
    csv_path, xlsx_path = write_report(result, nb, web, None, args.out, source_frames)

    print(f"NB: {len(nb)} productos")
    print(f"Web: {len(web)} productos ({mexx_csv})")
    print(result.groupby(["grupo", "estado"]).size().unstack(fill_value=0).to_string())
    print(f"CSV: {csv_path}")
    print(f"Excel: {xlsx_path}")


if __name__ == "__main__":
    main()
