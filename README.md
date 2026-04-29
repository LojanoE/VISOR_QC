# Visor de Fotos Georreferenciadas

Este es un visor de fotos georreferenciadas desarrollado en Python con la librería `customtkinter`. La aplicación permite a los usuarios visualizar fotos georreferenciadas en un mapa y filtrarlas por fecha y turno.

## Características

- Visualización de fotos georreferenciadas en un mapa.
- Filtrado de fotos por fecha, turno y metadatos avanzados.
- Edición de metadatos (Frente, Actividad, etc.) con guardado en DB y EXIF.
- Zoom y desplazamiento en el mapa centrado en el cursor.
- Exportación a Excel: listado detallado y tabla resumen de actividades.
- Caché de metadatos de fotos en PostgreSQL para un rendimiento óptimo.

## Requisitos

- Python 3.10 o superior
- Las dependencias listadas en el archivo `requirements.txt`

## Instalación

1. Clona este repositorio:

   ```bash
   git clone https://github.com/tu-usuario/visor-fotos-georreferenciadas.git
   ```

2. Crea un entorno virtual e instálalo:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # En Windows, usa `.venv\Scripts\activate`
   ```

3. Instala las dependencias:

   ```bash
   pip install -r requirements.txt
   ```

## Uso

1. Ejecuta la aplicación:

   ```bash
   python app.py
   ```

2. En la pestaña "Configuración", selecciona la imagen del mapa y el archivo de coordenadas.

3. En la pestaña "Mapa y Fotos", administra las carpetas de fotos y visualiza las fotos en el mapa.

4. Usa el botón "Excel" para exportar un listado de fotos filtradas y "Tabla" para una matriz de actividades.

5. Haz clic en una foto para ver sus detalles y usa el botón de editar metadatos para actualizarlos.

## Contribuciones

Las contribuciones son bienvenidas. Por favor, abre un "issue" para discutir los cambios propuestos.

## Licencia

Este proyecto está bajo la Licencia MIT. Consulta el archivo `LICENSE` para más detalles.
