import sys
import os
# Añadimos la carpeta principal al path para poder importar desde lib/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import re
import time
import unicodedata
from collections import Counter
from curl_cffi import requests
from bs4 import BeautifulSoup

from lib.DateHandler import DateHandler
from lib.EventClassifier import EventClassifier
from lib.PDFProcessor import PDFProcessor
from lib.ExtractionCache import ExtractionCache
from lib.ExtractionErrors import ExtractionFailedError, isFatalErrorType, describeErrorType
from extractors.SVProcessor import SVProcessor
from extractors.PMProcessor import PMProcessor


# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
comissions_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=369'
commitees_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=409'
browser = "edge"
parser = "html.parser"

# Tras este número de fallas de IA seguidas se deja de llamar a Gemini en esta ejecución
# (los documentos restantes quedan pendientes para la siguiente ejecución).
MAX_CONSECUTIVE_AI_FAILURES = 3

dh = DateHandler()
ec = EventClassifier()
pdf_processor = PDFProcessor()
sv_processor = SVProcessor()
pm_processor = PMProcessor()

# Rutas de descarga de versiones estenográficas y actas anteriores
sv_pdf_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'pdfs', 'svs')
pm_pdf_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'pdfs', 'pms')
os.makedirs(sv_pdf_dir, exist_ok=True)
os.makedirs(pm_pdf_dir, exist_ok=True)

# Caché persistente: registra qué documentos (por enlace) ya se extrajeron con
# éxito, para que una ejecución posterior no vuelva a descargar ni a llamar a
# Gemini para esos registros, y solo reintente los que quedaron pendientes.
cache_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'extraction_cache.json')
cache = ExtractionCache(cache_path)

# Lista de registros que, al terminar la ejecución, quedaron sin poder
# completarse (por fallas de la API tras agotar los reintentos, por no poder
# descargar el PDF, o por no poder extraerle texto legible).
pending_records = []

# Estado de la IA durante esta ejecución
aiStopReason = None
consecutiveAiFailures = 0


def registerAiSuccess():
    """Una extracción con IA salió bien: se reinicia el contador de fallas seguidas"""
    global consecutiveAiFailures
    consecutiveAiFailures = 0


def registerAiFailure(errorType):
    """
    Una extracción con IA falló: se cuenta la falla y, si el error es fatal
    (cuota diaria agotada, API key inválida) o ya hubo demasiadas fallas
    seguidas, se detiene el uso de la IA durante el resto de la ejecución.
    """
    global aiStopReason, consecutiveAiFailures
    consecutiveAiFailures += 1

    if isFatalErrorType(errorType):
        aiStopReason = describeErrorType(errorType)
    elif consecutiveAiFailures >= MAX_CONSECUTIVE_AI_FAILURES:
        aiStopReason = (f"Se acumularon {consecutiveAiFailures} fallas seguidas de la IA "
                        f"(última causa: {describeErrorType(errorType)})")

    if aiStopReason:
        print(f"  Se detiene el análisis con IA en esta ejecución: {aiStopReason}")


def build_default_extracted_data():
    """Estructura por defecto de los datos extraídos de una sesión, para que
    todas las sesiones del JSON final tengan siempre las mismas claves."""
    return {
        "Tipo": None,
        "Asunto abordado": None,
        "Presentador": None,
        "Partido": None,
        "Estatus": None,
        "Hora Programada": None,
        "Inicio de la sesión": None,
        "Fin de la sesión": None,
        "Tiempo": None,
        "Observaciones": None,
        "Estado de extracción": None
    }


def process_session_document(stenographic_link, acta_link, group_name, group_type, current_date):
    """
    Descarga y procesa el documento de una sesión (versión estenográfica o,
    en su defecto, acta anterior), usando la caché para no repetir trabajo ya
    hecho en una ejecución anterior.

    Regresa el diccionario de datos extraídos, ya listo para incluirse en el
    registro de la sesión, con "Estado de extracción" en uno de estos valores:
      - "Completo": se obtuvieron los datos exitosamente (ahora o en una
        ejecución previa, gracias a la caché).
      - "Pendiente": hubo un problema (falla de la API, descarga o texto
        ilegible) y el registro debe reintentarse en una futura ejecución.
      - "N/A": no había ninguna fuente (ni versión estenográfica ni acta)
        disponible para esta sesión.
    """
    extracted_data = build_default_extracted_data()

    source_link = stenographic_link or acta_link
    source_type = "SV" if stenographic_link else ("PM" if acta_link else None)

    if source_link is None:
        extracted_data["Observaciones"] = "No hay versión estenográfica ni otra fuente para completar el llenado de datos"
        extracted_data["Estado de extracción"] = "N/A"
        return extracted_data

    # Si este documento ya se procesó con éxito en una ejecución anterior,
    # reutilizamos ese resultado sin descargar ni llamar a Gemini de nuevo.
    if cache.is_success(source_link):
        cached_entry = cache.get(source_link)
        print(f"  Ya se había extraído previamente ({cached_entry['source']}), usando resultado en caché: {source_link}")
        extracted_data = dict(cached_entry["data"])
        extracted_data["Estado de extracción"] = "Completo"

        if source_type == "PM":
            extracted_data["Observaciones"] = "No hay versión estenográfica, datos obtenidos del acta anterior"

        return extracted_data

    # La IA se detuvo antes en esta ejecución (cuota agotada, API key inválida o
    # demasiadas fallas seguidas): no se descarga ni se llama a Gemini. El documento
    # no queda en la caché de éxitos, así que se procesará en la siguiente ejecución.
    if aiStopReason is not None:
        print(f"  Se omite (IA detenida en esta ejecución): {source_link}")
        extracted_data["Observaciones"] = "No se intentó procesar el documento porque el análisis con IA se detuvo en esta ejecución; pendiente de reprocesar"
        extracted_data["Estado de extracción"] = "Pendiente"
        pending_records.append({
            "Tipo": group_type,
            "Comisión / Comité": group_name,
            "Fecha": current_date,
            "Fuente": source_type,
            "Enlace": source_link,
            "Motivo": f"No se intentó: {aiStopReason}",
            "Código de error": "no_intentado"
        })
        return extracted_data

    # No hay caché exitosa: intentamos procesar el documento desde cero.
    processor = sv_processor if source_type == "SV" else pm_processor
    pdf_dir = sv_pdf_dir if source_type == "SV" else pm_pdf_dir
    label = "versión estenográfica" if source_type == "SV" else "acta anterior"

    print(f"  Descargando {label}: {source_link}")
    local_pdf_path = pdf_processor.download_pdf(source_link, pdf_dir, browser)

    if not local_pdf_path:
        print(f"  No se pudo descargar el archivo: {source_link}")
        extracted_data["Observaciones"] = f"No se pudo descargar el archivo de {label}; pendiente de reprocesar"
        extracted_data["Estado de extracción"] = "Pendiente"
        cache.set_failed(source_link, source_type, "sin_descarga")
        pending_records.append({
            "Tipo": group_type,
            "Comisión / Comité": group_name,
            "Fecha": current_date,
            "Fuente": source_type,
            "Enlace": source_link,
            "Motivo": "No se pudo descargar el archivo",
            "Código de error": "sin_descarga"
        })
        return extracted_data

    raw_text = pdf_processor.extract_text(local_pdf_path)
    clean_text = pdf_processor.clean_text(raw_text)

    if not clean_text:
        print(f"  No se pudo extraer texto legible del archivo: {source_link}")
        extracted_data["Observaciones"] = f"No se pudo extraer texto legible del archivo de {label}; pendiente de reprocesar"
        extracted_data["Estado de extracción"] = "Pendiente"
        cache.set_failed(source_link, source_type, "sin_texto")
        pending_records.append({
            "Tipo": group_type,
            "Comisión / Comité": group_name,
            "Fecha": current_date,
            "Fuente": source_type,
            "Enlace": source_link,
            "Motivo": "No se pudo extraer texto legible del PDF",
            "Código de error": "sin_texto"
        })
        return extracted_data

    try:
        extracted_data = processor.process_file(clean_text)
        extracted_data["Estado de extracción"] = "Completo"

        if source_type == "PM":
            extracted_data["Observaciones"] = "No hay versión estenográfica, datos obtenidos del acta anterior"

        cache.set_success(source_link, source_type, extracted_data)
        registerAiSuccess()

    except ExtractionFailedError as e:
        errorType = e.errorType
        print(f"  No se pudo completar la extracción tras varios intentos: {e}")
        print(f"  Causa: {describeErrorType(errorType)}")
        extracted_data = build_default_extracted_data()
        extracted_data["Observaciones"] = "No se pudo completar la extracción de datos tras varios intentos; pendiente de reprocesar"
        extracted_data["Estado de extracción"] = "Pendiente"
        cache.set_failed(source_link, source_type, errorType)
        pending_records.append({
            "Tipo": group_type,
            "Comisión / Comité": group_name,
            "Fecha": current_date,
            "Fuente": source_type,
            "Enlace": source_link,
            "Motivo": f"Falló la extracción con Gemini: {describeErrorType(errorType)}",
            "Código de error": errorType
        })
        registerAiFailure(errorType)

    return extracted_data


data = []

# ----------------------------------------- Extracción de datos de comisiones -----------------------------------------
print("Accediendo a la página de comisiones")

response = requests.get(comissions_url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

if response.status_code == 200:

    # Conjunto para almacenar los ids ya procesados
    visited_ids = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Buscar el contenedor de comisiones
    commisions_container = soup.find('div', class_="contenedorComisiones")

    commisions = commisions_container.find_all('div', class_="tarjetaComision")

    # Iterar por cada comisión
    for commision in commisions:
        commision_a = commision.find('a')
        commision_title = commision_a.find('h3').find('p').text.strip()
        commision_link = f'{domain}{commision_a['href']}'

        print(f"\nProcesando la comisión: {commision_title}")

        commision_response = requests.get(commision_link, impersonate=browser)

        if commision_response.status_code == 200:
            commision_soup = BeautifulSoup(commision_response.text, parser)

            info_divs = commision_soup.find('div', class_="contenedorGeneralComision").find_all('div', recursive=False)
            name = info_divs[0].find('h1').text.strip()

            sessions_info_container = commision_soup.find('div', class_="contenedorGeneralComision").find('section', id="contenedorBotoneraPorCategorias", recursive=False)

            # Traemos fechas y adjuntos en una sola lista, en el orden en que aparecen en el HTML
            session_blocks = sessions_info_container.find_all(
                'div',
                class_=lambda c: c and ('tituloFechaSesion' in c or 'contenedorAdjuntosComision' in c)
            )

            sessions = []
            current_date = None

            for block in session_blocks:
                classes = block.get('class', [])

                # Si es un título de fecha, lo guardamos y seguimos: aplica a los adjuntos que vienen después
                if 'tituloFechaSesion' in classes:
                    current_date = ' '.join(block.get_text(separator=' ', strip=True).split())
                    continue

                # Aquí ya es un contenedorAdjuntosComision: buscamos acta anterior y versión estenográfica, priorizando PDF
                stenographic_link = None
                acta_link = None

                for a in block.find_all('a', href=True):
                    # Texto visible del botón, sin acentos y en minúsculas
                    label = unicodedata.normalize('NFKD', a.get_text())
                    label = ''.join(c for c in label if not unicodedata.combining(c)).lower()
                    label = ' '.join(label.split())

                    # Title del enlace, normalmente trae el nombre de archivo con extensión
                    title_attr = unicodedata.normalize('NFKD', a.get('title', '').strip())
                    title_attr = ''.join(c for c in title_attr if not unicodedata.combining(c)).lower()

                    # El ícono dentro del botón delata si es .docx o .pdf
                    icon_span = a.find('span')
                    icon_classes = icon_span.get('class', []) if icon_span else []

                    if 'icon-documento' in icon_classes or title_attr.endswith('.docx') or title_attr.endswith('.doc'):
                        is_pdf = False
                    elif 'icon-pdf' in icon_classes or title_attr.endswith('.pdf'):
                        is_pdf = True
                    else:
                        # Sin ícono ni extensión que lo descarte, se acepta como válido
                        is_pdf = True

                    href = a['href']
                    url = href if href.startswith('http') else f'{domain}{href}'

                    if 'estenograf' in label and stenographic_link is None and is_pdf:
                        stenographic_link = url

                    if 'acta anterior' in label and acta_link is None and is_pdf:
                        acta_link = url

                if (stenographic_link or acta_link) and current_date is None:
                    print(f"  Aviso: adjuntos sin fecha previa en {commision_title}")

                # ------------------- Descarga y procesamiento del documento de la sesión -------------------
                extracted_data = process_session_document(
                    stenographic_link, acta_link, name, "Comisión", current_date
                )

                sessions.append({
                    "Fecha de sesión en comisión": current_date,
                    "Estenografica": stenographic_link,
                    "Acta": acta_link,
                    **extracted_data
                })

            commission_data = {
                "Nombre": name,
                "Tipo": "Comisión",
                "Enlace": commision_link,
                "Sesiones": sessions
            }

            data.append(commission_data)
            total_records += 1

    print(f"\nComisiones procesadas: {total_records}")


#  ------------------------------------- Extracción de datos de comités ---------------------------------------
print("Accediendo a la página de comités")

response = requests.get(commitees_url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

if response.status_code == 200:

    # Conjunto para almacenar los ids ya procesados
    visited_ids = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Buscar el contenedor de comités
    commitees_container = soup.find('div', class_="itemListSubCategories")

    commitees = commitees_container.find_all('div', class_="fichaLegislador")

    # Iterar por cada comité
    for commitee in commitees:
        commitee_a = commitee.find('h4').find('a')
        commitee_title = commitee_a.text.strip()
        commitee_link = f'{domain}{commitee_a['href']}'

        print(f"\nProcesando el comité: {commitee_title}")

        commitee_response = requests.get(commitee_link, impersonate=browser)

        if commitee_response.status_code == 200:
            commitee_soup = BeautifulSoup(commitee_response.text, parser)

            # La página de comités tiene una estructura distinta a la de comisiones:
            # contenedorBotoneraPorCategorias no cuelga directo de contenedorGeneralComision,
            # está anidado dentro de info_divs[2] -> section.content
            info_divs = commitee_soup.find('div', class_="contenedorGeneralComision").find_all('div', recursive=False)
            name = info_divs[0].find('h1').text.strip()

            content_section = info_divs[2].find('section', class_="content")
            sessions_info_container = content_section.find('section', id="contenedorBotoneraPorCategorias", recursive=False)

            # Traemos fechas y adjuntos en una sola lista, en el orden en que aparecen en el HTML
            session_blocks = sessions_info_container.find_all(
                'div',
                class_=lambda c: c and ('tituloFechaSesion' in c or 'contenedorAdjuntosComision' in c)
            )

            sessions = []
            current_date = None

            for block in session_blocks:
                classes = block.get('class', [])

                # Si es un título de fecha, lo guardamos y seguimos: aplica a los adjuntos que vienen después
                if 'tituloFechaSesion' in classes:
                    current_date = ' '.join(block.get_text(separator=' ', strip=True).split())
                    continue

                # Aquí ya es un contenedorAdjuntosComision: buscamos acta anterior y versión estenográfica, priorizando PDF
                stenographic_link = None
                acta_link = None

                for a in block.find_all('a', href=True):
                    # Texto visible del botón, sin acentos y en minúsculas
                    label = unicodedata.normalize('NFKD', a.get_text())
                    label = ''.join(c for c in label if not unicodedata.combining(c)).lower()
                    label = ' '.join(label.split())

                    # Title del enlace, normalmente trae el nombre de archivo con extensión
                    title_attr = unicodedata.normalize('NFKD', a.get('title', '').strip())
                    title_attr = ''.join(c for c in title_attr if not unicodedata.combining(c)).lower()

                    # El ícono dentro del botón delata si es .docx o .pdf
                    icon_span = a.find('span')
                    icon_classes = icon_span.get('class', []) if icon_span else []

                    if 'icon-documento' in icon_classes or title_attr.endswith('.docx') or title_attr.endswith('.doc'):
                        is_pdf = False
                    elif 'icon-pdf' in icon_classes or title_attr.endswith('.pdf'):
                        is_pdf = True
                    else:
                        # Sin ícono ni extensión que lo descarte, se acepta como válido
                        is_pdf = True

                    href = a['href']
                    url = href if href.startswith('http') else f'{domain}{href}'

                    if 'estenograf' in label and stenographic_link is None and is_pdf:
                        stenographic_link = url

                    if 'acta anterior' in label and acta_link is None and is_pdf:
                        acta_link = url

                if (stenographic_link or acta_link) and current_date is None:
                    print(f"  Aviso: adjuntos sin fecha previa en {commitee_title}")

                # ------------------- Descarga y procesamiento del documento de la sesión -------------------
                extracted_data = process_session_document(
                    stenographic_link, acta_link, name, "Comité", current_date
                )

                sessions.append({
                    "Fecha de sesión en comisión": current_date,
                    "Estenografica": stenographic_link,
                    "Acta": acta_link,
                    **extracted_data
                })

            commitee_data = {
                "Nombre": name,
                "Tipo": "Comité",
                "Enlace": commitee_link,
                "Sesiones": sessions
            }

            data.append(commitee_data)
            total_records += 1

    print(f"\nComités procesados: {total_records}")


# ----------------------------------------------- Guardar resultados -----------------------------------------------
folder = "data"
os.makedirs(folder, exist_ok=True)
ruta_archivo = os.path.join(folder, "comisiones_contenido.json")
with open(ruta_archivo, 'w', encoding='utf-8') as output_file:
    json.dump(data, output_file, ensure_ascii=False, indent=4)


# ------------------------------------------- Reporte de pendientes -------------------------------------------
print(f"\n=== Resumen de extracción ===")
if pending_records:
    print(f"Registros pendientes de completar: {len(pending_records)}")

    # Resumen por causa, para ver de un vistazo si fue alta demanda, cuota agotada, etc.
    errorCounts = Counter(record.get("Código de error", "error_desconocido") for record in pending_records)
    print("Pendientes por causa:")
    for errorCode, count in errorCounts.most_common():
        print(f"  - {count} x {describeErrorType(errorCode)}")

    if aiStopReason:
        print(f"\nEl análisis con IA se detuvo en esta ejecución: {aiStopReason}")

    print("")
    for record in pending_records:
        print(f"  - [{record['Tipo']}] {record['Comisión / Comité']} | Fecha: {record['Fecha']} | "
              f"Fuente: {record['Fuente']} | Motivo: {record['Motivo']}")
        print(f"    Enlace: {record['Enlace']}")

    pending_path = os.path.join(folder, "registros_pendientes.json")
    with open(pending_path, 'w', encoding='utf-8') as f:
        json.dump(pending_records, f, ensure_ascii=False, indent=4)

    print(f"\nEstos registros quedaron marcados como 'Pendiente' en el JSON principal.")
    print(f"La lista también se guardó por separado en: {pending_path}")
    print(f"En la próxima ejecución, estos registros se reintentarán automáticamente")
    print(f"(los que ya se completaron con éxito no se vuelven a procesar, gracias a la caché en: {cache_path}).")
    if aiStopReason:
        print("Vuelve a ejecutar el script cuando se resuelva el problema de la IA: solo se enviarán a Gemini los documentos pendientes.")
else:
    print("Todos los registros con documento disponible se extrajeron correctamente. No hay pendientes.")

print(f"\nProceso terminado. Registros guardados: {len(data)}")