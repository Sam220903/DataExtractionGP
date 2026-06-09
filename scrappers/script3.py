import sys
import os

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import time
from dotenv import load_dotenv
from datetime import datetime

from lib.PDFProcessor import PDFProcessor
from extractors.GacetaProcessor import GacetaProcessor
from lib.FolioManager import FolioManager 

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345'
browser = 'edge'
parser = 'html.parser'

pdf_processor = PDFProcessor()
gaceta_processor = GacetaProcessor()

print("Accediendo a la página principal de actas...")
response = requests.get(url, impersonate=browser)

data = {
    "registros": []
}

if response.status_code == 200:
    downloaded_minutes = set()
    soup = BeautifulSoup(response.text, parser)
    subcategories_section = soup.find('div', class_='itemListSubCategories')

    if subcategories_section:
        subcategories = subcategories_section.find_all('div', class_='fichaLegislador')
        
        if subcategories:
            for sc in subcategories:

                header = sc.find('h4')
                print(f"\nProcesando la subcategoría: {header.text.strip()}")

                sc_link = f"{domain}{header.find('a')['href']}"
                sc_response = requests.get(sc_link, impersonate=browser)

                if sc_response.status_code == 200:
                    sc_soup = BeautifulSoup(sc_response.text, parser)
                    sc_item_list = sc_soup.find('div', class_="itemList")
                    
                    if not sc_item_list:
                        continue

                    pdf_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'pdfs', 'minutes')
                    os.makedirs(pdf_dir, exist_ok=True)

                    sc_leading = sc_item_list.find('div', id="itemListLeading")
                    sc_primary = sc_item_list.find('div', id="itemListPrimary")

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
                        local_pdf_path = pdf_processor.download_pdf(download_link, pdf_dir, browser)

                        if local_pdf_path: 
                            downloaded_minutes.add(download_link)                            
                            
                            # 1. Leer el PDF
                            raw_text = pdf_processor.extract_text(local_pdf_path)
                            
                            # 2. Limpiar el texto
                            clean_text = pdf_processor.clean_text(raw_text)

                            if clean_text:
                                # 3. PHASE 1 (EXTRACTION): Pasamos folio_manager como None
                                new_records = gaceta_processor.process_file(clean_text, folio_manager=None)

                                for record in new_records:
                                    data['registros'].append(record)
                                    
    else:
        print("La estructura de la página cambió, no se encotraron subcategorías de actas para analizar.")

# ==============================================================================
# PHASE 2: SORTING & FOLIATION
# ==============================================================================
if data["registros"]:
    print("\n=== Phase 2: Chronological Sorting & Foliation ===")
    
    # Sort chronologicaly based on the date
    data["registros"].sort(key=lambda x: x.get("Fecha") or "")
    
    # Initialize a fresh FolioManager
    folio_manager = FolioManager()
    
    period_map = {"Primer periodo": 1, "Segundo periodo": 2, "Tercer periodo": 3}
    year_map = {"Primer año": 1, "Segundo año": 2, "Tercer año": 3}
    
    for record in data["registros"]:
        record_date = record.get("Fecha")
        period_str = record.get("Periodo", "")
        year_str = record.get("Año Legislatura", "")
        
        # Safe mapping to integers for the FolioManager
        period_int = period_map.get(period_str, 1)
        year_int = year_map.get(year_str, 1)
        
        # Generate linear folios
        folio_leg, folio_per = folio_manager.generate_folios(record_date, year_int, period_int)
        
        # Stamp the correct folios
        record["Folio legislatura"] = folio_leg
        record["Folio periodo"] = folio_per
        
    print("✓ Foliation completed.")

    # ==========================================================================
    # PHASE 3: SAVE JSON
    # ==========================================================================
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, 'unidad_participacion_test.json')
    
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"✓ JSON saved successfully: {len(data['registros'])} records -> {json_path}")
    except Exception as e:
        print(f"Error saving JSON: {e}")

    print("\n=== Execution Finished ===")

else:
    print("\nEl proceso terminó, pero no se extrajeron registros para guardar.")