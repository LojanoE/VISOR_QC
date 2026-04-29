@echo off
REM Compilación del Visor de Fotos Georreferenciadas
REM Este script compila la aplicación con PyInstaller en un solo archivo ejecutable

REM Eliminar directorios de compilación anteriores
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

REM Compilar la aplicación
pyinstaller --name "Visor_Fotos" --onefile --windowed --hidden-import="customtkinter" --hidden-import="PIL" --hidden-import="piexif" --hidden-import="pyproj" --hidden-import="tkcalendar" --hidden-import="openpyxl" app.py

REM Mensaje de compilación completada
echo.
echo Compilación completada. El ejecutable está en la carpeta 'dist'
echo.
pause