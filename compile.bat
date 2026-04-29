@echo off
pyinstaller --name "Visor_Fotos" --onefile --windowed --add-data "D:/G2/30_FOTOS_QC/.venv/Lib/site-packages/customtkinter;customtkinter/" app.py
