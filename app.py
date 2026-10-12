import os
import sys
import runpy
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt
from rich.table import Table
from rich import print

# Inicializar la consola de Rich
console = Console()

# Bandera interna: el .exe se vuelve a lanzar a sí mismo con esta bandera para ejecutar un script
RUN_SCRIPT_FLAG = "--run-script"


def getAppDir():
    """Carpeta raíz de la aplicación: junto al .exe si está empaquetada, o junto a app.py en desarrollo."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# Obtener la ruta base absoluta donde se encuentra app.py (o el .exe)
BASE_DIR = getAppDir()

# Diccionario con los nombres de las bases de datos
BASES_DE_DATOS = {1: "Perfiles", 2: "Votaciones", 3: "Unidad y Participación", 4: "Asistencias", 5: "Comisiones y comites", 6: "Sesiones",
                  7: "Iniciativas y Puntos de Acuerdo"}

def mostrar_menu():
    """Genera y muestra la tabla del menú principal."""
    table = Table(title="[bold cyan]Orquestador de Extracción de Datos[/bold cyan]", show_header=True, header_style="bold magenta")
    table.add_column("Opción", justify="center", style="cyan", no_wrap=True)
    table.add_column("Base de Datos", style="white")
    table.add_column("Estado", justify="center")

    for key, name in BASES_DE_DATOS.items():
        # Marcar visualmente las que ya sabemos que existen
        if key in [1, 2, 3, 4, 5, 6, 7]:
            estado = "[bold green]✓ Disponible[/bold green]"
        else:
            estado = "[dim]No implementado[/dim]"
        table.add_row(str(key), name, estado)

    table.add_row("0", "[bold red]Salir[/bold red]", "")
    console.print(table)


def ensureResultFolders():
    """Crea las carpetas de resultados junto al .exe para que el usuario siempre las encuentre."""
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "outputs"), exist_ok=True)


def showResultsLocation():
    """Muestra dónde se guardan los archivos JSON y Excel."""
    console.print(f"[dim]Archivos JSON: {os.path.join(BASE_DIR, 'data')}[/dim]")
    console.print(f"[dim]Archivos Excel: {os.path.join(BASE_DIR, 'outputs')}[/dim]")


def buildScriptCommand(scriptPath):
    """Arma el comando para ejecutar un script. Empaquetado, el .exe se relanza a sí mismo con la bandera interna."""
    if getattr(sys, "frozen", False):
        return [sys.executable, RUN_SCRIPT_FLAG, scriptPath]
    return [sys.executable, scriptPath]


def runScriptIfRequested():
    """Si el .exe fue lanzado con la bandera interna, ejecuta el script indicado y termina."""
    if len(sys.argv) < 3 or sys.argv[1] != RUN_SCRIPT_FLAG:
        return

    scriptPath = sys.argv[2]
    sys.argv = [scriptPath] + sys.argv[3:]
    sys.path.insert(0, BASE_DIR)
    runpy.run_path(scriptPath, run_name="__main__")
    sys.exit(0)


def ejecutar_scripts(db_number):
    """Ejecuta secuencialmente el scrapper y luego el writer correspondientes."""
    scrapper_path = os.path.join(BASE_DIR, "scrappers", f"script{db_number}.py")
    writer_path = os.path.join(BASE_DIR, "writers", f"script{db_number}.py")
    verifier_path = os.path.join(BASE_DIR, "verification", f"script{db_number}.py")

    # 1. Validar que ambos archivos existan antes de ejecutar nada
    if not os.path.exists(scrapper_path):
        console.print(f"[bold red]✗ Error:[/bold red] No se encontró el archivo scrapper en: {scrapper_path}")
        return
    if not os.path.exists(writer_path):
        console.print(f"[bold red]✗ Error:[/bold red] No se encontró el archivo writer en: {writer_path}")
        return

    console.print(Panel(f"[bold blue]Iniciando extracción para la Base de Datos {db_number}: {BASES_DE_DATOS[db_number]}[/bold blue]"))

    # 2. Ejecutar Scrapper
    try:
        with console.status(f"[bold yellow]Ejecutando scrappers/script{db_number}.py...[/bold yellow]", spinner="dots"):
            # Desarrollo: usa el mismo Python del entorno virtual. Empaquetado: el .exe se relanza a sí mismo
            subprocess.run(buildScriptCommand(scrapper_path), check=True)
        console.print(f"[bold green]✓ Scrapper (script{db_number}.py) finalizado correctamente.[/bold green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Error fatal durante la ejecución del Scrapper.[/bold red]\nDetalles: {e}")
        return  # Si falla el scrapper, detenemos el proceso y no ejecutamos el writer

    # 3. Ejecutar Writer
    try:
        with console.status(f"[bold yellow]Ejecutando writers/script{db_number}.py...[/bold yellow]", spinner="dots"):
            subprocess.run(buildScriptCommand(writer_path), check=True)
        console.print(f"[bold green]✓ Writer (script{db_number}.py) finalizado correctamente.[/bold green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Error fatal durante la ejecución del Writer.[/bold red]\nDetalles: {e}")
        return

    # 4. Ejecutar Verificador (opcional: solo si existe)
    if os.path.exists(verifier_path):
        try:
            # Sin status/spinner por si el verificador imprime resultados en pantalla
            console.print(f"[bold yellow]Ejecutando verification/script{db_number}.py...[/bold yellow]")
            subprocess.run(buildScriptCommand(verifier_path), check=True)
            console.print(f"[bold green]✓ Verificador (script{db_number}.py) finalizado correctamente.[/bold green]")
        except subprocess.CalledProcessError as e:
            console.print(f"[bold red]✗ Error durante la ejecución del Verificador.[/bold red]\nDetalles: {e}")
            return
    else:
        console.print(f"[dim]No hay verificador para la base de datos {db_number}; se omite este paso.[/dim]")

    console.print(f"\n[bold green]¡Proceso completo para la base de datos {db_number} finalizado con éxito! 🎉[/bold green]\n")


def main():
    # Las rutas relativas de los scripts (por ejemplo 'data') siempre parten de la carpeta de la aplicación
    os.chdir(BASE_DIR)
    ensureResultFolders()

    subprocess.run('cls' if os.name == 'nt' else 'clear', shell=True)

    while True:
        mostrar_menu()
        showResultsLocation()

        # Solicitar opción al usuario de forma segura con Rich
        opcion = IntPrompt.ask("\n[bold cyan]Selecciona una base de datos para extraer (0-7)[/bold cyan]", choices=[str(i) for i in range(8)])

        if opcion == 0:
            console.print("[bold red]Saliendo del orquestador. ¡Hasta pronto![/bold red]")
            break

        # Limpiar pantalla y ejecutar
        subprocess.run('cls' if os.name == 'nt' else 'clear', shell=True)
        ejecutar_scripts(opcion)

        # Pausa antes de volver al menú
        input("\nPresiona ENTER para volver al menú principal...")
        subprocess.run('cls' if os.name == 'nt' else 'clear', shell=True)


if __name__ == "__main__":
    # Si es una relanzada interna del .exe, ejecuta el script y termina sin mostrar el menú
    runScriptIfRequested()

    try:
        main()
    except KeyboardInterrupt:
        print("\n")
        console.print("[bold red]Ejecución interrumpida por el usuario. Saliendo...[/bold red]")
        sys.exit(0)