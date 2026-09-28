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
|	├── /pdfs				# Archivos pdf de gacetas, versiones estenográficas, actas, etc.
|	├── /photos				# Fotos de los diputados extraidas para la base de perfiles
│
├── /core                   # Código compartido
│   ├── excel_writer.py     # Lógica centralizada para exportar a Excel
│   ├── logger.py           # Para registrar errores y éxitos
│   └── base.py   # Plantilla base para los 7 scripts
│
├── /scrappers             	# Los 7 scripts individuales de recolección de datos (web scrapping)
│   ├── script1.py
│   ├── script2.py
│   └── ...
│
├── /writers            # Los 7 scripts individuales de escritura de datos en excel
│   ├── script1.py
│   ├── script2.py
│   └── ...
|
├──	/extractors			# Procesadores de documentos pdf inviduales (gacetas, versiones estenográficas, actas, etc.)
|	├──	{document_type}Processor.py
|	└── ...
|
|
├── /lib                    # Funciones reusables dentro del proyecto
|
├── /outputs                # Carpeta local donde se guardan los Excel
|
├──	/prompts				# Carpeta que contiene los prompts de IA a utilizar (no se llaman desde aquí)
│
└── requirements.txt        # Dependencias (pandas, openpyxl, etc.)
```

<br>

# Contenido de los scripts

| Nombre del archivo |           Base de datos           |                                                                                                                                                                                                                                            Fuente                                                                                                                                                                                                                                            |                                                                  Descripción | Uso de IA                                              | Salida equivalente                                                                                             |
| :----------------- | :--------------------------------: | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------: | ----------------------------------------------------------------------------: | ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| script1.py         |       Perfiles legislativos       |                                                                                                                                                         [www.congresopuebla.gob.mx/index.php?option=com_content&amp;view=article&amp;id=11527](https://www.congresopuebla.gob.mx/index.php?option=com_content&view=article&id=11527)                                                                                                                                                         |                        Datos básicos de cada diputado del congreso de Puebla | NO                                                     | perfiles.json                                                                                                  |
| script2.py         |             Votaciones             |                                                                                                                                                                                                     https://www.congresopuebla.gob.mx/index.php?option=com_content&view=article&id=12718                                                                                                                                                                                                     |           Votaciones de los diputados en sesiones sobre acuerdos o propuestas | NO                                                     | votaciones.json                                                                                                |
| script3.py         |      Unidad y participación      |                                                                                                                                                                                         https://www.congresopuebla.gob.mx/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=345                                                                                                                                                                                         |              Desglose de resultados de votaciones en cada evento del congreso | SI                                                     | unidad_participacion.json                                                                                      |
| script4.py         |      Registro de asistencias      |                                                                                                                                                         [www.congresopuebla.gob.mx/index.php?option=com_content&amp;view=article&amp;id=12715](https://www.congresopuebla.gob.mx/index.php?option=com_content&view=article&id=12715)                                                                                                                                                         |                          Desglose de asistencias de cada diputado por sesión | NO                                                     | asistencias.json                                                                                               |
| script5.py         | Comisiones legislativas y comités | Comisiones:[www.congresopuebla.gob.mx/index.php?option=com_k2&amp;view=itemlist&amp;layout=category&amp;task=category&amp;id=369](https://www.congresopuebla.gob.mx/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=369)Comités: [www.congresopuebla.gob.mx/index.php?option=com_k2&amp;view=itemlist&amp;layout=category&amp;task=category&amp;id=409](https://www.congresopuebla.gob.mx/index.php?option=com_k2&view=itemlist&layout=category&task=category&id=409) | Información desglosada de comisiones y comités. Contiene 3 hojas separadas. | SI (solo una hoja)                                     | Hoja 1: comisiones_integrantes.json<br />Hoja: comisiones_sesiones.json<br />Hoja 3: comisiones_contenido.json |
| script6.py         |      Sesiones y cronometría      |                                                                                                                                                         [www.congresopuebla.gob.mx/index.php?option=com_content&amp;view=article&amp;id=12868](https://www.congresopuebla.gob.mx/index.php?option=com_content&view=article&id=12868)                                                                                                                                                         |                                       Cronometría desglosada de cada sesión | NO - Analísis de documentos con expresiones regulares | sesiones.json                                                                                                  |
| script7.py         |  Iniciativas y puntos de acuerdo  |                                                                                                                                                                                                                                             ...                                                                                                                                                                                                                                             |                                                                           ... |                                                        |                                                                                                                |
