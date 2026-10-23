@echo off
REM RestroBill — one-click Windows .exe build (PyInstaller).
REM Run this once on the restaurant PC. The .exe lands in dist\RestroBill\.
REM Your data (restrobill.db, bills\, kot\, backups\) lives next to the .exe
REM and is never touched by a rebuild.
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --windowed --name RestroBill ^
  main.py
echo.
echo Done. Run dist\RestroBill\RestroBill.exe
pause
