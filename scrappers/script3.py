import sys
import os

# Añadir la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import time
from dotenv import load_dotenv

from lib.PDFProcessor import PDFProcessor
from extractors.gaceta_extractor import GacetaExtractor # Asegúrate de que el nombre del archivo coincida

# Cargar variables de entorno
load_dotenv()
api_key = os.getenv("GROQ_API_KEY")

if not api_key:
    print("Error: No se encontró la api_key en el archivo .env")
    exit()

# Pasar la api_key a la instancia del extractor
g_extractor = GacetaExtractor(api_key=api_key)

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
url = f'{domain}/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345'
browser = 'edge'
parser = 'html.parser'

pdf_processor = PDFProcessor()

print("Accediendo a la página principal de actas...")
response = requests.get(url, impersonate=browser)

if response.status_code == 200:
    data = {
        "registros": []
    }

    downloaded_minutes = set()
    
    # LIMITE DE REGISTROS PARA TESTING
    LIMITE_REGISTROS = 5
    contador_registros = 0

    soup = BeautifulSoup(response.text, parser)
    subcategories_section = soup.find('div', class_='itemListSubCategories')

    if subcategories_section:
        subcategories = subcategories_section.find_all('div', class_='fichaLegislador')
        
        if subcategories:
            for sc in subcategories:
                # Verificar si alcanzamos el limite antes de procesar nueva subcategoría
                if contador_registros >= LIMITE_REGISTROS:
                    break
                    
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
                        # Verificar si alcanzamos el limite de registros
                        if contador_registros >= LIMITE_REGISTROS:
                            print(f"\nLímite de {LIMITE_REGISTROS} registros alcanzado. Deteniendo análisis para testing...")
                            break
                        
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
                            contador_registros += 1
                            
                            print(f"Procesando archivo {contador_registros} de {LIMITE_REGISTROS}")
                            
                            # --- AQUÍ EMPIEZA LA MAGIA DE LA INTEGRACIÓN ---
                            
                            # 1. Leer el PDF
                            raw_text = pdf_processor.extract_text(local_pdf_path)
                            
                            # 2. Limpiar el texto
                            clean_text = pdf_processor.clean_text(raw_text)
                            
                            if clean_text:
                                # 3. Mandar el texto limpio a la IA para extraer los datos
                                nuevos_registros = g_extractor.get_data(clean_text)
                                
                                # 4. Si la IA encontró datos, los sumamos a nuestro diccionario principal
                                if nuevos_registros:
                                    data["registros"].extend(nuevos_registros)
                                    
    else:
        print("La estructura de la página cambió, no se encotraron subcategorías de actas para analizar.")

    # ==============================================================
    # GUARDADO FINAL DEL JSON CUANDO TERMINAN TODAS LAS EXTRACCIONES
    # ==============================================================
    if data["registros"]:
        output_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        os.makedirs(output_dir, exist_ok=True)
        json_path = os.path.join(output_dir, 'actas_extraidas_ia.json')
        
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            print(f"\n¡Éxito total! {len(data['registros'])} registros guardados en {json_path}")
        except Exception as e:
            print(f"Error al guardar el JSON final: {e}")
    else:
        print("\nEl proceso terminó, pero no se extrajeron registros para guardar.")