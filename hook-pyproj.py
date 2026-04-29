from PyInstaller.utils.hooks import collect_data_files

# Recopilar todos los archivos de datos de la biblioteca pyproj
# El parámetro 'include_py_files=True' es importante para asegurar que todo se incluya.
datas, binaries, hiddenimports = collect_data_files("pyproj", include_py_files=True)