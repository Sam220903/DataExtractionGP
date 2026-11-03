# script7.py
# Scrapper de Gacetas Legislativas -> Iniciativas_PA.json
# Basado en script6.py; cambia GMProcessor por GacetaIPAProcessor.
#
# Si la IA agota su límite gratuito diario: se imprime la alerta, se deja de
# recorrer gacetas, se guarda el JSON con lo extraído y el script sale con
# código 3 (app.py lo detecta, avisa que hay que esperar un día y no corre el writer).

import sys
import os
# Añadimos la carpeta principal al path para poder importar desde lib/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import traceback
from curl_cffi import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from lib.PDFProcessor import PDFProcessor
from extractors.GacetaIPAProcessor import GacetaIPAProcessor

# Código de salida con el que se avisa a app.py que se agotó el límite de la IA
# (debe coincidir con EXIT_CODE_QUOTA de app.py)
EXIT_CODE_QUOTA = 3

# ------------------------------------------------------------------------------
# Parámetros iniciales
# ------------------------------------------------------------------------------
domain = 'https://www.congresopuebla.gob.mx'
gacetas_url = f'{domain}/index.php?option=com_content&view=article&id=12868'
browser = "edge"
parser = "html.parser"

base_dir = os.path.dirname(__file__)
data_dir = os.path.join(base_dir, '..', 'data')
os.makedirs(data_dir, exist_ok=True)

# JSON ya extraído con el que se empatan C1..C6 (catálogo de comisiones)
comisiones_path = os.path.join(data_dir, 'comisiones_contenido.json')
# perfiles.json: nombre oficial y partido de cada diputado
perfiles_path = os.path.join(data_dir, 'perfiles.json')
# Caché de resultados de IA (si se interrumpe la corrida no se vuelve a pagar por lo ya resumido)
ia_cache_path = os.path.join(data_dir, 'ia_cache_iniciativas.json')

# Rutas de descarga de gacetas legislativas mensuales
gm_pdf_dir = os.path.join(data_dir, 'pdfs', 'gms')
os.makedirs(gm_pdf_dir, exist_ok=True)

# Salida
output_json_path = os.path.join(data_dir, 'Iniciativas_PA.json')

pdf_processor = PDFProcessor()
# ------------------------------------------------------------------------------
# ¿Usar IA? Solo regex (rápido, sin costo; 'Contenido' y 'Comentario' quedan en null)
# o regex + IA (Gemini resume 'Contenido' de las Iniciativas y llena 'Comentario' en
# casos extraordinarios; requiere GEMINI_API_KEY en .env).
# ------------------------------------------------------------------------------
while True:
    respuesta = input("¿Usar IA para llenar 'Contenido' y 'Comentario'? (Si/No): ").strip().lower()
    if respuesta in ('s', 'si', 'sí'):
        usar_ia = True
        break
    if respuesta in ('n', 'no'):
        usar_ia = False
        break
    print("  Respuesta no válida: escribe Si (s) o No (n).")

print(f"Modo: {'regex + IA (Gemini)' if usar_ia else 'solo regex (sin IA)'}")

ipa_processor = GacetaIPAProcessor(
    comisiones_path=comisiones_path,
    perfiles_path=perfiles_path,
    usar_ia=usar_ia,
    ia_cache_path=ia_cache_path,
)

data = {"registros": []}
limite_ia_agotado = False


def save_json():
    """Guarda Iniciativas_PA.json (arreglo directo de objetos, sin envoltura)."""
    if not data["registros"]:
        return False
    try:
        # Los campos auxiliares (_fecha, _src) usados por finalize() no se exportan
        limpios = [{k: v for k, v in r.items() if not k.startswith('_')}
                   for r in data["registros"]]
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(limpios, f, ensure_ascii=False, indent=4)
        print(f"\n✓ JSON guardado: {len(data['registros'])} registro(s) -> {output_json_path}")
        return True
    except Exception as e:
        print(f"Error guardando el JSON: {e}")
        return False


# ----------------------------------------- Extracción de gacetas mensuales -----------------------------------------
print("Accediendo a la página de gacetas mensuales...")

response = requests.get(gacetas_url, impersonate=browser)

if response.status_code == 200:

    # Conjunto para evitar descargar dos veces el mismo enlace
    visited_links = set()

    soup = BeautifulSoup(response.text, parser)
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

                if not local_pdf_path:
                    continue

                visited_links.add(month_link)

                # Un fallo en una gaceta (800-3000 páginas) no debe tirar
                # toda la corrida: se registra y se continúa con la siguiente.
                try:
                    # 1. Leer el PDF
                    raw_text = pdf_processor.extract_text(local_pdf_path)

                    # 2. NO se usa clean_text_preserve_lines(): esa función borra los
                    # pies "Pag. N", que GacetaIPAProcessor necesita para
                    # reconstruir páginas y detectar las páginas divisoras
                    # (ACTA, INICIATIVAS PRESENTADAS, DICTÁMENES...). El propio
                    # procesador limpia el ruido que le estorba.
                    clean_text = raw_text

                    # 3. Procesar la gaceta completa -> lista de registros
                    #    (si la IA se agota a media gaceta, devuelve lo procesado hasta ahí)
                    new_records = ipa_processor.process_file(clean_text, source_file=local_pdf_path)
                    data["registros"].extend(new_records)
                    print(f"    -> {len(new_records)} registro(s) en esta gaceta")

                    # Checkpoint tras cada gaceta (corridas largas)
                    save_json()

                except Exception as e:
                    print(f"  Error procesando {local_pdf_path}: {e}")
                    traceback.print_exc()

                # Límite gratuito de la IA agotado: no tiene caso seguir con más gacetas
                if ipa_processor.ia_quota_exhausted:
                    limite_ia_agotado = True
                    break

            if limite_ia_agotado:
                break
    else:
        print("La estructura de la página cambió, no se encontró el contenedor de botones de años.")

else:
    print(f"No se pudo acceder a la página de gacetas mensuales (status {response.status_code})")

# ==============================================================================
# POST-PROCESO ENTRE GACETAS
# ==============================================================================
# Estatus / Fecha de aprobación / F-C-A de una iniciativa dependen de un
# dictamen que normalmente aparece en OTRA sesión (a veces otra gaceta), por
# lo que solo pueden resolverse cuando ya se procesaron todas.
# (Si la corrida se detuvo por el límite de la IA, se resuelve con lo disponible.)
if data["registros"] and hasattr(ipa_processor, "finalize"):
    data["registros"] = ipa_processor.finalize(data["registros"])

# ==============================================================================
# GUARDADO FINAL DEL JSON (Iniciativas_PA.json)
# ==============================================================================
if not save_json():
    print("\nEl proceso terminó, pero no se generaron registros para guardar.")

# ==============================================================================
# ALERTA: LÍMITE GRATUITO DE LA IA AGOTADO
# ==============================================================================
if limite_ia_agotado:
    print("\n" + "=" * 70)
    print("ALERTA: SE AGOTÓ EL LÍMITE GRATUITO DIARIO DE LA IA (Gemini)")
    print("=" * 70)
    print(f"La extracción se detuvo. Se guardó lo extraído ({len(data['registros'])} registro(s)) en:")
    print(f"  {output_json_path}")
    print("Debes ESPERAR UN DÍA para que se restablezca el límite gratuito y volver a ejecutar.")
    print("Lo ya resumido quedó en la caché de IA: al reanudar solo se enviarán las iniciativas pendientes.")
    print("=" * 70)
    sys.exit(EXIT_CODE_QUOTA)

print("\n=== Ejecución finalizada ===")