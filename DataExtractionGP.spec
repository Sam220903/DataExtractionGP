# -*- mode: python ; coding: utf-8 -*-
# Spec de PyInstaller para DataExtractionGP (modo carpeta / onedir)
#
# Solo app.py se compila dentro del .exe. Las carpetas scrappers, writers, verification,
# lib, extractors y prompts se copian como archivos normales junto al .exe (ver build.bat),
# de modo que todas las rutas basadas en __file__ y '..' funcionen igual que en desarrollo.
# Por eso aquí se declaran todas las librerías que esos archivos externos necesitan.

from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = []
binaries = []
hiddenImports = []

# Paquetes con binarios o datos propios que PyInstaller no detecta por sí solo
packagesToCollect = [
    'curl_cffi',
    'google.genai',
    'google.auth',
    'selenium',
    'pymupdf',
    'certifi',
]

for packageName in packagesToCollect:
    packageDatas, packageBinaries, packageHiddenImports = collect_all(packageName)
    datas += packageDatas
    binaries += packageBinaries
    hiddenImports += packageHiddenImports

# Zonas horarias (necesarias en Windows para zoneinfo / pandas)
datas += collect_data_files('tzdata')

# Librerías de terceros que solo usan los archivos externos
hiddenImports += [
    'bs4', 'soupsieve', 'pandas', 'numpy', 'openpyxl', 'et_xmlfile',
    'requests', 'urllib3', 'idna', 'charset_normalizer', 'cffi', 'cryptography',
    'dotenv', 'dateutil', 'tenacity', 'httpx', 'pydantic', 'websockets',
    'websocket', 'trio', 'trio_websocket', 'socks', 'fitz', 'rich',
]

# Módulos de la librería estándar que solo usan los archivos externos
hiddenImports += [
    'argparse', 'glob', 'json', 're', 'time', 'unicodedata', 'datetime', 'shutil',
    'tempfile', 'csv', 'collections', 'itertools', 'functools', 'pathlib',
    'urllib.parse', 'urllib.request', 'hashlib', 'base64', 'logging', 'dataclasses',
    'difflib', 'statistics', 'textwrap', 'zipfile', 'io', 'copy', 'random', 'math',
    'string', 'traceback', 'concurrent.futures', 'threading', 'queue', 'sqlite3',
    'html', 'html.parser', 'zoneinfo', 'decimal', 'fractions', 'typing', 'enum',
    'contextlib', 'warnings', 'platform', 'ssl', 'socket', 'email', 'mimetypes',
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenImports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'IPython', 'pytest'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='DataExtractionGP',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # La aplicación es interactiva (menú en consola), no debe ocultarse la ventana
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='DataExtractionGP',
)
