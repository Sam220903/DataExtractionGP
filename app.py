import os
import sys
import subprocess
from rich.console import Console
from rich.panel import Panel
from rich.prompt import IntPrompt
from rich.table import Table
from rich import print

# Inicializar la consola de Rich
console = Console()

# Obtener la ruta base absoluta donde se encuentra app.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Diccionario con los nombres de las bases de datos
BASES_DE_DATOS = {
    1: "Base 1 (Pendiente)",
    2: "Votaciones",
    3: "Unidad y Participación",
    4: "Base 4 (Pendiente)",
    5: "Base 5 (Pendiente)",
    6: "Base 6 (Pendiente)",
    7: "Base 7 (Pendiente)"
}

def mostrar_menu():
    """Genera y muestra la tabla del menú principal."""
    table = Table(title="[bold cyan]Orquestador de Extracción de Datos[/bold cyan]", 
                  show_header=True, header_style="bold magenta")
    table.add_column("Opción", justify="center", style="cyan", no_wrap=True)
    table.add_column("Base de Datos", style="white")
    table.add_column("Estado", justify="center")

    for key, name in BASES_DE_DATOS.items():
        # Marcar visualmente las que ya sabemos que existen
        if key in [2, 3]:
            estado = "[bold green]✓ Disponible[/bold green]"
        else:
            estado = "[dim]No implementado[/dim]"
        
        table.add_row(str(key), name, estado)
    
    table.add_row("0", "[bold red]Salir[/bold red]", "")
    
    console.print(table)

def ejecutar_scripts(db_number):
    """Ejecuta secuencialmente el scrapper y luego el writer correspondientes."""
    scrapper_path = os.path.join(BASE_DIR, "scrappers", f"script{db_number}.py")
    writer_path = os.path.join(BASE_DIR, "writers", f"script{db_number}.py")

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
            # Usamos sys.executable para asegurar que use el mismo entorno virtual de Python
            subprocess.run([sys.executable, scrapper_path], check=True)
        console.print(f"[bold green]✓ Scrapper (script{db_number}.py) finalizado correctamente.[/bold green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Error fatal durante la ejecución del Scrapper.[/bold red]\nDetalles: {e}")
        return # Si falla el scrapper, detenemos el proceso y no ejecutamos el writer

    # 3. Ejecutar Writer
    try:
        with console.status(f"[bold yellow]Ejecutando writers/script{db_number}.py...[/bold yellow]", spinner="dots"):
            subprocess.run([sys.executable, writer_path], check=True)
        console.print(f"[bold green]✓ Writer (script{db_number}.py) finalizado correctamente.[/bold green]")
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]✗ Error fatal durante la ejecución del Writer.[/bold red]\nDetalles: {e}")
        return

    console.print(f"\n[bold green]¡Proceso completo para la base de datos {db_number} finalizado con éxito! 🎉[/bold green]\n")

def main():
    subprocess.run('cls' if os.name == 'nt' else 'clear', shell=True)
    
    while True:
        mostrar_menu()
        
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
    try:
        main()
    except KeyboardInterrupt:
        print("\n")
        console.print("[bold red]Ejecución interrumpida por el usuario. Saliendo...[/bold red]")
        sys.exit(0)