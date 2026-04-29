# Agent Instructions

## Project

Python app ("Visor de Fotos Georreferenciadas") for viewing geotagged JPEG photos on a custom map image. Two versions: desktop (customtkinter) and web (Flask + Leaflet). UI text is entirely in Spanish.

## Architecture

- **`app.py`** — Desktop version. Monolithic `App(ctk.CTk)` class. All GUI, filtering, caching, export.
- **`app_web.py`** — Web version. Flask server with REST API, serves SPA frontend. Authentication via `@app.before_request` session check. Supports CORS for cross-origin (GitHub Pages) deployment. Uses Waitress for production. CLI commands for user management (`--add-user`, `--delete-user`, `--list-users`).
- **`database.py`** — PostgreSQL operations via `psycopg2`. Schema: `REGISTRO_FT_QC.photos` + `REGISTRO_FT_QC.users`. Connection params are **hardcoded**. User passwords hashed with bcrypt.
- **`utils.py`** — EXIF extraction, coordinate parsing, document name parsing.
- **`index.html`** — SPA with two tabs (Config + Map), login page link, photo viewer, metadata editor, user management. Static HTML (no Jinja2), works served from Flask or GitHub Pages.
- **`login.html`** — Static login page. Auth via `/api/login` AJAX endpoint. Works served from Flask or GitHub Pages.
- **`static/js/config.js`** — API server URL configuration (`API_BASE`). Leave empty for same-origin; set to server URL for GitHub Pages cross-origin deployment.
- **`static/js/main.js`** — Frontend logic: map, filters (Excel-style dependent with counts), photo viewer, metadata editor, config, user management, auth session check.
- **`static/css/style.css`** — Dark theme styling including login page.

## Commands

```bash
pip install -r requirements.txt    # Install deps (Flask, bcrypt, waitress, etc.)
python app_web.py                  # Run web app → http://localhost:5000
python app_web.py --add-user <user> <pass>   # Create a user
python app_web.py --list-users     # List all users
python app_web.py --delete-user <user>        # Delete a user
start_web.bat                      # Windows launcher for web app (uses Waitress)
python app.py                      # Run desktop app (requires PostgreSQL + GTK display)
compile_enhanced.bat               # Build .exe with PyInstaller (Windows, desktop only)
```

### Lint & Test (CI)

```bash
pip install flake8 pytest
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics
pytest
```

No automated tests are currently in the repo (old test files were removed).

## Key Conventions

- **EXIF UserComment JSON keys are camelCase**: `workFront`, `coronation`, `activityPerformed`, `observationCategory` (not snake_case). Code converts between snake_case (DB/Python) and camelCase (EXIF).
- **Coordinate transform**: EPSG:4326 → EPSG:24877. A `pyproj.Transformer` is created once and stored globally.
- **Config persistence**: Saved to platform-specific AppData (`database.get_app_data_path`), **not** local `config.json` (which is gitignored). Web version uses the same config location.
- **Secret key persistence**: Web session key stored at `database.get_app_data_path('secret_key.txt')` so sessions survive server restarts.
- **Database has hardcoded credentials** in `database.py`. Do not modify connection params unless explicitly asked.
- **Photo scanning runs in background threads** with progress bars (desktop) / polling progress (web). Cache update prioritizes recent files (last 30 days for web).
- **Bulk DB ops use `psycopg2.extras.execute_values`** with deadlock retry logic (3 retries).
- **Web API photo paths**: Photo images are served by URL-encoded path via `/api/photo/image?path=...`. Server validates path is within configured photos folder.
- **Authentication**: API routes return 401 JSON if not authenticated. HTML pages redirect to `/login.html`. Sessions expire after 8 hours. Users stored in `REGISTRO_FT_QC.users` table with bcrypt-hashed passwords. Login via `/api/login` (JSON) or `/login` (redirect).
- **Cross-origin (GitHub Pages)**: Set `CORS_ORIGINS` env var to allowed origins (comma-separated). Set `CROSS_ORIGIN=true` env var to enable `SameSite=None; Secure` cookies (requires HTTPS). Frontend uses `API_BASE` in `config.js` to point to the API server.
- **Dependent filters**: The `/api/filters/options` endpoint excludes each field's own selection from filtering, so options for "Frente de Trabajo" depend on what's selected in the other 3 fields. Each option includes a count of matching photos.

## Web API Endpoints

| Method | Route | Auth | Description |
|--------|-------|------|-------------|
| GET | `/` | Yes | SPA index page |
| GET | `/login.html` | No | Login page |
| GET | `/login` | No | Redirect to `/login.html` |
| POST | `/api/login` | No | JSON login (`{username, password}`) |
| GET | `/api/session` | No | Session info (401 if not authenticated) |
| GET | `/api/logout` | No | Logout (JSON response) |
| GET | `/api/config` | Yes | Current config (paths) |
| POST | `/api/config` | Yes | Save config |
| GET | `/api/config/paths` | Yes | Browse filesystem directories |
| GET | `/api/map/bounds` | Yes | Map bounds from COORDENADAS.txt |
| GET | `/api/map/image` | Yes | Serve map image |
| GET | `/api/photos` | Yes | Photos filtered by date/shift/metadata |
| GET | `/api/filters/options` | Yes | Dependent filter options with counts |
| GET | `/api/photos/daterange` | Yes | Min/max dates from DB |
| GET | `/api/photo/image` | Yes | Serve photo image |
| GET | `/api/photo/thumbnail` | Yes | Serve photo thumbnail (150px) |
| PUT | `/api/photo/metadata` | Yes | Update photo metadata (DB + EXIF) |
| POST | `/api/cache/scan` | Yes | Start background scan |
| GET | `/api/cache/progress` | Yes | SSE: scan progress |
| GET | `/api/cache/status` | Yes | Current scan state |
| GET | `/api/documents` | Yes | Documents matching filtered dates |
| GET | `/api/document/open` | Yes | Serve document file |
| GET | `/api/export/excel` | Yes | Download detailed Excel |
| GET | `/api/export/table` | Yes | Download activity matrix Excel |
| GET | `/api/stats` | Yes | Photo counts |
| GET | `/api/users` | Yes | List all users |
| POST | `/api/users` | Yes | Create user |
| PUT | `/api/users/<username>` | Yes | Change password |
| DELETE | `/api/users/<username>` | Yes | Delete user (not self) |

## Gotchas

- `Visor_Fotos.spec` and `compile.bat` contain a stale path (`D:/G2/30_FOTOS_QC/.venv/`). Update when building.
- Images without GPS EXIF are silently skipped (`utils.get_image_metadata` returns `None`).
- Desktop app opens fullscreen (`winfo_screenwidth` × `winfo_screenheight`).
- Web version uses `L.CRS.Simple` with `L.ImageOverlay` for the custom map — UTM coordinates map directly to Leaflet's coordinate system.
- Date filter defaults to last 30 days on first load (`initDateRangeAndLoad`).
- `bcrypt` import inside `database.py` functions is deferred to avoid import errors if not installed — `pip install bcrypt` is required for web auth.
- Waitress is used as production server (`start_web.bat` launches it). Flask debug server is fallback only.