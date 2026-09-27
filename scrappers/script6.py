import sys
import os
# Añadimos la carpeta principal al path para poder importar desde lib/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import time
import unicodedata
from curl_cffi import requests
from bs4 import BeautifulSoup

from lib.PDFProcessor import PDFProcessor
from extractors.GMProcessor import GMProcessor
from urllib.parse import urljoin

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
gacetas_url = f'{domain}/index.php?option=com_content&view=article&id=12868'
browser = "edge"
parser = "html.parser"

pdf_processor = PDFProcessor()
gm_processor = GMProcessor()

# Rutas de descarga de gacetas legislativas mensuales
gm_pdf_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'pdfs', 'gms')
os.makedirs(gm_pdf_dir, exist_ok=True)

# ----------------------------------------- Extracción de datos de gacetas mensuales -----------------------------------------
print("Accediendo a la página de gacetas mensuales...")

response = requests.get(gacetas_url, impersonate=browser)

data = {
    "registros": []
}

if response.status_code == 200:

    # Conjunto para evitar descargar dos veces el mismo enlace
    visited_links = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # NOTA: BeautifulSoup filtra por clase con 'class_=', no con '_class='.
    # Con '_class' el find() nunca encuentra nada, así que este era el bug
    # que hacía que el scraper no bajara ningún archivo.
    buttons_container = soup.find('div', class_="contenedorBotonesTransparencia")

    if buttons_container:
        buttons = buttons_container.find_all('div', class_="botonesTrasparencia")

        # Iterar por cada uno de los botones (de año)
        for button in buttons:
            year_link_tag = button.find('a')
            if not year_link_tag or not year_link_tag.get('href'):
                continue

            year_link = year_link_tag['href']
            if not year_link.startswith('http'):
                year_link = urljoin(domain, year_link)

            print(f"\nProcesando año: {year_link}")
            year_response = requests.get(year_link, impersonate=browser)

            if year_response.status_code != 200:
                print(f"  No se pudo acceder al listado de este año (status {year_response.status_code})")
                continue

            year_soup = BeautifulSoup(year_response.text, parser)

            tbody = year_soup.find('table', class_="contentpaneopen")
            if not tbody:
                print("  No se encontró la tabla de meses para este año.")
                continue

            content_div = tbody.find('div', class_="content")
            months_container = content_div.find('div', class_='features') if content_div else None

            if not months_container:
                print("  No se encontró el contenedor de meses para este año.")
                continue

            months = months_container.find_all('div', class_="feature")

            for month in months:
                ficha = month.find('div', class_="fichaLegislador")
                month_link_tag = ficha.find('a') if ficha else None

                if not month_link_tag or not month_link_tag.get('href'):
                    continue

                month_link = month_link_tag['href']
                if not month_link.startswith('http'):
                    month_link = f"{domain}{month_link}"

                if month_link in visited_links:
                    continue

                print(f"  Descargando gaceta mensual: {month_link}")
                local_pdf_path = pdf_processor.download_pdf(month_link, gm_pdf_dir, browser)

                if local_pdf_path:
                    visited_links.add(month_link)

                    # 1. Leer el PDF
                    raw_text = pdf_processor.extract_text(local_pdf_path)

                    # 2. Limpiar el texto. GMProcessor segmenta por anclas de
                    # línea aislada ("ORDEN DEL DÍA"), así que aquí usamos
                    # clean_text_preserve_lines() en vez de clean_text(): la
                    # versión normal colapsa todos los saltos de línea a
                    # espacios y esas anclas nunca hacen match. clean_text()
                    # no se toca porque GacetaProcessor y otros módulos ya
                    # dependen de su comportamiento actual.
                    clean_text = pdf_processor.clean_text_preserve_lines(raw_text)

                    if clean_text:
                        # 3. Procesar (por ahora sin IA: GMProcessor arma el
                        # registro completo por regex con la estructura de
                        # sesiones.json)
                        new_records = gm_processor.process_file(clean_text, source_file=local_pdf_path)

                        for record in new_records:
                            data['registros'].append(record)
                    else:
                        print(f"  Aviso: no se pudo extraer texto de {local_pdf_path}")
    else:
        print("La estructura de la página cambió, no se encontró el contenedor de botones de años.")

else:
    print(f"No se pudo acceder a la página de gacetas mensuales (status {response.status_code})")

# ==============================================================================
# GUARDADO DEL JSON (sesiones.json)
# ==============================================================================
if data["registros"]:
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, 'sesiones.json')

    try:
        # El formato de referencia (sesiones.json) es un arreglo directo de
        # objetos, sin envoltura {"registros": [...]}, así que guardamos
        # data['registros'] tal cual.
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data['registros'], f, ensure_ascii=False, indent=4)
        print(f"\n✓ JSON guardado exitosamente: {len(data['registros'])} registro(s) -> {json_path}")
    except Exception as e:
        print(f"Error guardando el JSON: {e}")

else:
    print("\nEl proceso terminó, pero no se generaron registros para guardar.")

print("\n=== Ejecución finalizada ===")