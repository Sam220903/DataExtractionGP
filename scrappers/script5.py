import os
import subprocess
import sys

# Obtiene la ruta del directorio donde está ubicado script5.py (carpeta 'scrappers')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Bandera interna de app.py: el .exe se relanza a sí mismo con ella para ejecutar un script
# (debe coincidir con RUN_SCRIPT_FLAG de app.py)
RUN_SCRIPT_FLAG = "--run-script"

# Código de salida con el que un scrapper avisa que se agotó el límite de la IA
# (debe coincidir con EXIT_CODE_QUOTA de app.py)
EXIT_CODE_QUOTA = 3

# Los scripts hijos imprimen su progreso sin búfer, para que se vea en cuanto ocurre
CHILD_ENV = dict(os.environ, PYTHONUNBUFFERED="1")

# Lista de scripts a ejecutar en orden
scripts = ["script5_1.py", "script5_2.py", "script5_3.py"]


def build_command(script_path):
    """Dentro del .exe, sys.executable es el propio .exe: hay que relanzarlo con la bandera
    interna (sin ella abriría otra vez el menú de app.py y el proceso parecería colgado)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_SCRIPT_FLAG, script_path]
    return [sys.executable, script_path]


def ejecutar_scripts():
    for script in scripts:
        # Construye la ruta absoluta a cada script hijo
        script_path = os.path.join(BASE_DIR, script)

        print(f"\n{'=' * 50}\nIniciando ejecución de: {script}\n{'=' * 50}\n", flush=True)
        try:
            # Ejecuta el script pasando su ruta absoluta
            resultado = subprocess.run(build_command(script_path), check=True, env=CHILD_ENV)
            print(f"\nFinalizó {script} con éxito (Código {resultado.returncode}).", flush=True)
        except subprocess.CalledProcessError as e:
            if e.returncode == EXIT_CODE_QUOTA:
                # Se agotó el límite de la IA: se propaga el código para que app.py avise
                # que hay que esperar un día y no ejecute el writer
                print(f"\n{script} se detuvo: se agotó el límite gratuito de la IA.", flush=True)
                sys.exit(EXIT_CODE_QUOTA)
            print(f"\nError al ejecutar {script}: ocurrió un fallo con el código de salida {e.returncode}.", flush=True)
            # Se interrumpe la cadena si falla uno de los scripts
            break
        except FileNotFoundError:
            print(f"\nError: No se encontró el archivo {script} en {script_path}.", flush=True)
            break


if __name__ == "__main__":
    ejecutar_scripts()