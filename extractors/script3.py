import sys
import os

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import time # Añadido para la pausa ética
from rich import print as rprint

from lib.PDFProcessor import PDFProcessor

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345'
browser = 'edge'
parser = 'html.parser'

pdf_processor = PDFProcessor()

rprint("[cyan]Accediendo a la página principal de actas...[/]")

response = requests.get(url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

if response.status_code == 200:
    data = {
        "registros" : []
    }

    # Crear un diccionario para contener las actas y descargalas solo UNA vez
    downloaded_minutes = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Obtener el conjunto de subcategorias (clasificaciones de las actas)
    subcategories_section = soup.find('div', class_='itemListSubCategories')

    # Obtener todas las subcategorias
    subcategories = subcategories_section.find_all('div', class_='fichaLegislador')

    if subcategories:
        for sc in subcategories:
            header = sc.find('h4')
            print(f"\nProcesando la subcategoría: {header.text.strip()}")

            # Acceder al enlace de la subcategoría
            sc_link = f"{domain}{header.find('a')['href']}"

            sc_response = requests.get(sc_link, impersonate=browser)

            if sc_response.status_code == 200:
                sc_soup = BeautifulSoup(sc_response.text, parser)

                # Obtener todas las actas de la subcategoría
                sc_item_list = sc_soup.find('div', class_="itemList")

                # Definir y crear la carpeta de guardado (Asegúrate de importar 'os' al inicio del script)
                pdf_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'pdfs', 'minutes')
                os.makedirs(pdf_dir, exist_ok=True)

                # La página (html) divide sus actas en dos secciones
                # Acceder a las dos secciones para obtener a todas las actas
                # Extracción segura: find puede retornar None si la sección no existe en el HTML
                sc_leading = sc_item_list.find('div', id="itemListLeading")
                sc_primary = sc_item_list.find('div', id="itemListPrimary")

                # Obtener las listas, pero validando que la sección exista primero
                leading_minutes = sc_leading.find_all('div', class_="itemContainer") if sc_leading else []
                primary_minutes = sc_primary.find_all('div', class_="itemContainer") if sc_primary else []

                all_minutes = leading_minutes + primary_minutes

                for minute in all_minutes:
                    a_tag = minute.find('a')
                    if not a_tag:
                        continue

                    download_link = f"{domain}{a_tag['href']}"

                    if download_link in downloaded_minutes:
                        continue

                    print(f'\nIniciando descarga de: {download_link}')

                    # Llamar a función de descarga de archivo
                    local_pdf_path = pdf_processor.download_pdf(download_link, pdf_dir, browser)

                    if local_pdf_path: 
                        # Registramos lo que ya procesamos
                        downloaded_minutes.add(download_link)

    else:
        print("La estructura de la página cambió, no se encotraron subcategorías de actas para analizar.")
