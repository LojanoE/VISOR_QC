import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, Canvas
import json
import os
import re
import sys
from datetime import datetime, timedelta
from PIL import Image, ImageTk, ExifTags
import pyproj
from functools import partial
from tkcalendar import DateEntry
import threading
import shutil
from collections import defaultdict
import database
from utils import get_image_metadata, parse_coords_file, parse_document_name, find_image_files, update_image_metadata

# --- Constantes ---
CONFIG_FILE = 'config.json'
APP_TITLE = "Visor de Fotos Georreferenciadas_V2"
THUMBNAIL_SIZE = (64, 64)

# --- Clases y Funciones ---

class App(ctk.CTk):
    def __init__(self):
        ctk.CTk.__init__(self)  # Inicializar con CustomTkinter
        self.title(APP_TITLE)

        # Forzar la ventana a pantalla completa obteniendo las dimensiones de la pantalla
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        self.geometry(f"{screen_width}x{screen_height}+0+0")

        # Configurar apariencia de CustomTkinter
        ctk.set_appearance_mode("light")  # "light" o "dark"
        ctk.set_default_color_theme("blue")  # Temas: "blue", "green", "dark-blue"

        self.config = self.load_config()
        self.map_photo_image = None
        self.photo_thumbnails = [] # Mantener referencia de los thumbnails
        self.photo_metadata = [] # Caché para los metadatos de las fotos
        self.photo_cache = {} # Caché de metadatos con fechas de modificación: {path: {'metadata': data, 'modified': timestamp}}
        self.cache_loaded = False  # Indica si la caché ha sido cargada

        # Usar CustomTkinter para la interfaz
        self.notebook = ctk.CTkTabview(self)
        self.notebook.pack(pady=10, padx=10, fill="both", expand=True)

        self.tab_config = self.notebook.add("Configuración")
        self.tab_map = self.notebook.add("Mapa y Fotos")

        # Variables para los filtros de multiselección (Estilo Excel)
        # Diccionario maestro: { 'field_name': { 'vars': {}, 'widgets': {}, 'scroll': None } }
        self.filter_controls = {
            'work_front': {'title': 'Frente de Trabajo', 'vars': {}, 'widgets': {}, 'scroll': None},
            'coronation': {'title': 'Coronamiento', 'vars': {}, 'widgets': {}, 'scroll': None},
            'activity_performed': {'title': 'Actividad Realizada', 'vars': {}, 'widgets': {}, 'scroll': None},
            'observation_category': {'title': 'Categoría de Observación', 'vars': {}, 'widgets': {}, 'scroll': None}
        }
        
        # Estado de selecciones manuales del usuario (set vacío = todo seleccionado)
        self.user_selections = {field: set() for field in self.filter_controls}
        self.only_with_text_state = {field: False for field in self.filter_controls}
        self.search_queries = {field: "" for field in self.filter_controls}  # Búsquedas por texto
        self.none_selected_state = {field: False for field in self.filter_controls}  # "Ninguno" seleccionado

        self.create_config_tab()
        self.create_map_tab()
        
        # Variables para el zoom
        self.zoom_level = 1.0
        self.original_map_image = None
        self.zoomed_images_cache = {}  # Caché de imágenes ya redimensionadas para diferentes niveles de zoom
        self.max_cache_size = 10  # Número máximo de imágenes en caché
        
        # Variables para controlar la visualización de marcadores
        self.markers_drawn = False  # Indica si los marcadores ya han sido dibujados
        self.visible_markers_cache = {}  # Caché de posiciones de marcadores para diferentes zooms
        self.last_zoom_level_for_markers = None  # Último nivel de zoom usado para calcular posiciones de marcadores
        self.marker_info = {}  # Diccionario para mantener {marker_id: metadata}
        self.marker_positions = {}  # Almacenar posiciones originales de los marcadores

        # Variables para navegación mejorada
        self.is_panning = False
        self.last_x = 0
        self.last_y = 0
        self.pan_sensitivity = 0.2  # Sensibilidad del desplazamiento (disminuida para mayor control)
        self.pan_dx_accumulator = 0.0
        self.pan_dy_accumulator = 0.0

        # Inicializar transformador de coordenadas global para mejorar rendimiento
        self.transformer = pyproj.Transformer.from_crs("epsg:4326", "epsg:24877", always_xy=True)

        # Initialize database
        try:
            database.init_db()
        except Exception as e:
            print(f"Error initializing database: {e}")
            messagebox.showerror("Error", f"Error initializing database: {e}")
        
        # Si ya hay configuración completa, cargar directamente el mapa
        if self.config.get("map_image_path") and self.config.get("coords_file_path") and self.config.get("photos_folder_path"):
            # Cargar el mapa primero para que la UI sea visible
            self.after(100, self.load_map_only)
            # Preguntar si desea actualizar la caché
            self.after(200, self.ask_update_cache)
            self.notebook.set("Mapa y Fotos")  # Cambiar a la pestaña del mapa

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def ask_update_cache(self):
        """Pregunta al usuario si desea actualizar la caché de fotos."""
        # Verificar si existe la base de datos
        if not database.db_exists():
            # No hay DB, inicializar desde cero
            self.initialize_cache()
            return
        
        # Preguntar al usuario
        response = messagebox.askyesno(
            "Actualizar Caché",
            "¿Desea actualizar la caché de fotos ahora?\n\n"
            "• Sí: Buscará nuevas fotos y actualizará metadatos (puede tomar varios minutos)\n"
            "• No: Cargará solo los datos guardados en la base de datos (más rápido)",
            icon='question'
        )
        
        if response:
            # Usuario quiere actualizar: cargar desde DB y luego actualizar caché
            self.fast_load_photo_metadata_async(update_cache=True)
        else:
            # Usuario no quiere actualizar: solo cargar desde DB
            self.load_photo_metadata_from_db_only()

    def load_config(self):
        config_path = database.get_app_data_path('config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                try: return json.load(f)
                except json.JSONDecodeError: return {}
        return {}

    def save_config(self):
        config_path = database.get_app_data_path('config.json')
        with open(config_path, 'w') as f: json.dump(self.config, f, indent=4)

    def save_cache(self):
        """Guarda la caché de metadatos en la base de datos."""
        try:
            # Initialize database if needed
            database.init_db()
            
            # Prepare data for bulk insert
            photo_data_list = []
            for path, cache_entry in self.photo_cache.items():
                if cache_entry["metadata"] is not None:
                    photo_data_list.append((path, cache_entry["modified"], cache_entry["metadata"]))
            
            # Bulk insert/update photos in database
            database.bulk_upsert_photos(photo_data_list)
        except Exception as e:
            print(f"Error al guardar la caché en la base de datos: {e}")

    def load_cache(self):
        """Carga la caché de metadatos desde la base de datos."""
        if database.db_exists():
            try:
                # Load all photos from database for the current photos folder
                photos_folder = self.config.get("photos_folder_path")
                if not photos_folder:
                    return False
                
                # Get all photos from database
                all_photos = database.get_all_photos_for_indexing()
                
                # Convert to the format expected by the application
                for photo_data in all_photos:
                    photo_path = photo_data['path']
                    # Only load photos from the current folder
                    if photo_data['coords'] and photo_path.startswith(photos_folder):
                        self.photo_cache[photo_path] = {
                            "metadata": {
                                'coords': photo_data['coords'],
                                'date': photo_data['date']
                            },
                            "modified": photo_data['date'] or datetime.now()
                        }
                
                return True
            except Exception as e:
                print(f"Error al cargar la caché desde la base de datos: {e}")
        return False

    def on_closing(self):
        self.save_config()
        if self.cache_loaded:
            self.save_cache()
        self.quit() # Forzar la detención del mainloop
        self.destroy()

    # --- Métodos para Filtros Estilo Excel (PRO) ---

    def create_checklist_filter(self, parent, title, filter_type):
        """Crea un componente de filtro con búsqueda y checkboxes multiselección."""
        container = ctk.CTkFrame(parent)
        container.pack(fill="x", padx=10, pady=5)
        
        ctk.CTkLabel(container, text=title, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=5, pady=(5, 2))
        
        # Buscador en vivo
        search_var = ctk.StringVar()
        self.filter_controls[filter_type]['search_var'] = search_var
        search_entry = ctk.CTkEntry(container, placeholder_text=f"Buscar {title.lower()}...", textvariable=search_var, height=25)
        search_entry.pack(fill="x", padx=5, pady=2)
        search_entry.bind("<KeyRelease>", lambda e, ft=filter_type: self.on_search_change(ft))
        
        # Botones de selección rápida
        btn_frame = ctk.CTkFrame(container, fg_color="transparent")
        btn_frame.pack(fill="x", padx=5)
        ctk.CTkButton(btn_frame, text="Todos", width=60, height=20, font=("Arial", 10), 
                      command=lambda: self.toggle_all(filter_type, True)).pack(side="left", padx=2, pady=2)
        ctk.CTkButton(btn_frame, text="Ninguno", width=60, height=20, font=("Arial", 10), 
                      command=lambda: self.toggle_all(filter_type, False), fg_color="#D35B58").pack(side="left", padx=2, pady=2)
        
        # Botón "Solo con Texto"
        self.filter_controls[filter_type]['only_text_btn'] = ctk.CTkButton(
            btn_frame, text="Solo con Texto", width=90, height=20, font=("Arial", 10),
            command=lambda: self.toggle_only_text(filter_type), fg_color="#5B9BD5")
        self.filter_controls[filter_type]['only_text_btn'].pack(side="left", padx=2, pady=2)
        
        # Frame scrollable para los checkboxes
        scroll_frame = ctk.CTkScrollableFrame(container, height=100)
        scroll_frame.pack(fill="x", padx=5, pady=5)
        self.filter_controls[filter_type]['scroll'] = scroll_frame
            
        return container

    def on_search_change(self, field):
        """Se llama cuando el usuario escribe en el buscador de un filtro."""
        control = self.filter_controls[field]
        self.search_queries[field] = control['search_var'].get().lower()
        # Aplicar filtros y actualizar listas
        self.apply_filters_and_update_lists()

    def toggle_only_text(self, field):
        """Alterna el estado de ocultación de valores vacíos."""
        self.only_with_text_state[field] = not self.only_with_text_state[field]
        if self.only_with_text_state[field]:
            self.filter_controls[field]['only_text_btn'].configure(fg_color="#2E5A88")
        else:
            self.filter_controls[field]['only_text_btn'].configure(fg_color="#5B9BD5")
        self.apply_filters_and_update_lists()

    def update_filter_lists(self):
        """Calcula disponibilidades cruzadas y actualiza la UI de los filtros.
        Filtrado dependiente tipo Excel: al seleccionar un valor, los demás filtros
        solo muestran las opciones disponibles para esa selección.
        Búsqueda tipo Excel: el texto filtra las opciones mostradas en ese filtro.
        Las búsquedas también son dependientes entre filtros."""
        if not self.photo_metadata:
            return

        base_params = self._get_filter_params()

        for field_to_update in self.filter_controls:
            control = self.filter_controls[field_to_update]
            search_query = control['search_var'].get().lower()

            # 1. Calcular conteos considerando:
            #    - Filtros de selección (checkboxes) de los DEMÁS campos
            #    - Búsquedas de texto de los DEMÁS campos
            counts = defaultdict(int)
            for m in self.photo_metadata:
                if self._check_photo_passes_other_filters_and_search(m, base_params, field_to_update):
                    val = m.get(field_to_update)
                    val_norm = val if (val and not (isinstance(val, str) and not val.strip())) else f'Sin {control["title"]}'
                    counts[val_norm] += 1

            # 2. Obtener valores únicos actuales
            all_vals = set()
            default_val = f'Sin {control["title"]}'
            for m in self.photo_metadata:
                v = m.get(field_to_update)
                all_vals.add(v if (v and not (isinstance(v, str) and not v.strip())) else default_val)

            # 3. Sincronizar widgets
            vars_dict = control['vars']
            widgets_dict = control['widgets']
            scroll = control['scroll']

            for val in sorted(list(all_vals)):
                if val not in widgets_dict:
                    # Determinar estado inicial basado en user_selections y none_selected_state
                    if self.none_selected_state[field_to_update]:
                        initial_state = False  # Ninguno seleccionado
                    elif not self.user_selections[field_to_update]:
                        initial_state = True  # Todos seleccionados
                    else:
                        initial_state = val in self.user_selections[field_to_update]
                    
                    var = tk.BooleanVar(value=initial_state)
                    cb = ctk.CTkCheckBox(scroll, text=val, variable=var,
                                         command=lambda f=field_to_update, v=val, vr=var: self._on_checkbox_click(f, v, vr),
                                         font=("Arial", 11), checkbox_width=18, checkbox_height=18)
                    vars_dict[val] = var
                    widgets_dict[val] = cb
                else:
                    # Checkbox ya existe, actualizar su estado
                    var = vars_dict[val]
                    if self.none_selected_state[field_to_update]:
                        var.set(False)  # Ninguno seleccionado
                    elif not self.user_selections[field_to_update]:
                        var.set(True)  # Todos seleccionados
                    else:
                        var.set(val in self.user_selections[field_to_update])

                count = counts.get(val, 0)
                cb = widgets_dict[val]
                cb.configure(text=f"{val} ({count})")

                # Visibilidad dinámica (como Excel)
                # Prioridad: Búsqueda > Count > Solo con Texto
                show = True
                
                # 1. Si hay búsqueda, solo mostrar los que coinciden con el texto
                if search_query and search_query not in val.lower():
                    show = False
                
                # 2. Si no hay búsqueda, ocultar los que tienen count=0 (no disponibles)
                elif count == 0:
                    show = False
                
                # 3. Solo con Texto
                if self.only_with_text_state[field_to_update] and val.startswith("Sin "):
                    show = False

                if show:
                    cb.pack(anchor="w", padx=5, pady=2)
                else:
                    cb.pack_forget()

    def _check_photo_passes_other_filters(self, metadata, params, exclude_field):
        """Verifica si la foto pasa filtros base y los otros campos de multiselección.
        Para filtrado dependiente tipo Excel: usa los valores SELECCIONADOS (marcados)."""
        if not self._check_base_filters(metadata, params):
            return False

        for field, selected_values in self.user_selections.items():
            # Si estamos actualizando este campo, lo excluimos del cálculo
            if field == exclude_field:
                continue

            # Si no hay valores seleccionados (set vacío), todos pasan (no filtrar)
            if not selected_values:
                continue

            val = metadata.get(field)
            val_norm = val if (val and not (isinstance(val, str) and not val.strip())) else f'Sin {self.filter_controls[field]["title"]}'

            # Si el valor NO está en los seleccionados, la foto no pasa el filtro
            if val_norm not in selected_values:
                return False

        return True

    def _check_photo_passes_other_filters_and_search(self, metadata, params, exclude_field):
        """Verifica si la foto pasa:
        - Filtros base (fecha, turno)
        - Filtros de selección (checkboxes) de los DEMÁS campos
        - Búsquedas de texto de los DEMÁS campos
        Para filtrado dependiente tipo Excel."""
        if not self._check_base_filters(metadata, params):
            return False

        # 1. Verificar filtros de selección (checkboxes) de otros campos
        for field, selected_values in self.user_selections.items():
            if field == exclude_field or not selected_values:
                continue

            val = metadata.get(field)
            val_norm = val if (val and not (isinstance(val, str) and not val.strip())) else f'Sin {self.filter_controls[field]["title"]}'

            if val_norm not in selected_values:
                return False

        # 2. Verificar búsquedas de texto de otros campos
        for field, search_query in self.search_queries.items():
            if field == exclude_field or not search_query:
                continue

            val = metadata.get(field)
            val_norm = val if (val and not (isinstance(val, str) and not val.strip())) else f'Sin {self.filter_controls[field]["title"]}'

            # Si el valor no contiene el texto de búsqueda, la foto no pasa
            if search_query not in val_norm.lower():
                return False

        return True

    def _on_checkbox_click(self, field, value, var):
        """Actualiza el set de selecciones del usuario cuando hace clic.
        user_selections guarda los valores SELECCIONADOS (marcados)."""
        # Si estaba en estado "Ninguno" y el usuario hace clic, limpiar ese estado
        if self.none_selected_state[field] and var.get():
            self.none_selected_state[field] = False
        
        self._sync_user_selections_from_vars()
        self.apply_filters_and_update_lists()

    def _sync_user_selections_from_vars(self):
        """Sincroniza los sets de selecciones basados en el estado de TODOS los checkboxes.
        user_selections guarda los valores SELECCIONADOS (marcados).
        Set vacío = todos seleccionados (sin filtro).
        none_selected_state = ninguno seleccionado (filtrar todo)."""
        for field, control in self.filter_controls.items():
            all_vars = control['vars']
            if not all_vars:
                continue

            # Si está en estado "Ninguno", no guardar ningún valor seleccionado
            if self.none_selected_state[field]:
                self.user_selections[field] = set()  # Set vacío especial = ninguno
                continue

            # Obtener valores marcados (seleccionados)
            checked_values = {val for val, var in all_vars.items() if var.get()}

            # Si todos están marcados, usamos set vacío (sin filtro)
            if len(checked_values) == len(all_vars):
                self.user_selections[field].clear()
            else:
                # Guardar solo los valores seleccionados (marcados)
                self.user_selections[field] = checked_values

    def toggle_all(self, field, state):
        """Marca o desmarca todos los elementos (no solo los visibles)."""
        control = self.filter_controls[field]
        for val, var in control['vars'].items():
            var.set(state)
        
        # Si state=False (Ninguno), marcar explícitamente el estado "none_selected"
        if not state:
            self.none_selected_state[field] = True
        else:
            self.none_selected_state[field] = False
            self.user_selections[field].clear()  # Todos = sin filtro

        self._sync_user_selections_from_vars()
        self.apply_filters_and_update_lists()

    def _check_base_filters(self, metadata, params):
        """Valida fecha y turno."""
        photo_date = metadata.get('date')
        if not photo_date: return False
        if params['use_date']:
            photo_date_only = photo_date.date()
            if not (params['date_from'] <= photo_date_only <= params['date_to']): return False
            hour = photo_date.hour
            if params['shift'] == "Diurno" and not (7 <= hour < 19): return False
            if params['shift'] == "Nocturno" and not (hour >= 19 or hour < 7): return False
        elif params['shift'] != "Ambos":
            hour = photo_date.hour
            if params['shift'] == "Diurno" and not (7 <= hour < 19): return False
            if params['shift'] == "Nocturno" and not (hour >= 19 or hour < 7): return False
        return True

    def apply_filters_and_update_lists(self):
        self.apply_filters()
        self.update_filter_lists()

    def create_config_tab(self):
        frame = self.tab_config
        ctk.CTkLabel(frame, text="Ruta de la Imagen del Mapa:").pack(anchor="w", padx=10, pady=(10,0))
        self.map_image_path = ctk.StringVar(value=self.config.get("map_image_path", "No seleccionada"))
        ctk.CTkEntry(frame, textvariable=self.map_image_path, state="disabled", width=400).pack(anchor="w", padx=10)
        ctk.CTkButton(frame, text="Seleccionar Imagen...", command=self.select_map_image).pack(anchor="w", padx=10, pady=5)

        ctk.CTkLabel(frame, text="Ruta del Archivo de Coordenadas:").pack(anchor="w", padx=10, pady=(10,0))
        self.coords_file_path = ctk.StringVar(value=self.config.get("coords_file_path", "No seleccionado"))
        ctk.CTkEntry(frame, textvariable=self.coords_file_path, state="disabled", width=400).pack(anchor="w", padx=10)
        ctk.CTkButton(frame, text="Seleccionar Archivo...", command=self.select_coords_file).pack(anchor="w", padx=10, pady=5)

        ctk.CTkLabel(frame, text="Ruta de la Carpeta de Documentos:").pack(anchor="w", padx=10, pady=(10,0))
        self.docs_folder_path = ctk.StringVar(value=self.config.get("docs_folder_path", "No seleccionada"))
        ctk.CTkEntry(frame, textvariable=self.docs_folder_path, state="disabled", width=400).pack(anchor="w", padx=10)
        ctk.CTkButton(frame, text="Seleccionar Carpeta...", command=self.select_docs_folder).pack(anchor="w", padx=10, pady=5)

    def select_map_image(self):
        path = filedialog.askopenfilename(title="Seleccione la imagen del mapa", filetypes=[("JPEG files", "*.jpg *.jpeg")])
        if path: 
            self.map_image_path.set(path)
            self.config["map_image_path"] = path
            # Cargar el mapa si también se ha seleccionado el archivo de coordenadas
            if self.config.get("coords_file_path"):
                self.load_map_only()

    def select_coords_file(self):
        path = filedialog.askopenfilename(title="Seleccione el archivo de coordenadas", filetypes=[("Text files", "*.txt")])
        if path: 
            self.coords_file_path.set(path)
            self.config["coords_file_path"] = path
            # Cargar el mapa si también se ha seleccionado la imagen del mapa
            if self.config.get("map_image_path"):
                self.load_map_only()

    def select_docs_folder(self):
        path = filedialog.askdirectory(title="Seleccione la carpeta de documentos")
        if path: 
            self.docs_folder_path.set(path)
            self.config["docs_folder_path"] = path

    def open_document_explorer(self):
        """Open a document explorer window showing documents matching photo dates"""
        docs_path = self.config.get("docs_folder_path")
        if not docs_path or not os.path.exists(docs_path):
            messagebox.showwarning("Advertencia", "Por favor, seleccione una carpeta de documentos en la pestaña de Configuración.")
            return

        # Create a new window for the document explorer
        self.doc_explorer_window = ctk.CTkToplevel(self)
        self.doc_explorer_window.title("Explorador de Documentos")
        self.doc_explorer_window.geometry("600x400")
        self.doc_explorer_window.grab_set()  # Traer al frente y hacer modal
        
        # Find available documents that match the dates of visible photos
        available_docs = self.get_documents_for_visible_photos(docs_path)
        
        # Create a frame for the document list
        doc_frame = ctk.CTkFrame(self.doc_explorer_window)
        doc_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Create a label for the document list
        ctk.CTkLabel(doc_frame, text="Documentos disponibles para las fechas seleccionadas:", 
                     font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        
        # Create a scrollable frame for the document list
        if available_docs:
            # Create the scrollable frame
            scrollable_frame = ctk.CTkScrollableFrame(doc_frame, label_text="Documentos")
            scrollable_frame.pack(fill="both", expand=True, padx=10, pady=10)
            
            # Add each document to the scrollable frame
            for doc_path in available_docs:
                doc_name = os.path.basename(doc_path)
                doc_button = ctk.CTkButton(scrollable_frame, text=doc_name, 
                                          command=lambda path=doc_path: self.open_document(path))
                doc_button.pack(fill="x", padx=5, pady=2)
        else:
            # Show message if no documents found
            ctk.CTkLabel(doc_frame, text="No se encontraron documentos para las fechas seleccionadas.",
                         text_color="red").pack(pady=20)

    def get_documents_for_visible_photos(self, docs_path):
        """Find documents that match the dates of visible photos based on the filename format"""
        # Get dates of currently visible photos (after filtering)
        visible_dates = set()
        for photo in self.photo_metadata:
            if self.is_image_in_date_range(photo['date']):
                # Add the date to the set (without time)
                visible_dates.add(photo['date'].date())
        
        # Find documents that match these dates
        matching_docs = []
        
        # Walk through all subdirectories in the document folder
        for root, dirs, files in os.walk(docs_path):
            for file in files:
                # Skip hidden files
                if file.startswith('.'):
                    continue
                
                # Check if the file has a valid extension (PDF, DOCX, etc.)
                if file.lower().endswith(('.pdf', '.doc', '.docx', '.txt', '.xls', '.xlsx', '.jpg', '.jpeg', '.png')):
                    doc_date = parse_document_name(file)
                    
                    if doc_date and doc_date.date() in visible_dates:
                        doc_path = os.path.join(root, file)
                        matching_docs.append(doc_path)
        
        return matching_docs

    def open_document(self, doc_path):
        """Open the selected document using the default application"""
        try:
            if os.name == 'nt':  # For Windows
                os.startfile(doc_path)
            elif os.name == 'posix':  # For macOS and Linux
                import subprocess
                subprocess.call(['open', doc_path] if sys.platform == 'darwin' else ['xdg-open', doc_path])
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir el documento: {e}")

    def create_map_tab(self):
        frame = self.tab_map

        # --- Layout principal de dos columnas ---
        frame.grid_columnconfigure(0, weight=0, minsize=350) # Columna de controles (ancho fijo)
        frame.grid_columnconfigure(1, weight=1)             # Columna del mapa (expandible)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=0) # Fila para la barra de progreso

        # --- Columna Izquierda: Controles y Filtros ---
        left_controls_frame = ctk.CTkFrame(frame, width=350)
        left_controls_frame.grid(row=0, column=0, sticky="nswe", padx=(10, 5), pady=10)
        left_controls_frame.grid_propagate(False) # Evita que el frame cambie de tamaño

        # --- Columna Derecha: Mapa y Navegación ---
        right_map_frame = ctk.CTkFrame(frame)
        right_map_frame.grid(row=0, column=1, sticky="nswe", padx=(5, 10), pady=10)
        right_map_frame.grid_rowconfigure(1, weight=1)
        right_map_frame.grid_columnconfigure(0, weight=1)

        # --- Contenido de la Columna Izquierda ---

        # 1. Marco: Gestión de Datos
        data_management_frame = ctk.CTkFrame(left_controls_frame)
        data_management_frame.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(data_management_frame, text="Gestión de Datos", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(0, 5))
        
        ctk.CTkButton(data_management_frame, text="Seleccionar Carpeta de Fotos...", command=self.select_photos_folder).pack(fill="x")
        self.photos_folder_path = ctk.StringVar(value=self.config.get("photos_folder_path", "No seleccionada"))
        ctk.CTkLabel(data_management_frame, textvariable=self.photos_folder_path, wraplength=300, justify="left").pack(fill="x", pady=(5,0))
        ctk.CTkButton(data_management_frame, text="Recargar / Sincronizar", command=self.reload_database).pack(fill="x", pady=(10, 5))

        # 2. Marco: Filtros
        filters_frame = ctk.CTkFrame(left_controls_frame)
        filters_frame.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(filters_frame, text="Filtros", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(0, 5))

        # Crear un frame scrollable interno para contener todos los filtros
        filters_scroll_container = ctk.CTkScrollableFrame(filters_frame, fg_color="transparent")
        filters_scroll_container.pack(fill="both", expand=True, padx=2, pady=2)

        # Control de filtro de fechas
        self.use_date_filter = ctk.BooleanVar()
        self.date_filter_checkbox = ctk.CTkCheckBox(filters_scroll_container, text="Filtrar por fecha", variable=self.use_date_filter, command=self.toggle_date_filter)
        self.date_filter_checkbox.pack(anchor="w", padx=5, pady=5)
        
        ctk.CTkLabel(filters_scroll_container, text="Desde:").pack(anchor="w", padx=(15, 0))
        self.date_from_entry = DateEntry(filters_scroll_container, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='y-mm-dd')
        self.date_from_entry.pack(anchor="w", padx=(15, 0), pady=(0,5))
        
        ctk.CTkLabel(filters_scroll_container, text="Hasta:").pack(anchor="w", padx=(15, 0))
        self.date_to_entry = DateEntry(filters_scroll_container, width=12, background='darkblue', foreground='white', borderwidth=2, date_pattern='y-mm-dd')
        self.date_to_entry.pack(anchor="w", padx=(15, 0), pady=(0,10))

        # Filtro de Turno (Diurno/Nocturno)
        ctk.CTkLabel(filters_scroll_container, text="Turno:").pack(anchor="w", padx=(15, 0))
        self.shift_filter_var = ctk.StringVar(value="Ambos")
        self.shift_filter_menu = ctk.CTkComboBox(filters_scroll_container, values=["Ambos", "Diurno", "Nocturno"], variable=self.shift_filter_var, command=lambda value: self.apply_filters())
        self.shift_filter_menu.pack(anchor="w", padx=(15, 0), pady=(0,10))

        # Filtros Estilo Excel (Multiselección) - Generar dinámicamente
        for field, config in self.filter_controls.items():
            self.create_checklist_filter(filters_scroll_container, config['title'], field)

        # Botones de acción de filtros (fijos al final del frame de filtros, no dentro del scroll)
        filter_buttons_frame = ctk.CTkFrame(filters_frame, fg_color="transparent")
        filter_buttons_frame.pack(fill="x", padx=10, pady=(5, 5))
        filter_buttons_frame.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(filter_buttons_frame, text="Aplicar Filtros", command=self.apply_filters).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ctk.CTkButton(filter_buttons_frame, text="Limpiar Filtros", command=self.clear_filters, fg_color="#D35B58").grid(row=0, column=1, sticky="ew", padx=(5, 0))

        # 3. Marco: Conteo de Fotos
        count_frame = ctk.CTkFrame(left_controls_frame)
        count_frame.pack(fill="x", padx=10, pady=(5, 10))
        self.photo_count_label = ctk.CTkLabel(count_frame, text="Fotos en el mapa: 0", font=ctk.CTkFont(weight="bold"))
        self.photo_count_label.pack()

        # 3. Marco: Acciones (movido al marco de zoom)

        # --- Contenido de la Columna Derecha ---

        # Marco de Navegación (Zoom)
        zoom_frame = ctk.CTkFrame(right_map_frame)
        zoom_frame.grid(row=0, column=0, sticky="ne", pady=(0, 5))
        
        # Botones de zoom
        ctk.CTkButton(zoom_frame, text="+", command=self.zoom_in, width=30).pack(side="left", padx=2)
        ctk.CTkButton(zoom_frame, text="-", command=self.zoom_out, width=30).pack(side="left", padx=2)
        ctk.CTkButton(zoom_frame, text="Zoom Original", command=self.zoom_reset).pack(side="left", padx=2)
        
        # Botones de acciones
        ctk.CTkButton(zoom_frame, text="Copiar Fotos", command=self.copy_filtered_photos, width=100).pack(side="left", padx=2)
        ctk.CTkButton(zoom_frame, text="ROD", command=self.open_document_explorer, width=50).pack(side="left", padx=2)
        ctk.CTkButton(zoom_frame, text="Excel", command=self.export_to_excel, width=80).pack(side="left", padx=2)
        ctk.CTkButton(zoom_frame, text="Tabla", command=self.export_to_table_excel, width=80).pack(side="left", padx=2)

        # Crear frame para contener el canvas y los scrollbars
        canvas_frame = ctk.CTkFrame(right_map_frame)
        canvas_frame.grid(row=1, column=0, sticky="nswe")
        
        # Crear canvas con scrollbars
        self.map_canvas = Canvas(canvas_frame, bg="gray", scrollregion=(0, 0, 1000, 1000))
        
        # Scrollbars
        v_scrollbar = ctk.CTkScrollbar(canvas_frame, orientation="vertical", command=self.map_canvas.yview)
        h_scrollbar = ctk.CTkScrollbar(canvas_frame, orientation="horizontal", command=self.map_canvas.xview)
        
        self.map_canvas.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        # Colocar elementos
        self.map_canvas.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        # Configurar el grid
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)
        
        # Vincular eventos para zoom y desplazamiento
        self.map_canvas.bind("<MouseWheel>", self.zoom_with_wheel)
        
        # Vincular eventos para desplazamiento con el mouse
        self.map_canvas.bind("<ButtonPress-2>", self.start_pan)  # Botón central del mouse
        self.map_canvas.bind("<ButtonRelease-2>", self.stop_pan)  # Soltar botón central
        self.map_canvas.bind("<B2-Motion>", self.pan)          # Movimiento con botón central presionado
        self.map_canvas.bind("<ButtonPress-1>", self.start_pan_left_click)  # Botón izquierdo
        self.map_canvas.bind("<ButtonRelease-1>", self.stop_pan)  # Soltar botón izquierdo
        self.map_canvas.bind("<B1-Motion>", self.pan)          # Movimiento con botón izquierdo presionado
        
        # Optimización: Vincular eventos a la etiqueta "point" una sola vez en lugar de por cada elemento
        self.map_canvas.tag_bind("point", '<Button-1>', self.on_marker_click)
        self.map_canvas.tag_bind("point", '<Enter>', lambda e: self.map_canvas.config(cursor="hand2"))
        self.map_canvas.tag_bind("point", '<Leave>', lambda e: self.map_canvas.config(cursor=""))
        
        # Variables para el desplazamiento
        self.pan_start_x = 0
        self.pan_start_y = 0
        self.canvas_start_x = 0
        self.canvas_start_y = 0
        
        self.map_canvas.create_text(400, 300, text="El mapa se mostrará aquí", fill="white")
        
        # Barra de progreso para operaciones largas
        self.progress_bar = ctk.CTkProgressBar(frame)
        self.progress_bar.set(0)
        # No se muestra hasta que se necesite

    def select_photos_folder(self):
        path = filedialog.askdirectory(title="Seleccione la carpeta con fotos")
        if path: 
            self.photos_folder_path.set(path)
            self.config["photos_folder_path"] = path
            # Initialize database to make sure it exists
            try:
                database.init_db()
            except Exception as e:
                print(f"Error initializing database: {e}")
                messagebox.showerror("Error", f"Error initializing database: {e}")
                return
            # Load or initialize the cache for the new folder
            try:
                self.cache_loaded = self.load_cache()
            except Exception as e:
                print(f"Error loading cache: {e}")
                self.cache_loaded = False
            if not self.cache_loaded:
                # If cache couldn't be loaded, initialize a new one and scan
                self.initialize_cache()
            else:
                # Check for new or modified files
                self.update_cache()
            self.load_map_and_photos()

    def clear_filters(self):
        """Restablece todos los filtros a sus valores predeterminados."""
        # Restablecer filtro de fecha
        self.use_date_filter.set(False)
        self.update_date_entry_ranges()
        self.toggle_date_filter()

        # Restablecer filtro de turno
        self.shift_filter_var.set("Ambos")

        # Restablecer TODOS los filtros de multiselección
        for field in self.filter_controls:
            # Limpiar búsquedas
            self.filter_controls[field]['search_var'].set("")
            self.search_queries[field] = ""
            # Limpiar estado "Ninguno"
            self.none_selected_state[field] = False
            # Marcar todos
            self.toggle_all(field, True)

        # Aplicar cambios
        self.apply_filters()

    def initialize_cache(self):
        """Inicializa la caché escaneando todos los archivos en la carpeta de fotos."""
        photos_path = self.config.get("photos_folder_path")
        if not photos_path:
            return

        # Mostrar barra de progreso
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))
        self.progress_bar.set(0)  # Iniciar en 0

        # Initialize database
        database.init_db()

        # Iniciar la inicialización de la caché en un hilo
        scan_thread = threading.Thread(target=self._initialize_cache_worker, args=(photos_path,), daemon=True)
        scan_thread.start()

    def _initialize_cache_worker(self, photos_path):
        """Trabaja en un hilo para inicializar la caché de metadatos."""
        try:
            image_files = list(find_image_files(photos_path))
            total_files = len(image_files)
            
            if total_files == 0:
                # No files to process, finish early
                self.cache_loaded = True
                self.after(0, self._finish_cache_initialization)
                return
            
            # Prepare batch data for database insertion
            batch_data = []
            
            for i, img_path in enumerate(image_files):
                # Verificar si el archivo ha sido modificado o es nuevo
                try:
                    file_modified = datetime.fromtimestamp(os.path.getmtime(img_path))
                except OSError:
                    continue  # Saltar archivos que no se puedan acceder

                # Extraer metadatos y actualizar la caché
                metadata = get_image_metadata(img_path)
                self.photo_cache[img_path] = {
                    "metadata": metadata,
                    "modified": file_modified
                }
                
                # Prepare data for batch insertion to database - only if metadata is not None
                if metadata is not None:
                    batch_data.append((img_path, file_modified, metadata))
                
                # Process in batches of 100 to avoid blocking database
                if len(batch_data) >= 100:
                    try:
                        database.bulk_upsert_photos(batch_data)
                    except Exception as e:
                        print(f"Error in bulk upsert during initialization: {e}")
                    batch_data = []  # Reset batch
                
                # Actualizar progreso (en el hilo principal)
                progress = int(((i + 1) / total_files) * 100)
                self.after(0, lambda p=progress: self.progress_bar.set(p/100))  # CTkProgressBar uses 0-1 scale

            # Insert any remaining data
            if batch_data:
                try:
                    database.bulk_upsert_photos(batch_data)
                except Exception as e:
                    print(f"Error in final bulk upsert during initialization: {e}")

            # Marcar que la caché ha sido cargada
            self.cache_loaded = True
            # Programar la finalización en el hilo principal
            self.after(0, self._finish_cache_initialization)
        except Exception as e:
            print(f"Error in _initialize_cache_worker: {e}")
            self.cache_loaded = False
            self.after(0, self._finish_cache_initialization)

    def update_cache(self, silent=False):
        """Actualiza la caché verificando archivos nuevos o modificados con prioridad inmediata."""
        photos_path = self.config.get("photos_folder_path")
        if not photos_path:
            return

        if not silent:
            # Mostrar barra de progreso
            self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))
            self.progress_bar.set(0)

        # Iniciar la actualización en un hilo que prioriza lo nuevo de los últimos 30 días
        scan_thread = threading.Thread(target=self._update_cache_worker, args=(photos_path, silent), daemon=True)
        scan_thread.start()

    def _remove_photos_from_cache(self, paths):
        """Elimina fotos de la caché en memoria."""
        for path in paths:
            self.photo_cache.pop(path, None)

    def _update_cache_worker(self, photos_path, silent=False):
        """Trabaja en un hilo para actualizar la caché de metadatos con prioridad en los últimos 30 días."""
        try:
            self._is_updating_cache = True
            image_files = set(find_image_files(photos_path))
            
            # 1. Detectar y eliminar fotos borradas
            db_photos = set(database.get_all_photo_paths())
            deleted_photos = list(db_photos - image_files)
            if deleted_photos:
                database.remove_photos(deleted_photos)
                self.after(0, lambda: self._remove_photos_from_cache(deleted_photos))

            # 2. Identificar qué archivos necesitan actualización real
            file_times = {}
            for img_path in image_files:
                try:
                    file_times[img_path] = datetime.fromtimestamp(os.path.getmtime(img_path))
                except OSError: continue
            
            db_files_to_update = set(database.get_photos_to_update(file_times))
            
            # --- FASE 0: CARGA RÁPIDA DE DB ---
            if not silent:
                self.after(0, self.fast_load_photo_metadata)
                self.after(0, self.refresh_display_from_cache)

            # --- FASE 1: PRIORIDAD 15 DÍAS (NUEVOS Y MODIFICADOS) ---
            limit_date = datetime.now() - timedelta(days=15)
            priority_files = []
            other_files = []
            
            for img_path in image_files:
                if file_times.get(img_path, datetime.min) > limit_date:
                    priority_files.append(img_path)
                else:
                    other_files.append(img_path)
            
            processed = 0
            total_files = len(image_files)
            batch_data = []

            # Procesar primero los prioritarios (recientes o nuevos)
            for img_path in priority_files:
                processed += 1
                if img_path in db_files_to_update:
                    metadata = get_image_metadata(img_path)
                    if metadata and metadata.get('coords'):
                        # CALCULAR UTM UNA SOLA VEZ AL ESCANEAR
                        ux, uy = self.convert_coords(metadata['coords'][0], metadata['coords'][1])
                        metadata['utm_x'], metadata['utm_y'] = ux, uy
                        
                        self.photo_cache[img_path] = {"metadata": metadata, "modified": file_times[img_path]}
                        batch_data.append((img_path, file_times[img_path], metadata))
                
                if len(batch_data) >= 50:
                    database.bulk_upsert_photos(batch_data)
                    batch_data = []
                
                if not silent and processed % 20 == 0:
                    self.after(0, lambda p=int((processed/total_files)*100): self.progress_bar.set(p/100))

            # Guardar remanente de prioridad y REFRESCAR MAPA YA
            if batch_data:
                database.bulk_upsert_photos(batch_data)
                batch_data = []
            
            if not silent:
                # Cargar lo que hay en DB de los últimos 30 días y mostrarlo de una vez
                self.after(0, self._fast_refresh_recent)

            # --- FASE 2: EL RESTO DEL HISTÓRICO ---
            for img_path in other_files:
                processed += 1
                if img_path in db_files_to_update:
                    metadata = get_image_metadata(img_path)
                    if metadata and metadata.get('coords'):
                        # CALCULAR UTM UNA SOLA VEZ AL ESCANEAR
                        ux, uy = self.convert_coords(metadata['coords'][0], metadata['coords'][1])
                        metadata['utm_x'], metadata['utm_y'] = ux, uy
                        
                        self.photo_cache[img_path] = {"metadata": metadata, "modified": file_times[img_path]}
                        batch_data.append((img_path, file_times[img_path], metadata))
                else:
                    # Si no necesita update, asegurar que esté en caché de memoria si es necesario
                    if img_path not in self.photo_cache:
                        db_p = database.get_photo(img_path)
                        if db_p and db_p.get('coords'):
                            self.photo_cache[img_path] = {"metadata": db_p, "modified": db_p['modified_time']}

                if len(batch_data) >= 100:
                    database.bulk_upsert_photos(batch_data)
                    batch_data = []
                
                if not silent and processed % 50 == 0:
                    self.after(0, lambda p=int((processed/total_files)*100): self.progress_bar.set(p/100))

            if batch_data:
                database.bulk_upsert_photos(batch_data)

            self.after(0, lambda s=silent: self._finish_cache_update(s))
        except Exception as e:
            print(f"Error en worker optimizado: {e}")
            self._is_updating_cache = False
            self.after(0, lambda s=silent: self._finish_cache_update(s))

    def _finish_cache_update(self, silent=False):
        """Finaliza la actualización de la caché."""
        self.progress_bar.grid_forget()
        # Actualizar photo_metadata con los archivos que tienen coordenadas y que existen físicamente
        photos_folder = self.config.get("photos_folder_path")
        if photos_folder:
            # Filtrar solo fotos que tienen coordenadas y están en la carpeta
            self.photo_metadata = [
                data["metadata"] for path, data in self.photo_cache.items()
                if data["metadata"] and data["metadata"].get("coords") and path.startswith(photos_folder)
            ]
        else:
            # Si no hay carpeta seleccionada, usar todas las que tienen coordenadas
            self.photo_metadata = [data["metadata"] for data in self.photo_cache.values() 
                                   if data["metadata"] and data["metadata"].get("coords")]
        
        # Actualizar los rangos de fecha en los widgets del filtro
        self.update_date_entry_ranges()
        # Actualizar las listas de multiselección
        self.update_filter_lists()
        # Redibujar los puntos en el mapa
        self.refresh_display_from_cache()
        self._is_updating_cache = False  # Reset flag
        if not silent:
            self._show_completion_message()

    def _show_completion_message(self):
        messagebox.showinfo("Proceso Completado", f"Se dibujaron {len([m for m in self.photo_metadata if self.is_image_in_date_range(m['date'])])} fotos en el mapa.")

    def _fast_refresh_recent(self):
        """Refresca rápidamente la UI con lo encontrado en la fase de prioridad."""
        # Cargar metadatos recientes (esto llenará self.photo_metadata y self.photo_cache)
        self.fast_load_photo_metadata()
        self.update_date_entry_ranges(initial_setup=True)
        self.refresh_display_from_cache()

    def refresh_display_from_cache(self):
        """Actualiza photo_metadata desde la caché y redibuja los puntos."""
        try:
            photos_folder = self.config.get("photos_folder_path")
            if not photos_folder:
                self.photo_metadata = []
            else:
                # Filtrar solo fotos que existen en la carpeta actual y que tienen coordenadas
                if self.photo_cache and hasattr(self.photo_cache, 'values'):
                    # Filter photos that are in the current folder.
                    existing_photos = [
                        data["metadata"] for path, data in self.photo_cache.items()
                        if data and data.get("metadata") and data["metadata"].get("coords")
                        and path.startswith(photos_folder)
                    ]
                    self.photo_metadata = existing_photos
                else:
                    self.photo_metadata = []
            
            # Redibujar directamente los puntos
            try:
                self.map_canvas.delete("point")
            except:
                pass  # Canvas might not be initialized yet
                
            # Limpiar caches de marcadores
            if hasattr(self, 'marker_info'):
                self.marker_info.clear()
            if hasattr(self, 'marker_positions'):
                self.marker_positions.clear()
                
            if self.photo_metadata:
                self._draw_points_chunk(0)
                self.markers_drawn = True
            else:
                self.markers_drawn = True
                # Only show message if we're not in startup phase
                if hasattr(self, 'cache_loaded') and self.cache_loaded:
                    messagebox.showinfo("Proceso Completado", "No se encontraron fotos con coordenadas en la carpeta actual.")
        except Exception as e:
            print(f"Error in refresh_display_from_cache: {e}")
            self.photo_metadata = []
            self.markers_drawn = True
        
        # Asegurar que los marcadores estén en la capa superior
        try:
            self.map_canvas.tag_raise("point")
        except:
            pass  # Canvas might not be initialized yet

    def fast_load_photo_metadata(self):
        """Loads photo metadata efficiently from database with coordinates only (last 15 days)."""
        try:
            # Load only photos from the last 15 days for much faster startup
            photos_recent = database.get_recent_photos(days=15)
            
            # If no recent photos, try to load at least some to not show an empty map
            if not photos_recent:
                photos_recent = database.get_photos_for_indexing_with_filters(has_coords=True)
                # Limit to a reasonable number if it's too many
                if len(photos_recent) > 1000:
                    photos_recent = photos_recent[:1000]
            
            # Convert to expected format
            self.photo_metadata = []
            self.photo_cache = {}
            
            # Filtrar solo fotos que existen físicamente
            photos_folder = self.config.get("photos_folder_path")
            
            for photo in photos_recent:
                if photo['coords']:
                    # Solo incluir la foto si existe físicamente en la carpeta
                    if not photos_folder or os.path.exists(photo['path']):
                        metadata = {
                            'path': photo['path'],
                            'coords': photo['coords'],
                            'date': photo['date'],
                            'work_front': photo.get('work_front'),
                            'coronation': photo.get('coronation'),
                            'activity_performed': photo.get('activity_performed'),
                            'observation_category': photo.get('observation_category')
                        }
                        self.photo_metadata.append(metadata)
                        
                        # Also add to cache
                        self.photo_cache[photo['path']] = {
                            "metadata": metadata,
                            "modified": photo['date'] or datetime.now()
                        }
            
            return len(self.photo_metadata) > 0
        except Exception as e:
            print(f"Error in fast_load_photo_metadata: {e}")
            return False

    def fast_load_photo_metadata_async(self, update_cache=False):
        """Inicia la carga de metadatos desde la DB en un hilo secundario."""
        if not database.db_exists():
            self.initialize_cache() # Si no hay DB, inicializar desde cero
            return

        # Mostrar barra de progreso
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))
        self.progress_bar.set(0)
        self.progress_bar.start() # Modo indeterminado

        # Iniciar la carga en un hilo
        load_thread = threading.Thread(target=self._fast_load_worker, args=(update_cache,), daemon=True)
        load_thread.start()

    def _fast_load_worker(self, update_cache=False):
        """Trabaja en un hilo para cargar metadatos desde la base de datos."""
        try:
            photos_with_coords = database.get_photos_for_indexing_with_filters(has_coords=True)

            # Optimización: Filtrar archivos existentes en el hilo para no bloquear la UI
            photos_folder = self.config.get("photos_folder_path")
            valid_photos = []
            for photo in photos_with_coords:
                if photo['coords']:
                    if not photos_folder or photo['path'].startswith(photos_folder):
                        valid_photos.append(photo)

            self.after(0, self._finish_fast_load, valid_photos, update_cache)
        except Exception as e:
            print(f"Error in _fast_load_worker: {e}")
            self.after(0, self._finish_fast_load, [], update_cache)

    def _finish_fast_load(self, photos_with_coords, update_cache=False):
        """Se ejecuta en el hilo principal para procesar los datos cargados."""
        self.progress_bar.stop()
        self.progress_bar.grid_forget()

        # Procesar los datos y actualizar la UI
        self.cache_loaded = self.process_loaded_photos(photos_with_coords)

        if self.cache_loaded:
            # Actualizar los rangos de fecha (Configuración inicial: últimos 15 días) antes de dibujar
            self.update_date_entry_ranges(initial_setup=True)
            # Actualizar las listas de multiselección
            self.update_filter_lists()
            # Redibujar los puntos en el mapa
            self.refresh_display_from_cache()
            # Iniciar la actualización de la caché SOLO si el usuario lo solicitó
            if update_cache:
                self.update_cache(silent=True)
        else:
            # Si no hay nada en la caché, inicializar desde cero
            self.initialize_cache()

    def load_photo_metadata_from_db_only(self):
        """Carga solo los metadatos desde la base de datos sin actualizar la caché."""
        if not database.db_exists():
            self.initialize_cache()
            return

        # Mostrar barra de progreso
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))
        self.progress_bar.set(0)
        self.progress_bar.start()

        # Cargar en hilo separado
        load_thread = threading.Thread(target=self._load_from_db_only_worker, daemon=True)
        load_thread.start()

    def _load_from_db_only_worker(self):
        """Trabaja en un hilo para cargar metadatos desde la DB sin actualizar."""
        try:
            photos_with_coords = database.get_photos_for_indexing_with_filters(has_coords=True)

            # Filtrar archivos existentes
            photos_folder = self.config.get("photos_folder_path")
            valid_photos = []
            for photo in photos_with_coords:
                if photo['coords']:
                    if not photos_folder or photo['path'].startswith(photos_folder):
                        valid_photos.append(photo)

            self.after(0, self._finish_load_from_db_only, valid_photos)
        except Exception as e:
            print(f"Error in _load_from_db_only_worker: {e}")
            self.after(0, self._finish_load_from_db_only, [])

    def _finish_load_from_db_only(self, photos_with_coords):
        """Finaliza la carga desde DB sin actualizar."""
        self.progress_bar.stop()
        self.progress_bar.grid_forget()

        # Procesar los datos
        self.cache_loaded = self.process_loaded_photos(photos_with_coords)

        if self.cache_loaded:
            # Actualizar UI
            self.update_date_entry_ranges(initial_setup=True)
            self.update_filter_lists()
            self.refresh_display_from_cache()
            
            # Mostrar mensaje informativo
            total_photos = len(self.photo_metadata)
            messagebox.showinfo(
                "Carga Completada",
                f"Se cargaron {total_photos} fotos desde la base de datos.\n\n"
                "Nota: La caché no se ha actualizado. Para buscar nuevas fotos,\n"
                "haga clic en 'Recargar / Sincronizar' en la pestaña Configuración."
            )
        else:
            messagebox.showwarning(
                "Sin Datos",
                "No se encontraron fotos en la base de datos.\n\n"
                "Haga clic en 'Recargar / Sincronizar' para inicializar la caché."
            )

    def _finish_cache_initialization(self):
        """Finaliza la inicialización de la caché."""
        self.progress_bar.grid_forget()
        # Actualizar photo_metadata con los archivos que tienen coordenadas y que existen físicamente
        photos_folder = self.config.get("photos_folder_path")
        if photos_folder:
            # Filtrar solo fotos que tienen coordenadas
            self.photo_metadata = [
                data["metadata"] for path, data in self.photo_cache.items()
                if data["metadata"] and data["metadata"].get("coords") and path.startswith(photos_folder)
            ]
        else:
            # Si no hay carpeta seleccionada, usar todas las que tienen coordenadas
            self.photo_metadata = [data["metadata"] for data in self.photo_cache.values() 
                                   if data["metadata"] and data["metadata"].get("coords")]
        
        # Actualizar los rangos de fecha en los widgets del filtro
        self.update_date_entry_ranges(initial_setup=True)
        # Actualizar las listas de multiselección
        self.update_filter_lists()
        # Redibujar los puntos en el mapa
        self.refresh_display_from_cache()
        
        # Marcar que los marcadores ya han sido dibujados para evitar redibujarlos innecesariamente
        self.markers_drawn = True
        self._show_completion_message()

    def process_loaded_photos(self, photos_with_coords):
        """Procesa la lista de fotos cargadas desde la DB y las pone en la caché en memoria."""
        self.photo_metadata = []
        self.photo_cache = {}
        
        for photo in photos_with_coords:
            # Ya vienen filtradas del hilo secundario, solo procesar
            metadata = {
                'path': photo['path'],
                'coords': photo['coords'],
                'date': photo['date'],
                'work_front': photo.get('work_front'),
                'coronation': photo.get('coronation'),
                'activity_performed': photo.get('activity_performed'),
                'observation_category': photo.get('observation_category')
            }
            self.photo_metadata.append(metadata)
            
            # También agregar a la caché en memoria
            self.photo_cache[photo['path']] = {
                "metadata": metadata,
                "modified": photo['date'] or datetime.now()
            }
        
        return len(self.photo_metadata) > 0

    def update_date_entry_ranges(self, initial_setup=False):
        """Establece las fechas mínima y máxima en los widgets DateEntry basándose en los metadatos de las fotos cargadas."""
        if not self.photo_metadata:
            return

        # Extraer todas las fechas válidas de los metadatos
        dates = [m['date'] for m in self.photo_metadata if m and m.get('date')]
        
        if not dates:
            return

        # Encontrar la fecha mínima y máxima
        min_date = min(dates).date()
        max_date = max(dates).date()

        if initial_setup:
            # Lógica de "Últimos 15 días" para el inicio para mejorar rendimiento
            self.use_date_filter.set(True)
            self.toggle_date_filter() # Actualizar estado visual de los inputs
            
            start_date = max_date - timedelta(days=15)
            # Si el rango de 30 días es anterior al min_date, usar min_date
            if start_date < min_date:
                start_date = min_date
                
            self.date_from_entry.set_date(start_date)
            self.date_to_entry.set_date(max_date)
        else:
            # Si NO es setup inicial, solo actualizamos los rangos si el filtro NO está activo
            # para no perder la selección del usuario durante una actualización en segundo plano.
            if not self.use_date_filter.get():
                self.date_from_entry.set_date(min_date)
                self.date_to_entry.set_date(max_date)

    def load_map_only(self):
        map_path = self.config.get("map_image_path")
        coords_path = self.config.get("coords_file_path")

        if not map_path or not coords_path:
            return

        try:
            self.map_canvas.delete("all")
            self.original_map_image = Image.open(map_path)
            
            # Resetear el nivel de zoom al cargar el mapa
            self.zoom_level = 1.0
            self.resized_map_image = self.original_map_image.resize(
                (self.original_map_image.width, self.original_map_image.height), 
                Image.Resampling.BILINEAR
            )
            self.map_photo_image = ImageTk.PhotoImage(self.resized_map_image)
            
            # Dibujar la imagen en la esquina superior izquierda
            self.map_canvas.create_image(0, 0, anchor="nw", image=self.map_photo_image, tags="map_image")
            
            # Actualizar la región de scroll
            self.map_canvas.configure(scrollregion=(0, 0, self.resized_map_image.width, self.resized_map_image.height))
            
            # Mensaje informativo
            self.map_canvas.create_text(
                self.resized_map_image.width // 2, 
                self.resized_map_image.height // 2, 
                text="Mapa cargado. Seleccione la carpeta de fotos para mostrar puntos.", 
                fill="white", 
                font=("Arial", 14),
                tags="info_text"
            )
            
            # Asegurar que los marcadores estén en la capa superior
            self.map_canvas.tag_raise("point")
        except Exception as e:
            messagebox.showerror("Error al Cargar Mapa", f"No se pudo cargar la imagen del mapa: {e}")

    def center_map_view(self):
        """Centra la vista del canvas en el medio del mapa."""
        self.map_canvas.update_idletasks() # Asegura que las dimensiones del widget estén actualizadas
        
        img_width = self.resized_map_image.width
        img_height = self.resized_map_image.height
        
        canvas_width = self.map_canvas.winfo_width()
        canvas_height = self.map_canvas.winfo_height()

        # Calcular la esquina superior izquierda del viewport para que el centro de la imagen
        # coincida con el centro del canvas.
        target_x = (img_width / 2) - (canvas_width / 2)
        target_y = (img_height / 2) - (canvas_height / 2)

        # Convertir a fracción para moveto
        scroll_region = self.map_canvas.cget("scrollregion")
        if scroll_region:
            _, _, scroll_width, scroll_height = map(int, scroll_region.split())
            if scroll_width > 0 and scroll_height > 0:
                frac_x = max(0, target_x / scroll_width)
                frac_y = max(0, target_y / scroll_height)
                
                self.map_canvas.xview_moveto(frac_x)
                self.map_canvas.yview_moveto(frac_y)

    def zoom_with_wheel(self, event):
        # Zoom con la rueda del mouse
        if event.delta > 0:
            self.zoom_in(center=(event.x, event.y))
        elif event.delta < 0:
            self.zoom_out(center=(event.x, event.y))

    def zoom_in(self, center=None):
        if self.original_map_image:
            self.zoom_level *= 1.2
            print(f"DEBUG: Zoom In - New zoom level: {self.zoom_level}")
            self.apply_zoom(center=center)

    def zoom_out(self, center=None):
        if self.original_map_image:
            self.zoom_level /= 1.2
            # Establecer un mínimo razonable para el zoom
            self.zoom_level = max(self.zoom_level, 0.1)
            self.apply_zoom(center=center)

    def zoom_reset(self):
        self.zoom_level = 1.0
        if self.original_map_image:
            self.apply_zoom()

            # Forzar el centrado calculando la posición de scroll correcta
            self.map_canvas.update_idletasks()
            
            img_width = self.resized_map_image.width
            img_height = self.resized_map_image.height
            
            canvas_width = self.map_canvas.winfo_width()
            canvas_height = self.map_canvas.winfo_height()

            # La esquina superior izquierda del viewport debe ser:
            # (centro_imagen_x - mitad_viewport_x, centro_imagen_y - mitad_viewport_y)
            target_x = (img_width / 2) - (canvas_width / 2)
            target_y = (img_height / 2) - (canvas_height / 2)

            # Convertir a fracción para moveto
            bbox = self.map_canvas.bbox("all")
            if bbox:
                scroll_width = bbox[2] - bbox[0]
                scroll_height = bbox[3] - bbox[1]
                
                if scroll_width > 0 and scroll_height > 0:
                    frac_x = target_x / scroll_width
                    frac_y = target_y / scroll_height
                    
                    self.map_canvas.xview_moveto(frac_x)
                    self.map_canvas.yview_moveto(frac_y)

    def apply_zoom(self, center=None):
        if not self.original_map_image:
            return

        try:
            # Calculate focus point relative to old image if center is provided
            focus_x = 0
            focus_y = 0
            if center and self.resized_map_image:
                # center is (x, y) in canvas widget coordinates
                # Convert to canvas absolute coordinates (accounting for current scroll)
                canvas_x = self.map_canvas.canvasx(center[0])
                canvas_y = self.map_canvas.canvasy(center[1])
                
                # Calculate ratio (0.0 to 1.0) of the focus point in the OLD image
                old_width = self.resized_map_image.width
                old_height = self.resized_map_image.height
                if old_width > 0 and old_height > 0:
                    focus_x = canvas_x / old_width
                    focus_y = canvas_y / old_height

            # Buscar en la caché si ya existe una imagen para este nivel de zoom (considerando pequeñas diferencias)
            cached_zoom_level = None
            for cached_level in self.zoomed_images_cache:
                if abs(cached_level - self.zoom_level) < 0.01:  # Tolerancia de zoom
                    cached_zoom_level = cached_level
                    break

            if cached_zoom_level is not None:
                # Usar imagen ya redimensionada de la caché
                self.resized_map_image = self.zoomed_images_cache[cached_zoom_level]
                self.map_photo_image = ImageTk.PhotoImage(self.resized_map_image)
            else:
                # Calcular nuevas dimensiones según el nivel de zoom
                new_width = max(1, int(self.original_map_image.width * self.zoom_level))
                new_height = max(1, int(self.original_map_image.height * self.zoom_level))

                # Redimensionar la imagen
                self.resized_map_image = self.original_map_image.resize((new_width, new_height), Image.Resampling.BILINEAR)
                
                # Agregar a la caché si no excede el tamaño máximo
                if len(self.zoomed_images_cache) < self.max_cache_size:
                    self.zoomed_images_cache[self.zoom_level] = self.resized_map_image
                else:
                    # Eliminar la entrada más antigua para mantener el tamaño límite
                    first_key = next(iter(self.zoomed_images_cache))
                    del self.zoomed_images_cache[first_key]
                    self.zoomed_images_cache[self.zoom_level] = self.resized_map_image
                
                self.map_photo_image = ImageTk.PhotoImage(self.resized_map_image)

            # --- New Logic for Viewport ---
            target_frac_x, target_frac_y = None, None
            if center and self.resized_map_image:
                new_width = self.resized_map_image.width
                new_height = self.resized_map_image.height
                
                # Calculate where the focus point should be in the NEW image (absolute coords)
                new_canvas_x = focus_x * new_width
                new_canvas_y = focus_y * new_height
                
                # We want new_canvas_x to appear at center[0] (widget coord)
                # Viewport top-left = new_canvas_x - center[0]
                target_left = new_canvas_x - center[0]
                target_top = new_canvas_y - center[1]
                
                # Convert to fractions for moveto
                if new_width > 0 and new_height > 0:
                    target_frac_x = target_left / new_width
                    target_frac_y = target_top / new_height
            else:
                # Guardar la posición actual del viewport antes de cambiar el tamaño
                xview = self.map_canvas.xview()
                yview = self.map_canvas.yview()
                target_frac_x = xview[0] if xview else None
                target_frac_y = yview[0] if yview else None
            
            # Actualizar la región de scroll del canvas
            self.map_canvas.configure(scrollregion=(0, 0, self.resized_map_image.width, self.resized_map_image.height))
            self.map_canvas.update_idletasks()
            
            # Mover la vista a la posición calculada
            if target_frac_x is not None and target_frac_y is not None:
                self.map_canvas.xview_moveto(target_frac_x)
                self.map_canvas.yview_moveto(target_frac_y)
            
            # Actualizar posiciones de los marcadores antes de borrar la imagen
            self.update_marker_positions()
            
            # Dibujar la imagen en la esquina superior izquierda (detrás de los marcadores)
            self.map_canvas.delete("map_image")
            self.map_canvas.create_image(0, 0, anchor="nw", image=self.map_photo_image, tags="map_image")
            
            # Asegurar que los marcadores estén en la capa superior
            self.map_canvas.tag_raise("point")

        except Exception as e:
            messagebox.showerror("Error de Zoom", f"No se pudo aplicar el zoom: {e}")

    def resize_image_to_fit_canvas(self, image, canvas):
        canvas.update_idletasks()
        canvas_width = canvas.winfo_width(); canvas_height = canvas.winfo_height()
        img_width, img_height = image.size
        if canvas_width <= 1 or canvas_height <= 1: canvas_width, canvas_height = 1024, 768
        ratio = min(canvas_width / img_width, canvas_height / img_height)
        return image.resize((int(img_width * ratio), int(img_height * ratio)), Image.Resampling.LANCZOS)

    def toggle_date_filter(self):
        # Activar o desactivar los controles de fecha según el checkbox
        state = "normal" if self.use_date_filter.get() else "disabled"
        self.date_from_entry.config(state=state)
        
        # Si se activa o desactiva el filtro, actualizar los puntos visibles
        if self.photo_metadata:
            self.apply_filters()

    def _get_filter_params(self):
        """Obtiene y agrupa los parámetros de filtrado actuales de todos los controles."""
        use_date = self.use_date_filter.get()
        try:
            date_from = self.date_from_entry.get_date() if use_date else None
            date_to = self.date_to_entry.get_date() if use_date else None
        except Exception:
            date_from, date_to = None, None
            use_date = False

        return {
            'use_date': use_date,
            'date_from': date_from,
            'date_to': date_to,
            'shift': self.shift_filter_var.get()
        }

    def _check_photo_filters(self, metadata, filter_params):
        """Devuelve True si la foto cumple todos los filtros dinámicos, False de lo contrario."""
        if not metadata:
            return False

        # Usar _check_base_filters para fecha y turno
        if not self._check_base_filters(metadata, filter_params):
            return False

        # 2. Filtros Dinámicos (Multiselección) usando user_selections
        for field, selected_values in self.user_selections.items():
            # Si está en estado "Ninguno", filtrar TODAS las fotos de ese campo
            if self.none_selected_state[field]:
                return False  # Ninguna foto pasa
            
            # Si no hay valores seleccionados (set vacío = Todos), todos pasan (no filtrar)
            if not selected_values:
                continue

            val = metadata.get(field)
            val_norm = val if (val and not (isinstance(val, str) and not val.strip())) else f'Sin {self.filter_controls[field]["title"]}'

            # Si el valor NO está en los seleccionados, la foto no pasa el filtro
            if val_norm not in selected_values:
                return False

        # 3. Filtros por búsqueda de texto (si hay búsqueda en algún campo)
        for field, search_query in self.search_queries.items():
            if not search_query:  # Si no hay búsqueda, saltar
                continue
            
            val = metadata.get(field)
            val_norm = val if (val and not (isinstance(val, str) and not val.strip())) else f'Sin {self.filter_controls[field]["title"]}'
            
            # Si el valor no contiene el texto de búsqueda, la foto no pasa
            if search_query not in val_norm.lower():
                return False

        return True

    def apply_filters(self):
        """Aplica filtros mostrando/ocultando marcadores existentes para mayor rendimiento."""
        if not self.marker_info:
            self.photo_count_label.configure(text="Fotos en el mapa: 0")
            return  # No hay marcadores para filtrar

        filter_params = self._get_filter_params()
        visible_count = 0

        # Iterar sobre todos los marcadores y mostrar/ocultar según el filtro
        for marker_id, metadata in self.marker_info.items():
            is_visible = self._check_photo_filters(metadata, filter_params)
            
            # Actualizar estado del marcador en el canvas
            new_state = 'normal' if is_visible else 'hidden'
            self.map_canvas.itemconfigure(marker_id, state=new_state)
            if is_visible:
                visible_count += 1

        # Actualizar el contador de fotos visibles
        self.photo_count_label.configure(text=f"Fotos en el mapa: {visible_count}")

    def copy_filtered_photos(self):
        """Copia las fotos actualmente visibles (filtradas) a una carpeta seleccionada por el usuario."""
        if not self.marker_info:
            messagebox.showwarning("Sin Fotos", "No hay fotos cargadas para copiar.", parent=self)
            return

        # 1. Obtener la lista de rutas de las fotos visibles
        visible_photo_paths = []
        
        filter_params = self._get_filter_params()

        for metadata in self.marker_info.values():
            if self._check_photo_filters(metadata, filter_params):
                visible_photo_paths.append(metadata['path'])

        if not visible_photo_paths:
            messagebox.showinfo("Sin Fotos", "No hay fotos que coincidan con los filtros actuales.", parent=self)
            return

        # 2. Pedir al usuario la carpeta de destino
        dest_folder = filedialog.askdirectory(title="Seleccione la carpeta de destino para copiar las fotos")
        if not dest_folder:
            return  # El usuario canceló

        # 3. Copiar los archivos
        for photo_path in visible_photo_paths:
            shutil.copy(photo_path, dest_folder)

        messagebox.showinfo("Copia Completada", f"Se han copiado {len(visible_photo_paths)} fotos a:\n{dest_folder}", parent=self)

    def is_image_in_date_range(self, image_date):
        # Verificar si una imagen está dentro del rango de fechas especificado
        if not self.use_date_filter.get():
            return True  # Si no hay filtro, incluir todas las imágenes
            
        try:
            # Obtener fechas de los controles DateEntry
            date_from = self.date_from_entry.get_date() if self.date_from_entry.get() else None
            date_to = self.date_to_entry.get_date() if self.date_to_entry.get() else None
            
            # Si no hay fechas especificadas, no aplicar filtro
            if not date_from and not date_to:
                return True
                
            # Verificar si la imagen está en el rango
            if date_from and image_date.date() < date_from:
                return False
            if date_to and image_date.date() > date_to:
                return False
                
            return True
        except Exception as e:
            messagebox.showerror("Error al procesar fecha", f"Error al procesar la fecha de la imagen: {e}")
            return False

    def convert_coords(self, lat, lon):
        """Convierte coordenadas WGS84 a UTM usando el transformador global."""
        return self.transformer.transform(lon, lat)

    def map_coords_to_pixel(self, utm_x, utm_y, bounds, canvas_size):
        x_range = bounds["maxX"] - bounds["minX"]
        y_range = bounds["maxY"] - bounds["minY"]
        if x_range == 0 or y_range == 0: return None, None
        x_pixel = ((utm_x - bounds["minX"]) / x_range) * canvas_size[0]
        y_pixel = ((bounds["maxY"] - utm_y) / y_range) * canvas_size[1]
        return x_pixel, y_pixel

    def download_image(self, source_path):
        """Abre un diálogo para guardar una copia de la imagen."""
        if not source_path or not os.path.exists(source_path):
            messagebox.showerror("Error", "La ruta de la imagen original no es válida.", parent=self)
            return

        # Obtener el nombre de archivo original para sugerirlo
        original_filename = os.path.basename(source_path)
        
        # Sugerir un tipo de archivo basado en la extensión original
        file_extension = os.path.splitext(original_filename)[1]
        file_types = [(f"{file_extension.upper()} files", f"*{file_extension}"), ("All files", "*.*")]

        # Abrir el diálogo para guardar archivo
        dest_path = filedialog.asksaveasfilename(
            initialfile=original_filename,
            defaultextension=file_extension,
            filetypes=file_types,
            title="Guardar imagen como...")
        
        if dest_path:
            try:
                shutil.copy(source_path, dest_path)
                messagebox.showinfo("Éxito", f"Imagen guardada en:\n{dest_path}", parent=self)
            except Exception as e:
                messagebox.showerror("Error al Guardar", f"No se pudo guardar la imagen: {e}", parent=self)

    def open_metadata_editor(self, metadata, parent_window):
        """Abre una ventana para editar los metadatos de una foto."""
        editor_window = ctk.CTkToplevel(self)
        editor_window.title("Editar Metadatos")
        editor_window.geometry("400x450")
        editor_window.grab_set()

        # Variables para los campos de entrada
        work_front_var = ctk.StringVar(value=metadata.get('work_front', ''))
        coronation_var = ctk.StringVar(value=metadata.get('coronation', ''))
        activity_var = ctk.StringVar(value=metadata.get('activity_performed', ''))
        category_var = ctk.StringVar(value=metadata.get('observation_category', ''))

        # Crear campos de formulario
        ctk.CTkLabel(editor_window, text="Frente de Trabajo:").pack(anchor="w", padx=20, pady=(10, 0))
        ctk.CTkEntry(editor_window, textvariable=work_front_var, width=360).pack(anchor="w", padx=20)

        ctk.CTkLabel(editor_window, text="Coronamiento:").pack(anchor="w", padx=20, pady=(10, 0))
        ctk.CTkEntry(editor_window, textvariable=coronation_var, width=360).pack(anchor="w", padx=20)

        ctk.CTkLabel(editor_window, text="Actividad Realizada:").pack(anchor="w", padx=20, pady=(10, 0))
        ctk.CTkEntry(editor_window, textvariable=activity_var, width=360).pack(anchor="w", padx=20)

        ctk.CTkLabel(editor_window, text="Categoría de Observación:").pack(anchor="w", padx=20, pady=(10, 0))
        ctk.CTkEntry(editor_window, textvariable=category_var, width=360).pack(anchor="w", padx=20)

        # Frame para botones
        button_frame = ctk.CTkFrame(editor_window, fg_color="transparent")
        button_frame.pack(pady=20, padx=20, fill="x")
        button_frame.grid_columnconfigure((0, 1), weight=1)

        # Botón de Guardar
        save_button = ctk.CTkButton(button_frame, text="Guardar Cambios", command=lambda: self.save_metadata_changes(
            metadata,
            {
                'work_front': work_front_var.get(),
                'coronation': coronation_var.get(),
                'activity_performed': activity_var.get(),
                'observation_category': category_var.get()
            },
            editor_window,
            parent_window
        ))
        save_button.grid(row=0, column=0, padx=(0, 5), sticky="ew")

        # Botón de Cancelar
        cancel_button = ctk.CTkButton(button_frame, text="Cancelar", command=editor_window.destroy, fg_color="#D35B58")
        cancel_button.grid(row=0, column=1, padx=(5, 0), sticky="ew")

    def save_metadata_changes(self, original_metadata, new_data, editor_window, image_window):
        """Guarda los metadatos actualizados en la DB y en la caché."""
        photo_path = original_metadata['path']

        # 1. Actualizar la base de datos
        try:
            # Los metadatos completos son necesarios para la función de upsert
            full_metadata_to_save = original_metadata.copy()
            full_metadata_to_save.update(new_data)
            
            modified_time = datetime.fromtimestamp(os.path.getmtime(photo_path))
            database.upsert_photo(photo_path, modified_time, full_metadata_to_save)
        except Exception as e:
            messagebox.showerror("Error de Base de Datos", f"No se pudieron guardar los cambios: {e}")
            return

        # 2. Actualizar el archivo de imagen físico (EXIF UserComment)
        update_success = update_image_metadata(photo_path, new_data)
        if not update_success:
            messagebox.showwarning("Advertencia EXIF", "No se pudieron guardar los metadatos en el archivo de imagen. Los cambios solo se verán en esta aplicación.")
            # No detenemos el proceso, ya que la DB sí se actualizó.

        # 2. Actualizar la caché en memoria (photo_cache y photo_metadata)
        if photo_path in self.photo_cache:
            self.photo_cache[photo_path]['metadata'].update(new_data)

        for i, meta in enumerate(self.photo_metadata):
            if meta['path'] == photo_path:
                self.photo_metadata[i].update(new_data)
                break
        
        # 3. Actualizar la caché de marcadores en memoria (self.marker_info)
        for marker_id, marker_meta in self.marker_info.items():
            if marker_meta['path'] == photo_path:
                self.marker_info[marker_id].update(new_data)
                break

        messagebox.showinfo("Éxito", "Los metadatos se han actualizado correctamente.")
        editor_window.destroy()
        
        # Actualizar el diccionario original para reflejar los cambios inmediatamente en la UI
        original_metadata.update(new_data)
        
        # Refrescar la visualización de metadatos en la ventana de imagen existente
        if hasattr(image_window, 'scrollable_metadata_frame'):
            # Limpiar widgets existentes
            for widget in image_window.scrollable_metadata_frame.winfo_children():
                widget.destroy()
            
            # Helper para añadir metadatos
            def add_meta(label, value):
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=label,
                           font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(10, 0))
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=f"  {value}").pack(anchor="w", padx=10)

            # Usar los metadatos actualizados
            metadata = original_metadata

            if metadata.get('coords'):
                coords_lat, coords_lon = metadata['coords']
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text="Coordenadas:",
                           font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(5, 0))
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=f"  Lat: {coords_lat:.6f}").pack(anchor="w", padx=10)
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=f"  Lng: {coords_lon:.6f}").pack(anchor="w", padx=10)

            if metadata.get('date'): add_meta("Fecha:", metadata['date'])
            if metadata.get('work_front'): add_meta("Frente de Trabajo:", metadata['work_front'])
            if metadata.get('coronation'): add_meta("Coronamiento:", metadata['coronation'])
            if metadata.get('activity_performed'): add_meta("Actividad Realizada:", metadata['activity_performed'])
            if metadata.get('observation_category'): add_meta("Categoría de Observación:", metadata['observation_category'])

    def _get_visible_photos_sorted(self):
        """
        Devuelve una lista de metadatos de las fotos actualmente visibles en el mapa,
        ordenadas por fecha.
        """
        visible_photos = []
        for marker_id, metadata in self.marker_info.items():
            if self.map_canvas.itemcget(marker_id, "state") == 'normal':
                visible_photos.append(metadata)
        
        # Ordenar por fecha
        visible_photos.sort(key=lambda m: m.get('date', datetime.min))
        return visible_photos

    def on_marker_click(self, event):
        # Convertir coordenadas del evento a coordenadas del canvas (considerando el scroll)
        canvas_x = self.map_canvas.canvasx(event.x)
        canvas_y = self.map_canvas.canvasy(event.y)

        # Identificar el marcador exacto que fue clickeado
        items = self.map_canvas.find_overlapping(canvas_x - 2, canvas_y - 2, canvas_x + 2, canvas_y + 2)
        
        clicked_item_id = None
        for item_id in items:
            if "point" in self.map_canvas.gettags(item_id):
                clicked_item_id = item_id
                break

        if clicked_item_id and clicked_item_id in self.marker_info:
            # Obtener la lista de fotos visibles y ordenadas
            visible_photos = self._get_visible_photos_sorted()
            
            # Encontrar el índice de la foto actual en la lista ordenada
            clicked_metadata = self.marker_info[clicked_item_id]
            try:
                current_index = visible_photos.index(clicked_metadata)
            except ValueError:
                # Si por alguna razón no se encuentra, simplemente mostrar la imagen sin navegación
                self.show_large_image([clicked_metadata], 0)
                return

            self.show_large_image(visible_photos, current_index)

    def _navigate_photo(self, photo_list, new_index, image_window):
        """Actualiza la ventana actual en lugar de cerrarla y abrir una nueva."""
        if 0 <= new_index < len(photo_list):
            # Actualizar la ventana existente con la nueva imagen
            self._update_large_image(photo_list, new_index, image_window)

    def _update_large_image(self, photo_list, current_index, image_window):
        """Actualiza la ventana existente con una nueva imagen, metadatos y añade soporte para ZOOM."""
        metadata = photo_list[current_index]
        image_path = metadata['path']
        
        # Actualizar el título de la ventana
        image_window.title(os.path.basename(image_path))
        
        try:
            # Abrir la nueva imagen original para el zoom
            original_img = Image.open(image_path)
            image_window.original_photo = original_img
            image_window.photo_zoom_level = 1.0  # Reset zoom
            
            img_width, img_height = original_img.size

            # Obtener dimensiones de la pantalla para el tamaño inicial
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()

            # Dimensiones máximas para el visor inicial (proporción de pantalla)
            max_view_width = int(screen_width * 0.6)
            max_view_height = int(screen_height * 0.7)

            # Calcular tamaño inicial de encaje (fit)
            ratio = min(max_view_width / img_width, max_view_height / img_height, 1.0)
            view_width = int(img_width * ratio)
            view_height = int(img_height * ratio)
            image_window.photo_fit_ratio = ratio

            # --- Inicialización de UI si no existe ---
            if not getattr(image_window, 'ui_initialized', False):
                # Crear frame principal
                main_frame = ctk.CTkFrame(image_window)
                main_frame.pack(fill="both", expand=True, padx=10, pady=10)
                main_frame.grid_columnconfigure(0, weight=1) # El frame de la imagen crece
                main_frame.grid_rowconfigure(0, weight=1)

                # Frame para la imagen con CANVAS para zoom
                image_frame = ctk.CTkFrame(main_frame)
                image_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

                # CANVAS para la foto
                image_window.photo_canvas = tk.Canvas(image_frame, bg="#1a1a1a", highlightthickness=0)
                image_window.photo_canvas.pack(fill="both", expand=True)

                # Eventos de Zoom y Pan en la foto
                image_window.photo_canvas.bind("<MouseWheel>", lambda e: self.zoom_photo_with_wheel(e, image_window))
                image_window.photo_canvas.bind("<ButtonPress-1>", lambda e: self.start_pan_photo(e, image_window))
                image_window.photo_canvas.bind("<B1-Motion>", lambda e: self.pan_photo(e, image_window))

                # Frame para los metadatos
                metadata_frame = ctk.CTkFrame(main_frame)
                metadata_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

                # Panel de metadatos
                ctk.CTkLabel(metadata_frame, text="Detalles de la Imagen",
                             font=ctk.CTkFont(weight="bold"),
                             anchor="center").pack(pady=(10, 5), padx=10)

                # Frame scrollable para metadatos
                image_window.scrollable_metadata_frame = ctk.CTkScrollableFrame(metadata_frame, width=250)
                image_window.scrollable_metadata_frame.pack(fill="both", expand=True, padx=5, pady=(0, 10))

                # Frame para botones
                button_frame = ctk.CTkFrame(image_window)
                button_frame.pack(side="bottom", fill="x", pady=(0, 10), padx=10)
                button_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

                # Botones
                image_window.prev_button = ctk.CTkButton(button_frame, text="< Anterior")
                image_window.prev_button.grid(row=0, column=0, padx=(0, 5), pady=5, sticky="ew")

                image_window.download_button = ctk.CTkButton(button_frame, text="Descargar Foto")
                image_window.download_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew")

                image_window.edit_button = ctk.CTkButton(button_frame, text="Editar Metadatos")
                image_window.edit_button.grid(row=0, column=2, padx=5, pady=5, sticky="ew")

                image_window.next_button = ctk.CTkButton(button_frame, text="Siguiente >")
                image_window.next_button.grid(row=0, column=3, padx=(5, 0), pady=5, sticky="ew")

                image_window.ui_initialized = True

            # Renderizar imagen inicial
            self._render_zoomed_photo(image_window)

            # Actualizar metadatos (limpiar y recrear)
            for widget in image_window.scrollable_metadata_frame.winfo_children():
                widget.destroy()
            
            # Helper para añadir metadatos
            def add_meta(label, value):
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=label,
                           font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(10, 0))
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=f"  {value}").pack(anchor="w", padx=10)

            if metadata.get('coords'):
                coords_lat, coords_lon = metadata['coords']
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text="Coordenadas:",
                           font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(5, 0))
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=f"  Lat: {coords_lat:.6f}").pack(anchor="w", padx=10)
                ctk.CTkLabel(image_window.scrollable_metadata_frame, text=f"  Lng: {coords_lon:.6f}").pack(anchor="w", padx=10)

            if metadata.get('date'): add_meta("Fecha:", metadata['date'])
            if metadata.get('work_front'): add_meta("Frente de Trabajo:", metadata['work_front'])
            if metadata.get('coronation'): add_meta("Coronamiento:", metadata['coronation'])
            if metadata.get('activity_performed'): add_meta("Actividad Realizada:", metadata['activity_performed'])
            if metadata.get('observation_category'): add_meta("Categoría de Observación:", metadata['observation_category'])

            # Actualizar comandos de botones
            image_window.prev_button.configure(
                command=lambda: self._navigate_photo(photo_list, current_index - 1, image_window),
                state="disabled" if current_index == 0 else "normal"
            )
            image_window.download_button.configure(command=lambda: self.download_image(image_path))
            image_window.edit_button.configure(command=lambda: self.open_metadata_editor(metadata, image_window))
            image_window.next_button.configure(
                command=lambda: self._navigate_photo(photo_list, current_index + 1, image_window),
                state="disabled" if current_index == len(photo_list) - 1 else "normal"
            )

            # Configurar tamaño de la ventana (solo si es nueva)
            if not getattr(image_window, 'geometry_set', False):
                metadata_panel_width = 300
                total_width = min(view_width + metadata_panel_width + 40, int(screen_width * 0.9))
                total_height = min(view_height + 150, screen_height * 0.8)
                image_window.geometry(f"{int(total_width)}x{int(total_height)}")
                image_window.geometry_set = True

        except Exception as e:
            print(f"Error al cargar imagen grande: {e}")

    def _render_zoomed_photo(self, window):
        """Renderiza la foto centrada en el canvas ajustándose al zoom sin parpadeos."""
        img = window.original_photo
        zoom = window.photo_zoom_level * window.photo_fit_ratio
        
        new_width = int(img.width * zoom)
        new_height = int(img.height * zoom)
        
        # Redimensionar la imagen
        resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        new_photo_tk = ImageTk.PhotoImage(resized_img)
        
        # Obtener dimensiones del canvas para centrado
        cw = window.photo_canvas.winfo_width()
        ch = window.photo_canvas.winfo_height()
        
        # Si el canvas aún no tiene tamaño real, usar el de la ventana
        if cw <= 1: 
            window.update_idletasks()
            cw = window.photo_canvas.winfo_width()
            ch = window.photo_canvas.winfo_height()

        x = max(0, (cw - new_width) // 2)
        y = max(0, (ch - new_height) // 2)
        
        # ACTUALIZACIÓN SIN PARPADEO:
        # 1. Si ya existe el objeto, actualizamos su imagen y posición
        if hasattr(window, 'photo_canvas_image_id') and window.photo_canvas.find_withtag(window.photo_canvas_image_id):
            window.photo_canvas.itemconfig(window.photo_canvas_image_id, image=new_photo_tk)
            window.photo_canvas.coords(window.photo_canvas_image_id, x, y)
        else:
            # 2. Si es la primera vez, creamos el objeto
            window.photo_canvas.delete("all")
            window.photo_canvas_image_id = window.photo_canvas.create_image(x, y, anchor="nw", image=new_photo_tk)
        
        # 3. MANTENER REFERENCIA: Crucial para evitar parpadeo por recolección de basura
        window.photo_canvas.image_reference = new_photo_tk 
        window.current_photo_tk = new_photo_tk
        
        # 4. Actualizar scrollregion
        window.photo_canvas.config(scrollregion=(0, 0, max(cw, new_width), max(ch, new_height)))

    def zoom_photo_with_wheel(self, event, window):
        """Maneja el zoom con la rueda del ratón en el visor de fotos."""
        if event.delta > 0:
            window.photo_zoom_level *= 1.1
        else:
            window.photo_zoom_level /= 1.1
            
        # Limitar zoom mínimo para que no desaparezca
        window.photo_zoom_level = max(0.1, window.photo_zoom_level)
        self._render_zoomed_photo(window)

    def start_pan_photo(self, event, window):
        window.photo_canvas.scan_mark(event.x, event.y)

    def pan_photo(self, event, window):
        window.photo_canvas.scan_dragto(event.x, event.y, gain=1)

    def show_large_image(self, photo_list, current_index):
        metadata = photo_list[current_index]
        image_path = metadata['path']
        top = ctk.CTkToplevel(self)
        top.title(os.path.basename(image_path))
        top.grab_set()  # Hace que la ventana sea modal y la trae al frente
        
        self._update_large_image(photo_list, current_index, top)

    def load_map_and_photos(self):
        map_path = self.config.get("map_image_path")
        coords_path = self.config.get("coords_file_path")
        photos_path = self.config.get("photos_folder_path")

        if not all([map_path, coords_path, photos_path]):
            messagebox.showwarning("Faltan Datos", "Por favor, configure todo en la pestaña 'Configuración'."); return

        try:
            self.map_canvas.delete("all")
            self.original_map_image = Image.open(map_path)
            
            # Resetear el nivel de zoom al cargar el mapa
            self.zoom_level = 1.0
            self.resized_map_image = self.original_map_image.resize(
                (self.original_map_image.width, self.original_map_image.height), 
                Image.Resampling.BILINEAR
            )
            self.map_photo_image = ImageTk.PhotoImage(self.resized_map_image)
            self.map_canvas.create_image(0, 0, anchor="nw", image=self.map_photo_image, tags="map_image")
            
            # Actualizar la región de scroll del canvas
            self.map_canvas.configure(scrollregion=(0, 0, self.resized_map_image.width, self.resized_map_image.height))
            
            # Asegurar que los marcadores estén en la capa superior
            self.map_canvas.tag_raise("point")

            # Centrar la vista del mapa después de un breve retraso para asegurar que el canvas tenga tamaño
            self.after(100, self.center_map_view)
            
        except Exception as e:
            messagebox.showerror("Error al Cargar Mapa", f"No se pudo cargar la imagen del mapa: {e}"); return
        
        # La lógica de actualización y dibujado de puntos ya se maneja en los métodos
        # initialize_cache() y update_cache(), que son llamados desde select_photos_folder.
        # No es necesario hacer nada más aquí para evitar la duplicación.

        # Centrar la vista del mapa después de un breve retraso
        self.after(100, self.center_map_view)


    def _draw_points_chunk(self, index=0):
        # Usar directamente self.photo_metadata para evitar comprobaciones de disco lentas
        filtered_photo_metadata = self.photo_metadata
        
        chunk_size = 500  # Aumentado para mayor velocidad
        total_photos = len(filtered_photo_metadata)
        
        # Obtener datos necesarios para el dibujado
        coords_path = self.config.get("coords_file_path")
        map_bounds = parse_coords_file(coords_path)

        if not all([map_bounds, self.original_map_image]):
            self.progress_bar.grid_forget()
            return

        # Usar dimensiones del mapa original para la conversión de coordenadas
        original_width = self.original_map_image.width
        original_height = self.original_map_image.height
        canvas_size = (original_width, original_height)
        
        photos_to_draw = filtered_photo_metadata[index : index + chunk_size]

        for metadata in photos_to_draw:
            # Aplicar filtro de fecha
            if not self.is_image_in_date_range(metadata['date']):
                continue

            # USAR COORDENADAS UTM PRECALCULADAS SI EXISTEN
            if metadata.get('utm_x') and metadata.get('utm_y'):
                utm_x, utm_y = metadata['utm_x'], metadata['utm_y']
            else:
                # Fallback solo si no están en DB aún (ej. fotos nuevas antes de guardar)
                coords_wgs = metadata['coords']
                utm_x, utm_y = self.convert_coords(coords_wgs[0], coords_wgs[1])
            
            px, py = self.map_coords_to_pixel(utm_x, utm_y, map_bounds, canvas_size)

            if px is not None:
                original_pos = (px, py)
                try:
                    adjusted_x = px * self.zoom_level
                    adjusted_y = py * self.zoom_level
                    
                    point = self.map_canvas.create_oval(
                        adjusted_x-4, adjusted_y-4, 
                        adjusted_x+4, adjusted_y+4, 
                        fill="#FF6347", outline="black", width=1, tags="point"
                    )
                    self.marker_info[point] = metadata
                    self.marker_positions[point] = original_pos
                except: continue

        new_index = index + chunk_size
        if new_index < total_photos:
            self.after(1, self._draw_points_chunk, new_index)
        else:
            self.progress_bar.grid_forget()
            self.markers_drawn = True
            # Elevar los marcadores al final para asegurar visibilidad
            try:
                self.map_canvas.tag_raise("point")
            except:
                pass

    def update_visible_markers(self):
        """Actualizar los marcadores visibles según el filtro de fecha."""
        # Obtener datos necesarios para la conversión de coordenadas
        coords_path = self.config.get("coords_file_path")
        map_bounds = parse_coords_file(coords_path)
        
        if not map_bounds or not self.original_map_image:
            return

        # Usar dimensiones del mapa original para la conversión de coordenadas
        original_width = self.original_map_image.width
        original_height = self.original_map_image.height
        canvas_size = (original_width, original_height)
        
        # Guardar la posición actual del viewport para mantenerla después de actualizar
        xview = self.map_canvas.xview()
        yview = self.map_canvas.yview()
        
        # Primero ocultar todos los marcadores existentes
        for item_id in self.marker_info.keys():
            self.map_canvas.delete(item_id)
        
        # Limpiar diccionarios de marcadores
        self.marker_info.clear()
        self.marker_positions.clear()
        
        # Dibujar solo los marcadores que cumplen con el filtro de fecha Y que existen físicamente
        photos_folder = self.config.get("photos_folder_path")
        for metadata in self.photo_metadata:
            if not self.is_image_in_date_range(metadata['date']):
                continue

            coords_wgs = metadata['coords']
            utm_x, utm_y = self.convert_coords(coords_wgs[0], coords_wgs[1])
            
            # Calcular posición en la escala original del mapa
            orig_px, orig_py = self.map_coords_to_pixel(utm_x, utm_y, map_bounds, canvas_size)
            
            if orig_px is not None:
                try:
                    # Calcular posición ajustada según el zoom actual
                    adjusted_x = orig_px * self.zoom_level
                    adjusted_y = orig_py * self.zoom_level
                    
                    point = self.map_canvas.create_oval(
                        adjusted_x-4, adjusted_y-4, 
                        adjusted_x+4, adjusted_y+4, 
                        fill="#FF6347", outline="black", width=1, tags="point"
                    )
                    
                    # Guardar metadatos completos y posición original
                    self.marker_info[point] = metadata
                    self.marker_positions[point] = (orig_px, orig_py)  # Posición original en el mapa sin zoom
                except Exception:
                    continue
        
        # Asegurar que los marcadores estén en la capa superior
        self.map_canvas.tag_raise("point")
        
        # Restaurar la posición del viewport para mantener la vista actual
        if xview and yview:
            self.map_canvas.xview_moveto(xview[0])
            self.map_canvas.yview_moveto(yview[0])

    def update_marker_positions(self):
        """Actualizar las posiciones de los marcadores según el nivel actual de zoom."""
        # Actualizar posiciones de todos los marcadores existentes
        for item_id, (orig_x, orig_y) in self.marker_positions.items():
            try:
                # Calcular nueva posición según el zoom actual
                new_x = orig_x * self.zoom_level
                new_y = orig_y * self.zoom_level
                
                # Actualizar la posición del óvalo en el canvas
                coords = (new_x-4, new_y-4, new_x+4, new_y+4)
                self.map_canvas.coords(item_id, *coords)
            except:
                # Si el item no existe más, continuar
                continue

    def update_photo_markers(self):
        """Dibuja los marcadores de fotos solo si no están ya dibujados o si es necesario actualizarlos."""
        # Solo dibujar marcadores si no están ya dibujados o si es necesario
        current_points = self.map_canvas.find_withtag("point")
        if not current_points:  # Solo dibujar si no hay puntos actualmente
            if self.photo_metadata:
                self._draw_points_chunk(0)
        # Asegurar que los marcadores estén en la capa superior
        self.map_canvas.tag_raise("point")

    def redraw_photo_markers(self):
        """Limpia y redibuja todos los marcadores de fotos en el mapa."""
        if not self.photo_metadata:
            return

        # Usar update_visible_markers para aplicar el filtro de fecha
        self.update_visible_markers()
        
    def refresh_cache_and_display(self):
        """Actualiza la caché y redibuja los puntos en el mapa."""
        if self.config.get("photos_folder_path") and self.cache_loaded:
            self.update_cache()
            # Después de actualizar la caché, se redibujarán los puntos automáticamente

    def start_pan(self, event):
        # Iniciar el desplazamiento con el botón central del mouse
        self.is_panning = True
        self.last_x = event.x
        self.last_y = event.y
        self.map_canvas.config(cursor="fleur")

    def start_pan_left_click(self, event):
        # Iniciar el desplazamiento con el botón izquierdo del mouse (si no hay puntos debajo)
        # Solo iniciar si no se hace clic sobre un punto de imagen
        item = self.map_canvas.find_closest(event.x, event.y)
        tags = self.map_canvas.gettags(item[0]) if item else []
        
        # Si no es un item de punto de imagen, permitir desplazamiento
        if not any("point" in str(tag) for tag in tags):
            self.is_panning = True
            self.last_x = event.x
            self.last_y = event.y
            self.map_canvas.config(cursor="hand1")

    def pan(self, event):
        if not self.is_panning:
            return
            
        # Calcular el desplazamiento desde la última posición
        dx = event.x - self.last_x
        dy = event.y - self.last_y
        
        # Invertir la dirección para que el movimiento sea intuitivo
        dx = -dx
        dy = -dy
        
        # Acumular el movimiento para un desplazamiento suave
        self.pan_dx_accumulator += dx * self.pan_sensitivity
        self.pan_dy_accumulator += dy * self.pan_sensitivity
        
        # Obtener la parte entera para el scroll y guardar el resto
        scroll_dx = int(self.pan_dx_accumulator)
        scroll_dy = int(self.pan_dy_accumulator)
        
        if scroll_dx != 0:
            self.map_canvas.xview_scroll(scroll_dx, "units")
            self.pan_dx_accumulator -= scroll_dx
            
        if scroll_dy != 0:
            self.map_canvas.yview_scroll(scroll_dy, "units")
            self.pan_dy_accumulator -= scroll_dy
        
        # Actualizar la posición para el próximo movimiento
        self.last_x = event.x
        self.last_y = event.y

    def stop_pan(self, event):
        self.is_panning = False
        self.map_canvas.config(cursor="")

    def reload_database(self):
        self.update_cache()

    def get_filtered_photos(self):
        """Obtiene las fotos que cumplen con los filtros actuales."""
        if not self.marker_info:
            return []

        filter_params = self._get_filter_params()
        filtered_photos = []

        for metadata in self.marker_info.values():
            if self._check_photo_filters(metadata, filter_params):
                filtered_photos.append(metadata)

        return filtered_photos

    def export_to_excel(self):
        """Exporta las fotos filtradas a un archivo Excel con filtros aplicados y estadísticas."""
        # Obtener las fotos filtradas
        filtered_photos = self.get_filtered_photos()
        
        if not filtered_photos:
            messagebox.showinfo("Exportar a Excel", "No hay fotos que coincidan con los filtros actuales para exportar.", parent=self)
            return

        # Abrir diálogo para guardar archivo
        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Archivos Excel", "*.xlsx"), ("Todos los archivos", "*.*")],
            title="Guardar reporte como..."
        )
        
        if not file_path:
            return  # El usuario canceló

        # Capturar información de filtros para el encabezado del Excel
        filter_info = self._get_filter_params()

        # Mostrar barra de progreso
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))
        self.progress_bar.set(0)
        
        # Iniciar worker en un hilo separado
        threading.Thread(target=self._export_to_excel_worker, args=(file_path, filtered_photos, filter_info), daemon=True).start()

    def _export_to_excel_worker(self, file_path, filtered_photos, filter_info):
        try:
            from openpyxl import Workbook
            from openpyxl.drawing.image import Image as XLImage
            from openpyxl.styles import Font, Alignment, PatternFill
            from openpyxl.utils import get_column_letter
            from io import BytesIO
            import os
            
            # Crear el libro de trabajo
            wb = Workbook()
            ws = wb.active
            ws.title = "Reporte de Fotos"
            
            # Estilos
            title_font = Font(size=14, bold=True)
            header_font = Font(bold=True)
            header_fill = PatternFill(start_color="E6E6FA", end_color="E6E6FA", fill_type="solid")
            center_alignment = Alignment(horizontal="center", vertical="center")

            # Escribir filtros aplicados
            row = 1
            ws.cell(row=row, column=1, value="FILTROS APLICADOS").font = title_font
            row += 1
            
            if filter_info.get('use_date'):
                ws.cell(row=row, column=1, value="Filtro de fecha:")
                ws.cell(row=row, column=2, value=f"Del {filter_info.get('date_from')} al {filter_info.get('date_to')}")
                row += 1
            
            ws.cell(row=row, column=1, value="Turno:")
            ws.cell(row=row, column=2, value=filter_info.get('shift', ''))
            row += 1
            
            # Listar filtros de multiselección dinámicamente
            selected_values = filter_info.get('selected_values', {})
            for field, selected_set in selected_values.items():
                title = self.filter_controls[field]['title']
                ws.cell(row=row, column=1, value=f"{title}:")
                
                if len(selected_set) < 10:
                    ws.cell(row=row, column=2, value=", ".join(selected_set))
                else:
                    ws.cell(row=row, column=2, value=f"{len(selected_set)} seleccionados")
                row += 1
            
            row += 1

            # Escribir estadísticas
            ws.cell(row=row, column=1, value="ESTADÍSTICAS").font = title_font
            row += 1
            ws.cell(row=row, column=1, value="Total de fotos filtradas:")
            ws.cell(row=row, column=2, value=len(filtered_photos))
            row += 2

            # Definir encabezados
            headers = ["Nombre de Archivo", "Ruta", "Fecha", "Latitud", "Longitud", 
                      "Frente de Trabajo", "Coronamiento", "Actividad Realizada", 
                      "Categoría de Observación", "Foto"]
            
            for col, header in enumerate(headers, 1):
                cell = ws.cell(row=row, column=col, value=header)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = center_alignment
            row += 1

            # Escribir datos de las fotos
            total_photos = len(filtered_photos)
            for i, photo in enumerate(filtered_photos):
                filename = os.path.basename(photo['path'])
                path = photo['path']
                date = photo['date'].strftime('%Y-%m-%d %H:%M:%S') if photo['date'] else ""
                lat = f"{photo['coords'][0]:.6f}" if photo['coords'] else ""
                lon = f"{photo['coords'][1]:.6f}" if photo['coords'] else ""
                work_front = photo.get('work_front', '')
                coronation = photo.get('coronation', '')
                activity = photo.get('activity_performed', '')
                category = photo.get('observation_category', '')

                data_row = [filename, path, date, lat, lon, work_front, coronation, activity, category]
                for col, value in enumerate(data_row, 1):
                    ws.cell(row=row, column=col, value=value)

                try:
                    img = Image.open(photo['path'])
                    
                    target_width = 600
                    original_width, original_height = img.size
                    aspect_ratio = original_height / original_width
                    target_height = int(target_width * aspect_ratio)

                    min_width = 200
                    min_height = int(min_width * aspect_ratio)
                    if target_width < min_width:
                        target_width = min_width
                        target_height = min_height

                    img_resized = img.resize((target_width, target_height), Image.Resampling.LANCZOS)

                    img_bytes = BytesIO()
                    img_resized.save(img_bytes, format='JPEG', quality=90)
                    img_bytes.seek(0)

                    xl_img = XLImage(img_bytes)
                    xl_img.name = f"Image_{i}.jpg"
                    ws.row_dimensions[row].height = target_height * 0.75

                    cell = ws.cell(row=row, column=len(headers))
                    ws.add_image(xl_img, cell.coordinate)
                    
                except Exception as e:
                    print(f"Error procesando imagen {photo['path']}: {e}")
                    ws.cell(row=row, column=len(headers), value=f"Error: {str(e)}")
                
                row += 1
                
                if i % 5 == 0 or i == total_photos - 1:
                    progress = (i + 1) / total_photos
                    self.after(0, lambda p=progress: self.progress_bar.set(p))

            for col in range(1, len(headers) + 1):
                ws.column_dimensions[get_column_letter(col)].width = 18
            ws.column_dimensions[get_column_letter(len(headers))].width = 56

            wb.save(file_path)
            self.after(0, lambda: self._export_success(file_path))

        except ImportError:
            self.after(0, lambda: messagebox.showerror("Error", "La biblioteca 'openpyxl' no está instalada.", parent=self))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Ocurrió un error al exportar a Excel:\n{str(e)}", parent=self))
        finally:
            self.after(0, self.progress_bar.grid_forget)

    def _export_success(self, file_path):
        messagebox.showinfo("Exportar a Excel", f"Reporte exportado exitosamente a:\n{file_path}", parent=self)

    def export_to_table_excel(self):
        """Exporta las fotos filtradas a un archivo Excel con tabla de Actividad Realizada + Coronamiento."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
            from openpyxl.utils import get_column_letter
            from collections import defaultdict

            # Obtener las fotos filtradas
            filtered_photos = self.get_filtered_photos()

            if not filtered_photos:
                messagebox.showinfo("Exportar Tabla a Excel", "No hay fotos que coincidan con los filtros actuales para exportar.", parent=self)
                return

            # Abrir diálogo para guardar archivo
            file_path = filedialog.asksaveasfilename(
                defaultextension=".xlsx",
                filetypes=[("Archivos Excel", "*.xlsx"), ("Todos los archivos", "*.*")],
                title="Guardar tabla como..."
            )

            if not file_path:
                return  # El usuario canceló

            # Crear el libro de trabajo
            wb = Workbook()

            # Agrupar fotos por Categoría de Observación
            categories = defaultdict(lambda: defaultdict(list))
            work_fronts_set = set()
            dates_set = set()

            for photo in filtered_photos:
                category = photo.get('observation_category', 'Sin Categoría')
                work_front = photo.get('work_front', 'Sin Frente')
                
                photo_datetime = photo.get('date')
                if not photo_datetime:
                    continue

                # Un día de trabajo va de 7:00 AM a 6:59 AM del día siguiente.
                # Si la foto es antes de las 7 AM, pertenece al día de trabajo anterior.
                if photo_datetime.hour < 7:
                    date = (photo_datetime - timedelta(days=1)).date()
                else:
                    date = photo_datetime.date()

                if date and work_front:
                    categories[category][(work_front, date)].append(photo)
                    work_fronts_set.add(work_front)
                    dates_set.add(date)

            # Convertir sets a listas ordenadas
            work_fronts_list = sorted(list(work_fronts_set))
            dates_list = sorted(list(dates_set))

            # Crear una hoja por cada categoría de observación
            for category, data in categories.items():
                # Crear hoja con el nombre de la categoría (limitar longitud)
                safe_category_name = category[:31] if category else "Sin Nombre"  # Excel limita nombres de hojas a 31 caracteres
                ws = wb.create_sheet(title=safe_category_name)

                # Definir estilos
                header_font = Font(bold=True)
                header_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")  # Gris claro
                center_alignment = Alignment(horizontal="center", vertical="center")
                purple_fill = PatternFill(start_color="800080", end_color="800080", fill_type="solid")  # Púrpura
                white_font = Font(color="FFFFFF")  # Blanco para texto en celda púrpura
                thin_border = Border(
                    left=Side(style='thin'),
                    right=Side(style='thin'),
                    top=Side(style='thin'),
                    bottom=Side(style='thin')
                )

                # Escribir encabezados de fechas en la primera fila (después de la columna de Frente de Trabajo)
                ws.cell(row=1, column=1, value="Frente de Trabajo")
                for col_idx, date in enumerate(dates_list, 2):
                    ws.cell(row=1, column=col_idx, value=date.strftime('%Y-%m-%d'))
                    cell = ws.cell(row=1, column=col_idx)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = center_alignment
                    cell.border = thin_border

                # Aplicar estilo al encabezado de Frente de Trabajo
                header_cell = ws.cell(row=1, column=1)
                header_cell.font = header_font
                header_cell.fill = header_fill
                header_cell.alignment = center_alignment
                header_cell.border = thin_border

                # Escribir Frentes de Trabajo en la primera columna
                for row_idx, work_front in enumerate(work_fronts_list, 2):
                    ws.cell(row=row_idx, column=1, value=work_front)
                    cell = ws.cell(row=row_idx, column=1)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = center_alignment
                    cell.border = thin_border

                # Rellenar la tabla con Actividad Realizada + Coronamiento
                for row_idx, work_front in enumerate(work_fronts_list, 2):
                    for col_idx, date in enumerate(dates_list, 2):
                        key = (work_front, date)
                        
                        if key in data:
                            photos = data[key]
                            
                            # Obtener la primera foto para esta combinación de frente y fecha
                            first_photo = photos[0]
                            activity = first_photo.get('activity_performed', '')
                            coronation = first_photo.get('coronation', '')

                            # Combinar Actividad Realizada + Coronamiento para la celda
                            combined_text = f"{activity} {coronation}".strip()

                            cell = ws.cell(row=row_idx, column=col_idx)
                            if combined_text:
                                cell.value = combined_text
                                cell.fill = purple_fill
                                cell.font = white_font
                                cell.alignment = center_alignment
                            else:
                                cell.value = ""
                            
                            cell.border = thin_border
                            
                            # Agregar comentarios si hay más de una foto con actividades diferentes
                            if len(photos) > 1:
                                from openpyxl.comments import Comment
                                
                                # Crear un conjunto para evitar actividades repetidas en los comentarios
                                unique_activities = set()
                                for photo in photos:
                                    act = photo.get('activity_performed', '')
                                    cor = photo.get('coronation', '')
                                    
                                    # Asegurarse de que no se repitan actividades en los comentarios
                                    combined_act = f"{act} {cor}".strip()
                                    if combined_act and combined_act != combined_text and combined_act not in unique_activities:
                                        unique_activities.add(combined_act)
                                
                                # Crear comentario con todas las actividades únicas
                                if unique_activities:
                                    comment_text = "Otros:\n" + "\n".join(unique_activities)
                                    cell.comment = Comment(comment_text, "Sistema")
                        else:
                            cell = ws.cell(row=row_idx, column=col_idx, value="")
                            cell.border = thin_border
                # Ajustar ancho de columnas
                for col in range(1, len(dates_list) + 2):  # +2 because we have 1 col for header and dates
                    ws.column_dimensions[get_column_letter(col)].width = 25

                # Ajustar alto de filas si es necesario
                for row in range(1, len(work_fronts_list) + 2):
                    ws.row_dimensions[row].height = 30

                # Fijar la primera fila y la primera columna
                ws.freeze_panes = 'B2'  # Esto fija la primera fila y primera columna

            # Eliminar la hoja por defecto si no se usó
            if 'Sheet' in wb.sheetnames:
                std = wb['Sheet']
                if len(wb.sheetnames) > 1:
                    wb.remove(std)
                else:
                    std.title = "Sin Datos"

            # Guardar el archivo
            wb.save(file_path)
            messagebox.showinfo("Exportar Tabla a Excel", f"Tabla exportada exitosamente a:\n{file_path}", parent=self)

        except ImportError:
            messagebox.showerror("Error", "La biblioteca 'openpyxl' no está instalada. Por favor, instale las dependencias con:\npip install -r requirements.txt", parent=self)
        except Exception as e:
            messagebox.showerror("Error", f"Ocurrió un error al exportar la tabla a Excel:\n{str(e)}", parent=self)


if __name__ == "__main__":
    # Configurar apariencia de CustomTkinter (antes de crear la app)
    ctk.set_appearance_mode("light")  # "light" o "dark"
    ctk.set_default_color_theme("blue")  # Temas: "blue", "green", "dark-blue"
    
    app = App() 
    app.mainloop()
