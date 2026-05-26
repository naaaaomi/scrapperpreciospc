from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
sys.path.append(str(SCRIPTS))

from compare_nb_all_sources import (  # noqa: E402
    build_report,
    load_mexx as load_web,
    load_nb,
    write_report,
)
from scrape_urls import scrape_urls, source_label_for_url, write_outputs  # noqa: E402


RUNS_DIR = ROOT / "data" / "gui_runs"
REPORTS_DIR = ROOT / "reports"


def save_uploaded_csv(uploaded_file) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RUNS_DIR / f"nb_uploaded_{timestamp}.csv"
    path.write_bytes(uploaded_file.getbuffer())
    return path


def parse_urls(raw_urls: str) -> list[str]:
    return [line.strip() for line in raw_urls.splitlines() if line.strip()]


def source_labels_from_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(source_label_for_url(url) for url in urls))


def format_ars(value: object) -> str:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(number):
        return "-"
    return f"{int(round(float(number))):,}".replace(",", ".")


def display_comparison(result: pd.DataFrame, source_labels: list[str]) -> pd.DataFrame:
    preview = result.copy()
    base_columns = ["grupo", "mayor_profit", "nb_nombre", "nb_precio_ars"]
    source_columns = [f"{label}_precio_ars" for label in source_labels if f"{label}_precio_ars" in preview.columns]
    ordered_columns = [col for col in base_columns + source_columns if col in preview.columns]
    preview = preview[ordered_columns].copy()

    rename_map = {
        "grupo": "grupo",
        "mayor_profit": "mayor profit",
        "nb_nombre": "producto",
        "nb_precio_ars": "precio nb",
    }
    rename_map.update({f"{label}_precio_ars": f"precio {label}" for label in source_labels})
    preview = preview.rename(columns=rename_map)

    for column in preview.columns:
        if column.startswith("precio ") or column == "mayor profit":
            preview[column] = preview[column].map(format_ars)
    return preview


def downloadable_file(path: Path, label: str, mime: str) -> None:
    st.download_button(
        label=label,
        data=path.read_bytes(),
        file_name=path.name,
        mime=mime,
        use_container_width=True,
    )


def main() -> None:
    st.set_page_config(page_title="Comparador de precios PC", layout="wide")
    st.title("Comparador de precios PC")

    uploaded_csv = st.file_uploader("Lista de precios del distribuidor NB", type=["csv"])
    urls_text = st.text_area(
        "URLs a comparar",
        placeholder=(
            "https://www.mexx.com.ar/productos-rubro/procesadores/\n"
            "https://tienda.example.com/procesadores"
        ),
        height=180,
    )

    run = st.button("Scrapear y comparar", type="primary", use_container_width=True)
    if not run:
        st.info("Subi el CSV, pega URLs y ejecuta la comparacion.")
        return

    if uploaded_csv is None:
        st.error("Primero subi el CSV del distribuidor.")
        return

    urls = parse_urls(urls_text)
    if not urls:
        st.error("Pega al menos una URL para comparar.")
        return
    source_labels = source_labels_from_urls(urls)

    nb_csv = save_uploaded_csv(uploaded_csv)
    web_csv = None
    progress = st.status("Ejecutando pipeline...", expanded=True)

    with progress:
        st.write("Leyendo CSV de NB...")
        nb = load_nb(nb_csv)

        web = pd.DataFrame()
        st.write("Scrapeando URLs...")
        try:
            products = scrape_urls(urls)
            if not products:
                st.error("No encontre productos con precio en las URLs indicadas. Proba con una pagina de categoria o listado que muestre nombre y precio.")
                return
            _, web_csv = write_outputs(products, ROOT / "data" / "raw" / "web")
            web = load_web(web_csv)
        except Exception as exc:
            st.error(f"No pude scrapear las URLs: {exc}")
            return
        if web.empty:
            st.error("No encontre productos con precio en las URLs indicadas. Proba con una pagina de categoria o listado que muestre nombre y precio en el HTML.")
            return

        st.write("Generando comparativa...")
        result, _, source_frames = build_report(nb, web)
        if result.empty:
            st.error("No encontre productos repetidos entre NB y las URLs indicadas.")
            return
        csv_path, xlsx_path = write_report(result, nb, web, None, REPORTS_DIR, source_frames)
        st.write("Listo.")

    st.subheader("Resumen")
    metric_cols = st.columns(4)
    metric_cols[0].metric("NB", len(nb))
    metric_cols[1].metric("Web", len(web))
    metric_cols[2].metric("URLs", len(urls))
    metric_cols[3].metric("Filas reporte", len(result))

    summary = result.groupby(["grupo"]).size().to_frame("repetidos")
    st.dataframe(summary, use_container_width=True)

    st.subheader("Top oportunidades")
    st.dataframe(display_comparison(result, source_labels), use_container_width=True)

    col_csv, col_xlsx = st.columns(2)
    with col_csv:
        downloadable_file(csv_path, "Descargar CSV", "text/csv")
    with col_xlsx:
        downloadable_file(
            xlsx_path,
            "Descargar Excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
