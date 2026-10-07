import sys
import os
import re
import json
import glob
import shutil
from collections import defaultdict

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from lib.PDFProcessor import PDFProcessor
from extractors.GacetaProcessor import GacetaProcessor
from lib.FolioManager import FolioManager
from lib.ActasRegistry import ActasRegistry

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345'
browser = 'edge'
parser = 'html.parser'

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
pdf_dir = os.path.join(DATA_DIR, 'pdfs', 'minutes')


def acta_id_from(text):
    m = re.search(r'acta_(\d+)', text) or re.search(r'[?&]id=(\d+)', text)
    return m.group(1) if m else None


# ==============================================================================
# FUENTES DE ACTAS: web (descarga solo lo que falta) o PDFs locales
# ==============================================================================
def local_actas():
    for p in sorted(glob.glob(os.path.join(pdf_dir, '*.pdf'))):
        yield acta_id_from(os.path.basename(p)), None, p


def web_actas(pdf_processor, registry):
    from curl_cffi import requests
    from bs4 import BeautifulSoup

    print("Accediendo a la página principal de actas...")
    response = requests.get(url, impersonate=browser)
    if response.status_code != 200:
        return
    downloaded_minutes = set()
    soup = BeautifulSoup(response.text, parser)
    subcategories_section = soup.find('div', class_='itemListSubCategories')
    if not subcategories_section:
        print("La estructura de la página cambió, no se encotraron subcategorías de actas para analizar.")
        return

    for sc in subcategories_section.find_all('div', class_='fichaLegislador'):
        header = sc.find('h4')
        print(f"\nProcesando la subcategoría: {header.text.strip()}")
        sc_response = requests.get(f"{domain}{header.find('a')['href']}", impersonate=browser)
        if sc_response.status_code != 200:
            continue
        sc_item_list = BeautifulSoup(sc_response.text, parser).find('div', class_="itemList")
        if not sc_item_list:
            continue
        os.makedirs(pdf_dir, exist_ok=True)
        sc_leading = sc_item_list.find('div', id="itemListLeading")
        sc_primary = sc_item_list.find('div', id="itemListPrimary")
        all_minutes = (sc_leading.find_all('div', class_="itemContainer") if sc_leading else []) + \
                      (sc_primary.find_all('div', class_="itemContainer") if sc_primary else [])

        for minute in all_minutes:
            a_tag = minute.find('a')
            if not a_tag:
                continue
            download_link = f"{domain}{a_tag['href']}"
            if download_link in downloaded_minutes:
                continue
            downloaded_minutes.add(download_link)
            aid = acta_id_from(download_link)

            # Ya descargada según el registro -> no se vuelve a bajar
            known = registry.pdf_on_disk(aid) if aid else None
            if known:
                yield aid, download_link, known
                continue
            print(f'\nIniciando descarga de: {download_link}')
            local_pdf_path = pdf_processor.download_pdf(download_link, pdf_dir, browser)
            if local_pdf_path:
                aid = aid or acta_id_from(os.path.basename(local_pdf_path))
                registry.mark_downloaded(aid, download_link, local_pdf_path)
                yield aid, download_link, local_pdf_path


def read_clean(pdf_processor, path):
    return pdf_processor.clean_text(pdf_processor.extract_text(path))


# ==============================================================================
# AJUSTES DEL DESARROLLADOR (el usuario final no necesita tocar nada)
# ==============================================================================
REANALIZAR_PROMPT_ANTERIOR = False   # True = reanaliza con IA las actas analizadas con un prompt anterior (gasta tokens)
FORZAR_ACTAS = []                    # ids de acta a reanalizar siempre, ej. ['54601', '55669']
MAX_FALLAS_IA_CONSECUTIVAS = 3       # tras este número de fallas de IA seguidas se detiene el análisis (las pendientes quedan para la próxima ejecución)


# ==============================================================================
# AVISOS AL USUARIO SOBRE ERRORES DE IA
# ==============================================================================
def describeAiError(reason):
    return GacetaProcessor.ERROR_MESSAGES.get(reason, reason)


def countPending(remainingActas, registry, gaceta_processor):
    """Cuenta las actas que todavía necesitan análisis y no se alcanzaron a intentar"""
    pending = 0
    for aid, link, path in remainingActas:
        if registry.needs_analysis(aid, gaceta_processor.PROMPT_VERSION,
                                   force=aid in FORZAR_ACTAS, reanalyze_old=REANALIZAR_PROMPT_ANTERIOR):
            pending += 1
    return pending


def buildStopMessage(gaceta_processor, consecutiveFailures):
    if gaceta_processor.isFatalError():
        return describeAiError(gaceta_processor.last_error_type)
    return f"Se acumularon {consecutiveFailures} fallas seguidas de la IA ({describeAiError(gaceta_processor.last_error_type)})"


def printAiFailureReport(registry, stopMessage, notAttempted):
    failures = registry.pending_failures()
    if not failures and not stopMessage:
        return

    print("\n" + "=" * 70)
    print("ACTAS NO PROCESADAS")
    print("=" * 70)

    if stopMessage:
        print(f"El análisis con IA se detuvo: {stopMessage}")
        print(f"Actas que ni siquiera se alcanzaron a intentar: {notAttempted}")

    for aid, error in failures:
        print(f"  - acta {aid}: {describeAiError(error.get('motivo'))} ({error.get('fecha')})")

    print("\nLas actas ya analizadas quedaron guardadas en el registro.")
    print("Vuelve a ejecutar el script cuando se resuelva el problema: solo se enviarán a la IA")
    print("las actas pendientes, las que ya fueron analizadas no consumen tokens.")
    print("=" * 70)


# ==============================================================================
# MAIN: todo es automático
# ==============================================================================
def main():
    pdf_processor = PDFProcessor()
    gaceta_processor = GacetaProcessor()
    registry = ActasRegistry(DATA_DIR)
    json_path = os.path.join(DATA_DIR, 'unidad_participacion.json')

    # --- PHASE 0: reunir actas (descarga solo las que faltan; si no hay internet usa las locales) ----
    actas = []
    try:
        actas = list(web_actas(pdf_processor, registry))
    except Exception as e:
        print(f"No se pudo consultar la web ({e}); se usarán los PDFs ya descargados.")
    known = {a[0] for a in actas}
    actas += [a for a in local_actas() if a[0] not in known]
    actas = [a for a in actas if a[0]]

    # --- Migración automática: primera vez con un unidad_participacion.json previo ----------------
    if not registry.entries or not any(e.get('analizado') for e in registry.entries.values()):
        if os.path.exists(json_path):
            backup = json_path.replace('.json', '.respaldo.json')
            if not os.path.exists(backup):
                shutil.copyfile(json_path, backup)
            actas_by_date = defaultdict(list)
            for aid, link, path in actas:
                text = read_clean(pdf_processor, path)
                fecha = gaceta_processor.get_date(text) if text else None
                if fecha:
                    actas_by_date[fecha].append(aid)
            old = json.load(open(json_path, encoding='utf-8')).get('registros', [])
            print("Histórico existente reconocido, no se vuelve a analizar:",
                  registry.migrate(old, dict(actas_by_date), prompt_version=1))

    # --- PHASE 1: analizar SOLO lo que no está analizado -------------------------------------------
    stats = {"nuevas": 0, "ya_analizadas": 0, "sin_votos(sin IA)": 0, "fallidas": 0}
    consecutiveFailures = 0
    stopMessage = None
    notAttempted = 0

    for index, (aid, link, path) in enumerate(actas):
        registry.mark_downloaded(aid, link or registry.get(aid).get('url'), path)

        if not registry.needs_analysis(aid, gaceta_processor.PROMPT_VERSION,
                                       force=aid in FORZAR_ACTAS, reanalyze_old=REANALIZAR_PROMPT_ANTERIOR):
            stats["ya_analizadas"] += 1
            continue

        clean_text = read_clean(pdf_processor, path)
        if not clean_text:
            registry.mark_failed(aid, "sin_texto"); stats["fallidas"] += 1
            continue
        fecha = gaceta_processor.get_date(clean_text)
        if not fecha:
            registry.mark_failed(aid, "sin_fecha"); stats["fallidas"] += 1
            print(f"  acta {aid}: no se pudo extraer la fecha")
            continue

        # Sin conteo de votos ni 'unanimidad de votos' la regla A del prompt no puede encontrar nada: no se gasta IA
        if not gaceta_processor.has_vote_anchor(clean_text):
            registry.mark_analyzed(aid, [], "sin_votaciones_prefiltro", gaceta_processor.PROMPT_VERSION,
                                   gaceta_processor.model_id, fecha)
            stats["sin_votos(sin IA)"] += 1
            continue

        new_records = gaceta_processor.process_file(clean_text, folio_manager=None, acta_id=aid)

        # Falló la IA: NO se marca como analizada, se guarda el motivo y se reintenta la próxima ejecución
        if not gaceta_processor.last_extraction_ok:
            registry.mark_failed(aid, gaceta_processor.last_error_type, gaceta_processor.last_error_detail)
            stats["fallidas"] += 1
            consecutiveFailures += 1
            print(f"  acta {aid}: NO procesada -> {describeAiError(gaceta_processor.last_error_type)}")

            if gaceta_processor.isFatalError() or consecutiveFailures >= MAX_FALLAS_IA_CONSECUTIVAS:
                stopMessage = buildStopMessage(gaceta_processor, consecutiveFailures)
                notAttempted = countPending(actas[index + 1:], registry, gaceta_processor)
                break
            continue

        consecutiveFailures = 0
        registry.mark_analyzed(aid, new_records, "ok", gaceta_processor.PROMPT_VERSION,
                               gaceta_processor.model_id, fecha)
        stats["nuevas"] += 1
        print(f"  acta {aid}: {len(new_records)} registros")

    print("\nResumen:", stats, "| Registro:", registry.summary())
    printAiFailureReport(registry, stopMessage, notAttempted)

    # --- PHASE 2: SORTING & FOLIATION (sobre todo lo analizado, nuevo y previo) -----------------------
    data = {"registros": registry.all_records()}
    if data["registros"]:
        print("\n=== Phase 2: Chronological Sorting & Foliation ===")
        data["registros"].sort(key=lambda x: x.get("Fecha") or "")
        folio_manager = FolioManager()
        period_map = {"Primer periodo": 1, "Segundo periodo": 2, "Tercer periodo": 3}
        year_map = {"Primer año": 1, "Segundo año": 2, "Tercer año": 3}
        for record in data["registros"]:
            period_int = period_map.get(record.get("Periodo", ""), 1)
            year_int = year_map.get(record.get("Año Legislatura", ""), 1)
            folio_leg, folio_per = folio_manager.generate_folios(record.get("Fecha"), year_int, period_int)
            record["Folio legislatura"] = folio_leg
            record["Folio periodo"] = folio_per
        print("✓ Foliation completed.")

        # ==========================================================================
        # PHASE 3: SAVE JSON
        # ==========================================================================
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"✓ JSON saved successfully: {len(data['registros'])} records -> {json_path}")
        except Exception as e:
            print(f"Error saving JSON: {e}")
        print("\n=== Execution Finished ===")
    else:
        print("\nEl proceso terminó, pero no se extrajeron registros para guardar.")


if __name__ == '__main__':
    main()