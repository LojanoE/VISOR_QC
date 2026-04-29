import os
import re
import json
from datetime import datetime
from PIL import Image, ExifTags
import piexif

def get_image_metadata(image_path):
    try:
        img = Image.open(image_path)
        exif_data = img._getexif()

        # Initialize default values for new fields
        work_front = None
        coronation = None
        activity_performed = None
        observation_category = None

        if not exif_data:
            # Usar fecha de modificación si no hay EXIF
            date = datetime.fromtimestamp(os.path.getmtime(image_path))
            return {
                'path': image_path, 
                'coords': None, 
                'date': date,
                'work_front': work_front,
                'coronation': coronation,
                'activity_performed': activity_performed,
                'observation_category': observation_category
            }

        exif = {ExifTags.TAGS.get(k, k): v for k, v in exif_data.items() if k in ExifTags.TAGS}
        
        # Extract additional fields from UserComment if present
        user_comment = exif.get("UserComment")
        if user_comment:
            # Attempt to parse as JSON if it looks like it might be JSON
            if isinstance(user_comment, bytes):
                try:
                    # Decode bytes to string, removing null bytes and extra text like 'ASCII'
                    decoded_comment = user_comment.decode('utf-8')
                    # Remove the "ASCII" prefix and null byte
                    if decoded_comment.startswith('ASCII'):
                        decoded_comment = decoded_comment[5:]  # Remove 'ASCII'
                    decoded_comment = decoded_comment.replace('\x00', '')  # Remove null bytes
                    # Check if it contains JSON
                    if decoded_comment.startswith('{') and decoded_comment.endswith('}'):
                        parsed_comment = json.loads(decoded_comment)
                        work_front = parsed_comment.get('workFront')
                        coronation = parsed_comment.get('coronation')
                        activity_performed = parsed_comment.get('activityPerformed')
                        observation_category = parsed_comment.get('observationCategory')
                except (json.JSONDecodeError, UnicodeDecodeError, IndexError):
                    pass
            elif isinstance(user_comment, str):
                # Handle if the comment is already a string
                try:
                    # Remove null bytes and other control characters
                    clean_comment = user_comment.replace('\x00', '')
                    # Remove everything before the first '{' and after the last '}'
                    start_brace = clean_comment.find('{')
                    end_brace = clean_comment.rfind('}') + 1
                    if start_brace != -1 and end_brace != 0:
                        clean_comment = clean_comment[start_brace:end_brace]
                    
                    if clean_comment.startswith('{') and clean_comment.endswith('}'):
                        parsed_comment = json.loads(clean_comment)
                        work_front = parsed_comment.get('workFront')
                        coronation = parsed_comment.get('coronation')
                        activity_performed = parsed_comment.get('activityPerformed')
                        observation_category = parsed_comment.get('observationCategory')
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
        
        # --- Obtener Coordenadas GPS ---
        gps_info = exif.get("GPSInfo")
        if not gps_info:
            return None # Ignorar si no tiene datos GPS

        def to_degrees(c):
            return c[0] + (c[1] / 60.0) + (c[2] / 3600.0)

        lat_val = to_degrees(gps_info[2])
        if gps_info[1] != 'N': lat_val = -lat_val
        lon_val = to_degrees(gps_info[4])
        if gps_info[3] != 'E': lon_val = -lon_val
        coords = (lat_val, lon_val)

        # --- Obtener Fecha ---
        date = None
        for tag_name in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
            date_str = exif.get(tag_name)
            if date_str:
                try:
                    date = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                    break
                except (ValueError, TypeError):
                    continue
        
        if not date:
            date = datetime.fromtimestamp(os.path.getmtime(image_path))

        return {
            'path': image_path, 
            'coords': coords, 
            'date': date,
            'work_front': work_front,
            'coronation': coronation,
            'activity_performed': activity_performed,
            'observation_category': observation_category
        }
    except Exception:
        return None

def update_image_metadata(image_path, new_data):
    """
    Actualiza el campo UserComment en los metadatos EXIF de una imagen.
    
    Args:
        image_path (str): La ruta al archivo de imagen.
        new_data (dict): Un diccionario con los nuevos metadatos a guardar.
                         Las claves deben ser 'workFront', 'coronation', etc.
    
    Returns:
        bool: True si la actualización fue exitosa, False en caso contrario.
    """
    try:
        # Cargar los datos EXIF existentes
        exif_dict = piexif.load(image_path)
        
        # Asegurarse de que la sección 'Exif' exista
        if 'Exif' not in exif_dict:
            exif_dict['Exif'] = {}

        # Preparar el comentario del usuario en formato JSON
        # Es importante usar las claves originales que espera el sistema de campo
        user_comment_data = {
            "workFront": new_data.get('work_front', ''),
            "coronation": new_data.get('coronation', ''),
            "activityPerformed": new_data.get('activity_performed', ''),
            "observationCategory": new_data.get('observation_category', '')
        }
        
        # El UserComment debe ser codificado correctamente
        user_comment_json = json.dumps(user_comment_data, ensure_ascii=False)
        
        # Manejar UserComment de forma más robusta
        try:
            # Intentar con la forma estándar
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = piexif.dump_usercomment(user_comment_json, encoding="unicode")
        except:
            # Si falla, intentar con codificación directa
            exif_dict["Exif"][piexif.ExifIFD.UserComment] = user_comment_json.encode("utf-8")

        # Convertir el diccionario de nuevo a bytes y guardarlo en la imagen
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, image_path)
        return True
    except Exception as e:
        print(f"Error al actualizar los metadatos EXIF de la imagen {image_path}: {e}")
        return False

def parse_coords_file(file_path):
    coords = {}
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            keys = ["minX", "maxX", "minY", "maxY"]
            for key in keys:
                match = re.search(rf"{key}:\s*([\d.]+)", content)
                if match: coords[key] = float(match.group(1))
            if len(coords) != 4: return None
        return coords
    except Exception: return None

def parse_document_name(filename):
    """Parses document name to extract date information.
    Expected format: YYMM-XXXX-XXXX-DDD-VX where YY=year, MM=month, DDD=day
    Example: 2510-DRT-ROD-001-V0 means year 25, month 10, day 001 (day 1)
    """
    name_part = filename.split('.')[0]
    parts = name_part.split('-')
    if len(parts) >= 5:
        try:
            yy_mm_part = parts[0]
            if len(yy_mm_part) != 4:
                return None
            
            day_part = parts[3]
            
            if len(day_part) != 3:
                return None
            
            if not day_part.isdigit():
                return None
            
            year_str = yy_mm_part[0:2]
            month_str = yy_mm_part[2:4]
            
            year = int(year_str)
            month = int(month_str)
            day = int(day_part)
            
            full_year = 2000 + year
            
            document_date = datetime(full_year, month, day)
            return document_date
        except (ValueError, IndexError):
            return None
    return None

def find_image_files(folder_path):
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg')):
                yield os.path.join(root, file)
