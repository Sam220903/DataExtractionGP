import sys
import os
import re
import json
import glob

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
SUMMARY_JSON = os.path.join(DATA_DIR, 'iniciativas_pa_resumen.json')


def acta_id_from(path: str):
    m = re.search(r'acta_(\d+)', path)
    return m.group(1) if m else None


def local_pdfs():
    """PDFs que ya descargó script3."""
    return [(acta_id_from(os.path.basename(p)), p) for p in sorted(glob.glob(os.path.join(PDF_DIR, '*.pdf')))]


# ==============================================================================
# MAIN
# ==============================================================================
def main():
    pdf_processor = PDFProcessor()
    comisiones = json.load(open(COMISIONES_JSON, encoding='utf-8')) if os.path.exists(COMISIONES_JSON) else []
    actas_processor = ActasProcessor(commission_names=[c['Nombre'] for c in comisiones])
    perfiles = json.load(open(PERFILES_JSON, encoding='utf-8'))['registros'] if os.path.exists(PERFILES_JSON) else []
    merger = ActasMerger(perfiles=perfiles)

    pdfs = [(a, p) for a, p in local_pdfs() if a]
    if not pdfs:
        print("No hay actas descargadas. Ejecuta primero script3.")
        return

    # Presentaciones: se guardan en caché; solo se leen los PDFs nuevos (no usa IA, no gasta tokens)
    cache = {}
    if os.path.exists(CACHE_JSON):
        cache = json.load(open(CACHE_JSON, encoding='utf-8'))
        if cache.get('_version') != ActasProcessor.VERSION:
            cache = {}
    for aid, path in pdfs:
        if aid in cache:
            continue
        clean_text = pdf_processor.clean_text(pdf_processor.extract_text(path))
        if clean_text:
            cache[aid] = actas_processor.process_file(clean_text, acta_id=aid)
            print(f"  acta {aid}: {len(cache[aid])} presentaciones")
    cache['_version'] = ActasProcessor.VERSION
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(cache, open(CACHE_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    presentations = [r for k, recs in cache.items() if k != '_version' for r in recs]

    # Votaciones (ya extraídas por script3) + fusión
    votes = json.load(open(UNIDAD_JSON, encoding='utf-8'))['registros'] if os.path.exists(UNIDAD_JSON) else []
    rows, review = merger.merge(presentations, votes, comisiones)

    json.dump(rows, open(OUTPUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)
    json.dump(review, open(REVIEW_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    resumen = dict(merger.stats)
    resumen['actas_leidas'] = len(pdfs)
    resumen['presentaciones_detectadas'] = len(presentations)
    resumen['actas_sin_presentaciones'] = sorted(k for k, recs in cache.items() if k != '_version' and not recs)
    json.dump(resumen, open(SUMMARY_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"\n✓ {len(rows)} registros -> {OUTPUT_JSON}")
    print(f"✓ {len(review)} casos para revisar -> {REVIEW_JSON}")
    print(f"✓ resumen de cobertura -> {SUMMARY_JSON}")
    print("\n=== Execution Finished ===")


if __name__ == '__main__':
    main()