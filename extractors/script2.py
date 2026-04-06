import sys
import os
# Le decimos a Python que añada la carpeta principal al directorio de búsqueda
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from curl_cffi import requests
from bs4 import BeautifulSoup
import json
import time # Añadido para la pausa ética
from lib.DateFormatter import DateFormatter
from lib.EventClassifier import EventClassifier

# Establecer parámetros iniciales
domain = 'https://www.congresopuebla.gob.mx'
url = f'{domain}/index.php?option=com_content&view=article&id=12718'
browser = "edge"
parser = "html.parser"

date_formatter = DateFormatter()
classifier = EventClassifier()

print("Accediendo a la página de votaciones")

response = requests.get(url, impersonate=browser)

# Iniciar un contador de registros
total_records = 0

# Acceder a la pantalla principal de votaciones
if response.status_code == 200:
    # Crear diccionario para contener los datos
    data = {
        "registros" : []
    }

    # Crear un conjunto paracontener los enlaces y visitarlos uns sola vez
    visited_links = set()

    # Mapeo de página
    soup = BeautifulSoup(response.text, parser)

    # Obtener la segunda etiqueta 'center', que contiene los botones con los años
    centers = soup.find_all('center')
    
    if len(centers) > 1:
        years_container = centers[1]

        # Obtener los años disponibles
        years = years_container.find_all('a')

        # Iterar por cada año
        for year in years:
            # Limpiamos el texto para sacar el año puro (2024, 2025...)
            year_text = year.text.strip()
            # Unimos el dominio para tener la URL completa y lista para ser visitada
            full_year_link = f"{domain}{year['href']}" 
            
            print(f"\nProcesando el año: {year_text}")
            
            # Acceder a los registros de ese año
            year_page_response = requests.get(full_year_link, impersonate=browser)
            
            if year_page_response.status_code == 200:
                year_soup = BeautifulSoup(year_page_response.text, parser)

                # Obtener todas las tablas de asistencia (son varias porque se dividen por meses)
                tables = year_soup.find_all('table', class_='tablaAsistencia')


                # Variables de control para determiar las votaciones que se hacen en un día
                previous_date = ''
                day_votation_counter = 0

                for table in tables:
                    # Seleccionar el cuerpo de la tabla para eliminar margen de error
                    tbody = table.find('tbody')
                    
                    # Validación: Asegurarnos de que el tbody existe antes de buscar adentro
                    if not tbody:
                        continue
                    
                    # Obtener todos los registros de la tabla, registro por registro añadirlo al JSON
                    records = tbody.find_all('tr')

                    for record in records:
                        # Obtener el enlace al registro de la votación
                        # Todas las celdas contienen una etiqueta 'a' con el mismo enlace de redirección
                        # Por ende, es indiferente cual celda se toma para esta referencia, en este caso se toma la primera
                        record_a = record.find('a')
                        
                        # Validación: Si la fila no tiene etiqueta 'a', la ignoramos
                        if not record_a or 'href' not in record_a.attrs:
                            continue
                            
                        # Corrección: Uso de comillas dobles por fuera y simples por dentro para no cortar el string
                        full_record_link =  f"{domain}{record_a['href']}"

                        # Verificar que no sea un enlace previamente visitado, en caso de serlo, ignorarlo
                        if full_record_link in visited_links:
                            continue

                        visited_links.add(full_record_link)
                        
                        record_page_response = requests.get(full_record_link, impersonate=browser)

                        # Corrección: Añadido .status_code para que la validación funcione
                        if record_page_response.status_code == 200:
                            record_soup = BeautifulSoup(record_page_response.text, parser)

                            record_header = record_soup.find('div', class_='cabecera-tema')
                            
                            # Validación: Confirmar que la página cargó bien y tiene la cabecera
                            if record_header:
                                record_ps = record_header.find_all('p') 
                                
                                # Validación: Evitar IndexError si faltan párrafos
                                if len(record_ps) >= 3:
                                    
                                    # Captura de todos los datos a exportar
                                    topic = record_header.find('h2').text.strip()
                                    date = record_ps[0].find('strong').next_sibling.strip()
                                    date = date_formatter.format(date)      # Formatear la fecha a dd/mm/aa
                                    session = record_ps[2].find('strong').next_sibling.strip()
                                    description = record_ps[1].find('strong').next_sibling.strip()

                                    # Obtener número de votación del día
                                    # Comparar si se esta evaluando un mismo día
                                    if previous_date == date:
                                        day_votation_counter += 1
                                    else: 
                                        day_votation_counter = 1

                                    # Obtener el tipo de votación (Iniciativa - 1, Punto de Acuerdo - 2)
                                    vote_type = classifier.classify_vote(description)

                                    # Obtener el periodo en el que se hizo la votación
                                    period = classifier.classify_per_period(date)

                                    new_record = {
                                        "tema" : topic,
                                        "fecha" : date,
                                        "sesion" : session,
                                        "votacion" : day_votation_counter,
                                        "descripcion" : description,
                                        "tipo" : vote_type,
                                        "periodo": period,
                                        "votaciones" : []
                                    }

                                    data["registros"].append(new_record)
                                    print(f"Registro extraído: {new_record['tema'][:50]}...")
                                    total_records += 1

                                    previous_date = date
                        
                        # Pausa de 1 segundo para no saturar el servidor del Congreso
                        time.sleep(1)

        with open('votaciones.json', 'w', encoding='utf-8') as file:
            # json.dump convierte tu diccionario de Python a formato JSON
            # indent=4 lo formatea bonito para que sea legible por humanos
            json.dump(data, file, ensure_ascii=False, indent=4)

        print("\n¡Archivo JSON guardado con éxito!")
        print(f"\nRegistros extraídos: {total_records}")

    else:
        print("La estructura de la página cambió, no se encontró el contenedor de años.")
else:
    print(f"Error accediendo a la página inicial: {response.status_code}")