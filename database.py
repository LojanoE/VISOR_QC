import psycopg2
from psycopg2 import sql, extras
import os
from datetime import datetime, timedelta
import sys
import time

# Database Configuration
DB_HOST = "192.168.60.82"
DB_NAME = "test2"
DB_PORT = "5432"
DB_USER = "alexism"
DB_PASS = "Data.GDR$2024"
DB_SCHEMA = "REGISTRO_FT_QC"

def get_connection():
    """Establishes a connection to the PostgreSQL database."""
    conn = psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        port=DB_PORT
    )
    return conn

def get_app_data_path(file_name):
    """Obtiene una ruta escribible en la carpeta de datos del usuario."""
    app_name = "Visor de Fotos Georreferenciadas"
    if sys.platform == "win32":
        base_dir = os.path.join(os.environ["APPDATA"], app_name)
    elif sys.platform == "darwin":
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)
    else: # linux
        base_dir = os.path.join(os.path.expanduser("~"), ".local", "share", app_name)
    
    if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    return os.path.join(base_dir, file_name)

def init_db():
    """Initializes the database, creates schema and table if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create schema
    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}")
    
    # Set search path
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")

    # Create table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS photos (
        path TEXT PRIMARY KEY,
        modified_time DOUBLE PRECISION NOT NULL,
        latitude DOUBLE PRECISION,
        longitude DOUBLE PRECISION,
        photo_date TIMESTAMP,
        utm_x DOUBLE PRECISION,
        utm_y DOUBLE PRECISION,
        work_front TEXT,
        coronation TEXT,
        activity_performed TEXT,
        observation_category TEXT
    )
    """)
    
    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_date ON photos(photo_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_coords ON photos(latitude, longitude)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_modified_time ON photos(modified_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_work_front ON photos(work_front)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_coronation ON photos(coronation)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_performed ON photos(activity_performed)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_observation_category ON photos(observation_category)")
    
    conn.commit()
    conn.close()

def upsert_photo(path, modified_time, metadata):
    """Inserts or updates photo metadata."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")

    if metadata:
        photo_date = metadata.get('date') 
        lat = metadata['coords'][0] if metadata.get('coords') else None
        lon = metadata['coords'][1] if metadata.get('coords') else None
        utm_x = metadata.get('utm_x')
        utm_y = metadata.get('utm_y')
        work_front = metadata.get('work_front')
        coronation = metadata.get('coronation')
        activity_performed = metadata.get('activity_performed')
        observation_category = metadata.get('observation_category')
    else:
        photo_date, lat, lon, utm_x, utm_y, work_front, coronation, activity_performed, observation_category = None, None, None, None, None, None, None, None, None

    cursor.execute("""
    INSERT INTO photos (path, modified_time, latitude, longitude, photo_date, utm_x, utm_y, work_front, coronation, activity_performed, observation_category)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT(path) DO UPDATE SET
        modified_time = excluded.modified_time,
        latitude = excluded.latitude,
        longitude = excluded.longitude,
        photo_date = excluded.photo_date,
        utm_x = excluded.utm_x,
        utm_y = excluded.utm_y,
        work_front = excluded.work_front,
        coronation = excluded.coronation,
        activity_performed = excluded.activity_performed,
        observation_category = excluded.observation_category
    """, (path, modified_time.timestamp(), lat, lon, photo_date, utm_x, utm_y, work_front, coronation, activity_performed, observation_category))

    conn.commit()
    conn.close()

def bulk_upsert_photos(photo_data_list):
    if not photo_data_list:
        return
        
    # Agrupar datos para inserción masiva
    data_to_insert = []
    for path, modified_time, metadata in photo_data_list:
        if metadata:
            photo_date = metadata.get('date')
            lat = metadata['coords'][0] if metadata.get('coords') else None
            lon = metadata['coords'][1] if metadata.get('coords') else None
            utm_x = metadata.get('utm_x')
            utm_y = metadata.get('utm_y')
            work_front = metadata.get('work_front')
            coronation = metadata.get('coronation')
            activity_performed = metadata.get('activity_performed')
            observation_category = metadata.get('observation_category')
        else:
            photo_date = lat = lon = utm_x = utm_y = work_front = coronation = activity_performed = observation_category = None
        
        data_to_insert.append((path, modified_time.timestamp(), lat, lon, photo_date, utm_x, utm_y, work_front, coronation, activity_performed, observation_category))

    # Definir la consulta con ON CONFLICT
    query = """
    INSERT INTO photos (path, modified_time, latitude, longitude, photo_date, utm_x, utm_y, work_front, coronation, activity_performed, observation_category)
    VALUES %s
    ON CONFLICT(path) DO UPDATE SET
        modified_time = EXCLUDED.modified_time,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        photo_date = EXCLUDED.photo_date,
        utm_x = EXCLUDED.utm_x,
        utm_y = EXCLUDED.utm_y,
        work_front = EXCLUDED.work_front,
        coronation = EXCLUDED.coronation,
        activity_performed = EXCLUDED.activity_performed,
        observation_category = EXCLUDED.observation_category
    """

    # Reintentar en caso de deadlock (TransactionRollbackError)
    max_retries = 3
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(f"SET search_path TO {DB_SCHEMA}")
            
            extras.execute_values(cursor, query, data_to_insert)
            
            conn.commit()
            return  # Éxito
        except psycopg2.extensions.TransactionRollbackError:
            if conn: conn.rollback()
            print(f"Deadlock detectado en bulk_upsert. Reintentando ({attempt + 1}/{max_retries})...")
            time.sleep(0.5 * (attempt + 1))
        except Exception as e:
            if conn: conn.rollback()
            print(f"Error en bulk_upsert_photos: {e}")
            raise e
        finally:
            if conn: conn.close()

def get_photo(path):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("SELECT * FROM photos WHERE path = %s", (path,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'path': row[0],
            'modified_time': datetime.fromtimestamp(row[1]),
            'coords': (row[2], row[3]) if row[2] is not None else None,
            'date': row[4],
            'utm_x': row[5],
            'utm_y': row[6],
            'work_front': row[7],
            'coronation': row[8],
            'activity_performed': row[9],
            'observation_category': row[10]
        }
    return None

def get_photos_for_indexing_with_filters(date_from=None, date_to=None, has_coords=True, shift_filter="Ambos"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    
    query = "SELECT path, photo_date, latitude, longitude, work_front, coronation, activity_performed, observation_category, utm_x, utm_y FROM photos"
    conditions = []
    params = []
    
    if has_coords:
        conditions.append("latitude IS NOT NULL AND longitude IS NOT NULL")
    
    if date_from and date_to:
        if shift_filter == "Nocturno":
            start_datetime = datetime.combine(date_from, datetime.min.time()).replace(hour=19)
            end_datetime = datetime.combine(date_to, datetime.min.time()).replace(hour=7) + timedelta(days=1)
            conditions.append("photo_date >= %s AND photo_date < %s")
            params.extend([start_datetime, end_datetime])
        else: # Diurno or Ambos
            start_datetime = datetime.combine(date_from, datetime.min.time()).replace(hour=7)
            end_datetime = datetime.combine(date_to, datetime.min.time()).replace(hour=7) + timedelta(days=1)
            conditions.append("photo_date >= %s AND photo_date < %s")
            params.extend([start_datetime, end_datetime])
    elif shift_filter == "Diurno":
        conditions.append("EXTRACT(HOUR FROM photo_date) >= 7 AND EXTRACT(HOUR FROM photo_date) < 19")
    elif shift_filter == "Nocturno":
        conditions.append("(EXTRACT(HOUR FROM photo_date) < 7 OR EXTRACT(HOUR FROM photo_date) >= 19)")
    
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    
    photos = []
    for row in rows:
        photos.append({
            'path': row[0],
            'date': row[1],
            'coords': (row[2], row[3]),
            'work_front': row[4],
            'coronation': row[5],
            'activity_performed': row[6],
            'observation_category': row[7],
            'utm_x': row[8],
            'utm_y': row[9]
        })
    return photos

def get_recent_photos(days=45):
    """Obtiene las fotos de los últimos N días de la base de datos."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    
    since_date = datetime.now() - timedelta(days=days)
    
    query = """
        SELECT path, photo_date, latitude, longitude, work_front, coronation, activity_performed, observation_category, utm_x, utm_y
        FROM photos 
        WHERE photo_date >= %s AND latitude IS NOT NULL AND longitude IS NOT NULL
    """
    
    cursor.execute(query, (since_date,))
    rows = cursor.fetchall()
    conn.close()
    
    photos = []
    for row in rows:
        photos.append({
            'path': row[0],
            'date': row[1],
            'coords': (row[2], row[3]),
            'work_front': row[4],
            'coronation': row[5],
            'activity_performed': row[6],
            'observation_category': row[7],
            'utm_x': row[8],
            'utm_y': row[9]
        })
    return photos

def get_all_photo_paths():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("SELECT path FROM photos")
    paths = [row[0] for row in cursor.fetchall()]
    conn.close()
    return paths

def get_all_photos_for_indexing():
    return get_photos_for_indexing_with_filters()

def remove_photos(paths_to_delete):
    if not paths_to_delete:
        return
    
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    
    batch_size = 500
    for i in range(0, len(paths_to_delete), batch_size):
        batch = paths_to_delete[i:i + batch_size]
        placeholders = ','.join('%s' for _ in batch)
        cursor.execute(f"DELETE FROM photos WHERE path IN ({placeholders})", tuple(batch))
    
    conn.commit()
    conn.close()

def get_photos_to_update(all_image_files):
    if not all_image_files:
        return []
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    
    paths = list(all_image_files.keys())
    batch_size = 500
    files_to_process = []
    
    for i in range(0, len(paths), batch_size):
        batch = paths[i:i + batch_size]
        placeholders = ','.join('%s' for _ in batch)
        cursor.execute(f"SELECT path, modified_time FROM photos WHERE path IN ({placeholders})", tuple(batch))
        db_files = {row[0]: row[1] for row in cursor.fetchall()}
        
        for path in batch:
            modified_time = all_image_files[path]
            if path not in db_files or db_files[path] < modified_time.timestamp():
                files_to_process.append(path)
    
    conn.close()
    return files_to_process

def db_exists():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT to_regclass('{DB_SCHEMA}.photos')")
        exists = cursor.fetchone()[0] is not None
        conn.close()
        return exists
    except Exception:
        return False

def get_photo_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("SELECT COUNT(*) FROM photos")
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_photos_with_coords_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("SELECT COUNT(*) FROM photos WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    count = cursor.fetchone()[0]
    conn.close()
    return count


# ──────────────── User Management ────────────────

def init_users_table():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()


def create_user(username, password):
    import bcrypt
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    try:
        cursor.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, password_hash))
        conn.commit()
        return True
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return False
    finally:
        conn.close()


def verify_user(username, password):
    import bcrypt
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), row[0].encode('utf-8'))
    except Exception:
        return False


def delete_user(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("DELETE FROM users WHERE username = %s", (username,))
    conn.commit()
    conn.close()


def list_users():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"SET search_path TO {DB_SCHEMA}")
    cursor.execute("SELECT username, created_at FROM users ORDER BY username")
    rows = cursor.fetchall()
    conn.close()
    return rows
