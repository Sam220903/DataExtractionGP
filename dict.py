
import sys
import os
import re
import json
import glob
import argparse

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lib.PDFProcessor import PDFProcessor
from extractors.ActasProcessor import ActasProcessor, ActasMerger

# ==============================================================================
# CONFIGURACIÓN
# ==============================================================================
BASE = os.path.join(os.path.dirname(__file__), '..')
DATA_DIR = os.path.join(BASE, 'data')
PDF_DIR = os.path.join(DATA_DIR, 'pdfs', 'minutes')

UNIDAD_JSON = os.path.join(DATA_DIR, 'unidad_participacion.json')      # votaciones (extracción previa con IA)
COMISIONES_JSON = os.path.join(DATA_DIR, 'comisiones_contenido.json')  # sesiones de comisiones (extracción previa)
PERFILES_JSON = os.path.join(DATA_DIR, 'perfiles.json')                # partido de cada diputado
CACHE_JSON = os.path.join(DATA_DIR, 'actas_presentaciones.json')       # presentaciones ya parseadas
OUTPUT_JSON = os.path.join(DATA_DIR, 'iniciativas_pa.json')
REVIEW_JSON = os.path.join(DATA_DIR, 'iniciativas_pa_revision.json')

DOMAIN = 'https://www.congresopuebla.gob.mx'
URL = f'{DOMAIN}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345'
BROWSER = 'edge'
PARSER = 'html.parser'


# ==============================================================================
# PHASE 1: OBTENER LOS PDFs (solo descarga; NO se llama a la IA)
# ==============================================================================
def acta_id_from(path_or_link: str):
    m = re.search(r'acta_(\d+)', path_or_link) or re.search(r'[?&]id=(\d+)', path_or_link)
    return m.group(1) if m else None


def local_pdfs():
    """PDFs que ya descargó script3 (o una corrida anterior de este script)."""
    return [(acta_id_from(p), p) for p in sorted(glob.glob(os.path.join(PDF_DIR, '*.pdf')))]


def scrape_pdfs(pdf_processor):
    """Misma navegación que script3. Si el PDF ya existe en disco no se vuelve a descargar."""
    from curl_cffi import requests
    from bs4 import BeautifulSoup

    os.makedirs(PDF_DIR, exist_ok=True)
    found, seen = [], set()
    print("Accediendo a la página principal de actas...")
    response = requests.get(URL, impersonate=BROWSER)
    if response.status_code != 200:
        print("No se pudo acceder a la página principal; se usarán solo los PDFs locales.")
        return local_pdfs()

    soup = BeautifulSoup(response.text, PARSER)
    section = soup.find('div', class_='itemListSubCategories')
    if not section:
        print("La estructura de la página cambió, no se encontraron subcategorías de actas.")
        return local_pdfs()

    for sc in section.find_all('div', class_='fichaLegislador'):
        header = sc.find('h4')
        print(f"\nProcesando la subcategoría: {header.text.strip()}")
        sc_response = requests.get(f"{DOMAIN}{header.find('a')['href']}", impersonate=BROWSER)
        if sc_response.status_code != 200:
            continue
        sc_item_list = BeautifulSoup(sc_response.text, PARSER).find('div', class_="itemList")
        if not sc_item_list:
            continue
        leading = sc_item_list.find('div', id="itemListLeading")
        primary = sc_item_list.find('div', id="itemListPrimary")
        minutes = (leading.find_all('div', class_="itemContainer") if leading else []) + \
                  (primary.find_all('div', class_="itemContainer") if primary else [])
        for minute in minutes:
            a_tag = minute.find('a')
            if not a_tag:
                continue
            link = f"{DOMAIN}{a_tag['href']}"
            if link in seen:
                continue
            seen.add(link)
            aid = acta_id_from(link)
            existing = os.path.join(PDF_DIR, f'acta_{aid}.pdf')
            if aid and os.path.exists(existing):
                found.append((aid, existing))
                continue
            print(f'\nIniciando descarga de: {link}')
            path = pdf_processor.download_pdf(link, PDF_DIR, BROWSER)
            if path:
                found.append((aid or acta_id_from(path), path))
    return found


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--local', action='store_true', help='No visitar la web: usar solo los PDFs ya descargados')
    ap.add_argument('--reparse', action='store_true', help='Ignorar el caché y volver a leer todos los PDFs')
    args = ap.parse_args()

    pdf_processor = PDFProcessor()

    comisiones = json.load(open(COMISIONES_JSON, encoding='utf-8')) if os.path.exists(COMISIONES_JSON) else []
    actas_processor = ActasProcessor(commission_names=[c['Nombre'] for c in comisiones])
    perfiles = json.load(open(PERFILES_JSON, encoding='utf-8'))['registros'] if os.path.exists(PERFILES_JSON) else []
    merger = ActasMerger(perfiles=perfiles)

    # --- Presentaciones: caché + actas nuevas -----------------------------------
    cache = {}
    if os.path.exists(CACHE_JSON) and not args.reparse:
        cache = json.load(open(CACHE_JSON, encoding='utf-8'))

    pdfs = local_pdfs() if args.local else scrape_pdfs(pdf_processor)
    for aid, path in pdfs:
        if aid in cache:
            continue
        raw_text = pdf_processor.extract_text(path)
        clean_text = pdf_processor.clean_text(raw_text)
        if not clean_text:
            continue
        cache[aid] = actas_processor.process_file(clean_text, acta_id=aid)
        print(f"  acta {aid}: {len(cache[aid])} presentaciones")

    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(cache, open(CACHE_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    presentations = [r for recs in cache.values() for r in recs]

    # --- PHASE 2: votaciones (ya existen, sin IA nueva) + fusión -------------------
    votes = json.load(open(UNIDAD_JSON, encoding='utf-8'))['registros'] if os.path.exists(UNIDAD_JSON) else []
    rows, review = merger.merge(presentations, votes, comisiones)

    # --- PHASE 3: guardar ---------------------------------------------------------
    json.dump(rows, open(OUTPUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)
    json.dump(review, open(REVIEW_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"\n✓ {len(rows)} registros -> {OUTPUT_JSON}")
    print(f"✓ {len(review)} casos para revisar -> {REVIEW_JSON}")
    print("\n=== Execution Finished ===")


if __name__ == '__main__':
    main()