# Descripción del proyecto
Repositorio para la extracción de datos solicitados por el área de Bien Común en la UPAEP 

<br>

# Estructura del proyecto

```
/DataExtractionGP
│
├── /config                 # Credenciales de las fuentes y rutas de salida
|
├── /data                   # Archivos JSON de salida de los extractores
│
├── /core                   # Código compartido
│   ├── excel_writer.py     # Lógica centralizada para exportar a Excel
│   ├── logger.py           # Para registrar errores y éxitos
│   └── base.py   # Plantilla base para los 7 scripts
│
├── /extractors             # Los 7 scripts individuales
│   ├── script1.py
│   ├── script2.py
│   └── ...
|
├── /lib                    # Funciones reusables dentro del proyecto
|
├── /outputs                # (Opcional) Carpeta local donde se guardan los Excel
│
└── requirements.txt        # Dependencias (pandas, openpyxl, etc.)
```
<br>

# Contenido de los scripts

|Nombre del archivo|Base de datos|Fuente|Descripción|
| :--- | :---: | :---: | ---: |
|script1.py|Perfiles legislativos||Datos básicos de cada diputado del congreso de Puebla|
|script2.py|Votaciones|https://www.congresopuebla.gob.mx/index.php?option=com_content&view=article&id=12718|Votaciones de los diputados en sesiones sobre acuerdos o propuestas|
|script3.py|Unidad y participación|https://www.congresopuebla.gob.mx/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345|Desglose de resultados de votaciones en cada evento del congreso|
|script4.py|Sesiones y cronometría|...|...|
|script5.py|Iniciativas y puntos de acuerdo|...|...|
|script6.py|Comisiones legislativas|...|...|
|script7.py|Registro de asistencias|...|...|
