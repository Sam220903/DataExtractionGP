import os
import subprocess
import sys

# Obtiene la ruta del directorio donde está ubicado script5.py (carpeta 'scrappers')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Lista de scripts a ejecutar en orden
scripts = ["script5_1.py", "script5_2.py", "script5_3.py"]


def ejecutar_scripts():
	for script in scripts:
		# Construye la ruta absoluta a cada script hijo
		script_path = os.path.join(BASE_DIR, script)

		print(f"\n{'=' * 50}\nIniciando ejecución de: {script}\n{'=' * 50}\n")
		try:
			# Ejecuta el script pasando su ruta absoluta
			resultado = subprocess.run([sys.executable, script_path], check=True)
			print(f"\nFinalizó {script} con éxito (Código {resultado.returncode}).")
		except subprocess.CalledProcessError as e:
			print(f"\nError al ejecutar {script}: ocurrió un fallo con el código de salida {e.returncode}.")
			# Se interrumpe la cadena si falla uno de los scripts
			break
		except FileNotFoundError:
			print(f"\nError: No se encontró el archivo {script} en {script_path}.")
			break


if __name__ == "__main__":
	ejecutar_scripts()