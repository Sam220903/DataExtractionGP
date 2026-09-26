import sys
import os
# Añadimos la carpeta principal al path para poder importar desde lib/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import re
import time
import unicodedata
from curl_cffi import requests
from bs4 import BeautifulSoup

from lib.DateHandler import DateHandler
from lib.EventClassifier import EventClassifier
from lib.PDFProcessor import PDFProcessor
from extractors.SVProcessor import SVProcessor
from extractors.PMProcessor import PMProcessor


# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
comissions_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=369'
commitees_url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=409'
browser = "edge"
parser = "html.parser"

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
                extracted_data = {
                    "Tipo": None,
                    "Asunto abordado": None,
                    "Presentador": None,
                    "Partido": None,
                    "Estatus": None,
                    "Hora Programada": None,
                    "Inicio de la sesión": None,
                    "Fin de la sesión": None,
                    "Tiempo": None,
                    "Observaciones": None
                }

                if stenographic_link:
                    print(f"  Descargando versión estenográfica: {stenographic_link}")
                    local_pdf_path = pdf_processor.download_pdf(stenographic_link, sv_pdf_dir, browser)

                    if local_pdf_path:
                        raw_text = pdf_processor.extract_text(local_pdf_path)
                        clean_text = pdf_processor.clean_text(raw_text)

                        if clean_text:
                            extracted_data = sv_processor.process_file(clean_text)

                elif acta_link:
                    print(f"  No hay versión estenográfica, descargando acta anterior: {acta_link}")
                    local_pdf_path = pdf_processor.download_pdf(acta_link, pm_pdf_dir, browser)

                    if local_pdf_path:
                        raw_text = pdf_processor.extract_text(local_pdf_path)
                        clean_text = pdf_processor.clean_text(raw_text)

                        if clean_text:
                            extracted_data = pm_processor.process_file(clean_text)

                    extracted_data["Observaciones"] = "No hay versión estenográfica, datos obtenidos del acta anterior"

                else:
                    extracted_data["Observaciones"] = "No hay versión estenográfica ni otra fuente para completar el llenado de datos"

                # La fecha del HTML (current_date) es la fuente confiable; descartamos la
                # "Fecha de sesión en comisión / comité" que pudieran regresar los procesadores,
                # para no terminar con dos fechas distintas o una clave inconsistente entre sesiones.
                extracted_data.pop("Fecha de sesión en comisión / comité", None)

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
                extracted_data = {
                    "Tipo": None,
                    "Asunto abordado": None,
                    "Presentador": None,
                    "Partido": None,
                    "Estatus": None,
                    "Hora Programada": None,
                    "Inicio de la sesión": None,
                    "Fin de la sesión": None,
                    "Tiempo": None,
                    "Observaciones": None
                }

                if stenographic_link:
                    print(f"  Descargando versión estenográfica: {stenographic_link}")
                    local_pdf_path = pdf_processor.download_pdf(stenographic_link, sv_pdf_dir, browser)

                    if local_pdf_path:
                        raw_text = pdf_processor.extract_text(local_pdf_path)
                        clean_text = pdf_processor.clean_text(raw_text)

                        if clean_text:
                            extracted_data = sv_processor.process_file(clean_text)

                elif acta_link:
                    print(f"  No hay versión estenográfica, descargando acta anterior: {acta_link}")
                    local_pdf_path = pdf_processor.download_pdf(acta_link, pm_pdf_dir, browser)

                    if local_pdf_path:
                        raw_text = pdf_processor.extract_text(local_pdf_path)
                        clean_text = pdf_processor.clean_text(raw_text)

                        if clean_text:
                            extracted_data = pm_processor.process_file(clean_text)

                    extracted_data["Observaciones"] = "No hay versión estenográfica, datos obtenidos del acta anterior"

                else:
                    extracted_data["Observaciones"] = "No hay versión estenográfica ni otra fuente para completar el llenado de datos"

                # La fecha del HTML (current_date) es la fuente confiable; descartamos la
                # "Fecha de sesión en comisión / comité" que pudieran regresar los procesadores,
                # para no terminar con dos fechas distintas o una clave inconsistente entre sesiones.
                extracted_data.pop("Fecha de sesión en comisión / comité", None)

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

print(f"\nProceso terminado. Registros guardados: {len(data)}")