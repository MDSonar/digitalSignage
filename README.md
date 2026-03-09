# Litmus Digital Signage (Flask)

## 1. Project Title
**Litmus Digital Signage**

## 2. Project Description
Litmus Digital Signage is a Flask-based content management and playback system for digital screens.

It solves a common operations problem: non-technical teams need a simple way to upload media, organize it into playlists, and show it on one or more display screens without building custom player software.

Typical usage:
- Office TV announcements
- Factory KPI/production dashboards
- Lobby and reception displays
- Training room and cafeteria screens

## 3. Key Features
- Browser-based admin dashboard with login
- Upload support for videos (`.mp4`, `.avi`, `.mov`, `.mkv`, `.webm`)
- Upload support for presentations (`.pptx`, `.ppt`, `.pdf`)
- Automatic presentation-to-slide conversion (PNG cache)
- YouTube link support inside playlists
- Multi-playlist management (create, duplicate, rename, delete, reorder)
- Playlist item repeats and drag-drop ordering
- Browser-based web player for TVs/displays
- Player remote controls (play/pause/next/prev)
- System stats API (CPU/RAM/disk/temp via `psutil`)

## 4. How the System Works
### Admin dashboard
- Runs in `dashboard.py` on port `5000`
- Handles login, uploads, playlist editing, media deletion, and player commands
- Stores state in files under `~/signage`

### Media upload
- Upload endpoint: `POST /upload_chunk/<content_type>`
- Uploads are chunked (safer for large files)
- Video files are stored in `~/signage/content/videos`
- Presentation files are stored in `~/signage/content/presentations`
- Presentations are converted into slide images in `~/signage/cache/slides/<presentation-name>`

### Playlist management
- Canonical playlist store: `~/signage/playlists.json`
- Legacy support: `~/signage/playlist.json`
- Dashboard APIs under `/api/playlists*` manage CRUD/reorder/active playlist

### Web player for display screens
- Runs in `web_player.py` on port `8080`
- UI route: `/`
- Polls playlist API and updates playback automatically
- Supports video, image slides, and YouTube items

## 5. Project Architecture Overview
```text
Admin Browser
   |
   v
Dashboard Flask App (dashboard.py :5000)
   |- Auth, uploads, playlist APIs, commands
   |- Writes files under ~/signage
   v
Storage (~/signage)
   |- content/videos
   |- content/presentations
   |- cache/slides
   |- playlists.json
   |- youtube_links.json
   |- commands/web.json
   v
Web Player Flask App (web_player.py :8080)
   |- Reads playlist/media from ~/signage
   |- Serves player UI and media endpoints
   v
TV Browser(s)
```

## 6. Folder Structure
```text
digitalSignage/
|- dashboard.py            # Admin dashboard Flask app (auth, uploads, playlists, control APIs)
|- web_player.py           # Display/player Flask app for TV browsers
|- utils.py                # Playlist store helpers (playlists.json CRUD + migration)
|- templates/
|  |- dashboard.html       # Admin UI
|  |- login.html           # Login page
|  |- web_player.html      # TV player UI
|- requirements.txt        # Python dependencies
|- Dockerfile              # Docker image build
|- docker-compose.yml      # Production-like multi-service run (dashboard + web-player)
|- docker-compose.dev.yml  # Dev override (hot reload mounts)
|- entrypoint.sh           # Container startup logic
|- start.sh / start.bat    # Quick Docker helper scripts
`- README-DOCKER.md        # Docker-focused guide
```

## 7. Prerequisites
- Python `3.10+` (Python `3.11` recommended)
- `pip`
- `git`
- Optional but recommended for local presentation conversion:
  - LibreOffice (`soffice` in PATH)
  - ImageMagick (`convert` in PATH)
  - Ghostscript
- Optional for containerized setup:
  - Docker + Docker Compose

## 8. Installation Steps
### 1) Clone repository
```bash
git clone <your-repo-url>
cd digitalSignage
```

### 2) Create virtual environment
Windows (PowerShell):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) Install dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## 9. Running the Application
Run both Flask apps in separate terminals.

Terminal 1 (Dashboard):
```bash
python dashboard.py
```

Terminal 2 (Web Player):
```bash
python web_player.py
```

Optional Docker run:
```bash
docker-compose up -d --build
```

## 10. How to Access the Application
- Admin root URL: `http://localhost:5000`
- Admin dashboard URL: `http://localhost:5000/dashboard`
- Web player URL (default): `http://localhost:8080`
- Example player path URL (when reverse proxied): `http://localhost:5000/player`

Default login credentials:
- Username: `admin`
- Password: `signage`

## 11. Example Usage
1. Open `http://localhost:5000` and sign in (`admin` / `signage`).
2. Upload a video or presentation from the dashboard.
3. (Optional) Add YouTube links.
4. Create or select a playlist, then check media items to include them.
5. Reorder playlist items and set repeat counts, then save.
6. Open the display player on the target screen:
   - `http://localhost:8080`
7. Keep the player open full-screen in the display browser.

## 12. Deployment Options
### Local server (developer machine or small LAN host)
- Run `dashboard.py` and `web_player.py`
- Point screens to `http://<host-ip>:8080`

### Raspberry Pi
- Install Python dependencies
- Run both apps on boot (for example with `systemd`)
- Open `http://<pi-ip>:8080` on TV browsers/kiosk mode
- Useful for low-cost on-prem signage endpoints

### Docker
- Use `docker-compose.yml` for dashboard + web player services
- Persistent volumes keep media and playlists
- Recommended when you want reproducible setup and fewer host dependencies

## 13. Troubleshooting
### Port already in use
- If `5000` or `8080` is busy, stop the conflicting process or remap ports (Docker).

### Cannot log in
- Default credentials are `admin` / `signage`.
- If changed in code, restart the dashboard.

### Presentations do not convert to slides
- Verify `soffice` and `convert` are installed and available in PATH.
- Use Docker if local image/PDF toolchain is difficult to set up.

### Upload fails for large files
- Max upload size is configured to `2GB` in `dashboard.py`.
- Check free disk space under your home directory (`~/signage`).

### Player opens but shows nothing
- Ensure playlist has items assigned.
- Check playlist response at `http://localhost:8080/api/playlist`.
- Verify uploaded media exists under `~/signage/content`.

### Where data is stored
- Linux/macOS: `/home/<user>/signage` (or equivalent home path)
- Windows: `C:\Users\<user>\signage`
- Docker containers: `/root/signage`

