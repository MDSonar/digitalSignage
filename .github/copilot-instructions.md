# Litmus Digital Signage - AI Agent Instructions

## Project Overview
A Flask-based digital signage system for displaying videos, PowerPoint presentations, and YouTube videos on network TVs. Two main services: **Dashboard** (port 5000) for content management and **Web Player** (port 8080) for playback on TVs.

## Architecture & Components

### Data Storage Hierarchy
All runtime data lives under `~/signage/` (or `/root/signage` in containers):
- `content/{videos,presentations}/` - Uploaded media files
- `cache/slides/` - Generated PNG slides from presentations (one subfolder per .pptx)
- `playlists/` - Individual playlist JSON files (legacy)
- `playlists.json` - Multi-playlist store (v2 format, preferred)
- `playlist.json` - Legacy single playlist file (fallback)
- `config.json` - Active playlist ID and display mode (both|video|presentation)
- `youtube_links.json` - YouTube URL metadata
- `commands/` - IPC files for player control (web.json, signage.json)
- `logs/` - Application logs
- `*.pid` - Process ID files for player management

### Service Architecture
- **dashboard.py**: Admin UI (Flask) - upload content, manage playlists, control players, system monitoring
- **web_player.py**: Playback UI (Flask) - serves content carousel to network TVs via web browser
- **utils.py**: Shared playlist utilities with atomic writes, handles v1→v2 migration

### Playlist System Evolution
The codebase supports **two playlist formats** for backward compatibility:

1. **Legacy (v1)**: Single `playlist.json` with list of filenames
2. **Current (v2)**: `playlists.json` with multiple named playlists, UUIDs, timestamps

**Critical Pattern**: Always use `utils.py` functions (`read_playlists()`, `get_playlist_by_id()`, `update_playlist()`) instead of direct JSON reads. Functions handle format detection and migration automatically.

**Playlist Items**: Each item is an object with:
- `name` (string) - filename or YouTube URL
- `repeats` (int) - loop count (default 1)
- `type` (string, optional) - "youtube" for YouTube content

### Content Processing Pipeline
**Presentations → Slides**:
1. Upload .pptx/.ppt file → `content/presentations/`
2. Dashboard converts to PDF (via LibreOffice `unoconv` or `soffice --convert-to pdf`)
3. PDF converted to PNG sequence (via ImageMagick `convert`)
4. PNGs stored in `cache/slides/{presentation_stem}/slide_001.png, slide_002.png...`
5. Web player treats each PNG as a 10-second slide

**Important**: When deleting presentations, MUST also delete corresponding `cache/slides/{filename_stem}/` folder (see [dashboard.py](dashboard.py#L1043-L1049)).

### Player Synchronization
Web player uses hash-based playlist change detection (`/api/playlist_sync`):
1. Calculate MD5 hash of current playlist JSON
2. Compare with previous hash
3. On change, return current item index based on elapsed time
4. Multiple TVs stay synchronized by using server time as reference

## Development Workflows

### Running Locally (Windows/Linux/Mac)
```powershell
# Install dependencies
pip install -r requirements.txt

# Start dashboard (port 5000)
python dashboard.py

# Start web player (port 8080, separate terminal)
python web_player.py
```

Default credentials: `admin` / `signage` (hash stored in `dashboard.py:USERS`)

### Docker Deployment
```bash
# Development (with hot reload)
docker-compose -f docker-compose.dev.yml up --build

# Production
docker-compose up -d --build
```

**Volume mappings** (see [docker-compose.yml](docker-compose.yml)):
- `signage-content` → persistent media storage
- `signage-cache` → presentation slide cache
- `./logo` → custom branding assets (read-only)

### Process Management
Dashboard can start/stop web player via subprocess:
- **Start**: `python web_player.py` as background process, write PID to `~/signage/web_player.pid`
- **Stop**: Read PID, send SIGTERM (Unix) or `taskkill /F /T` (Windows) to process group
- **Check status**: `is_process_running(pidfile, script_name)` verifies PID exists and matches script

## Project-Specific Conventions

### Path Handling
- Always use `Path.home() / 'signage' / ...` for runtime paths (works cross-platform)
- In Docker: `Path.home()` resolves to `/root/signage`
- Use `Path` objects, not string concatenation
- Use `.stem` for filename without extension: `Path("file.pptx").stem` → `"file"`

### Error Handling & Logging
- Flask routes return `jsonify({'ok': bool, 'error': str})` for API errors
- Log exceptions with `logger.exception('Context message')` (includes traceback)
- Use flash messages for user-facing errors: `flash('Message', 'error'|'success'|'info')`

### File Upload Pattern
1. Save to `UPLOAD_TMP_DIR` with secure_filename
2. Validate extension against `ALLOWED_VIDEO_EXTENSIONS` or `ALLOWED_PPT_EXTENSIONS`
3. Move to final destination (`VIDEOS_DIR` or `PRESENTATIONS_DIR`)
4. For presentations, trigger conversion pipeline immediately
5. Clean up temp files on success/failure

### Atomic Writes
Use `utils.atomic_write(path, json_string)` for all JSON files:
- Writes to `.tmp` file first
- Atomic rename to target (prevents corruption if process crashes)
- Creates parent directories automatically

### Login Requirement
Decorate admin routes with `@login_required` (checks `session['username']`). Web player endpoints are public (no auth) so TVs can display without login.

## Common Pitfalls

1. **Don't read playlists directly from JSON** - Use `read_playlists()` / `get_playlist_by_id()` for format compatibility
2. **Don't forget cache cleanup** - When deleting presentations, remove `cache/slides/{stem}/`
3. **ImageMagick PDF policy** - Docker image modifies `/etc/ImageMagick-6/policy.xml` to allow PDF processing (blocked by default)
4. **Windows vs Unix process termination** - Use `platform.system()` check for `taskkill` vs `os.killpg()`
5. **psutil is optional** - System stats gracefully degrade if `psutil` not installed (check `HAS_PSUTIL`)

## Key Integration Points

### Dashboard ↔ Web Player
- **Playlist sync**: Dashboard writes `playlists.json` → Web player reads on hash change
- **Remote control**: Dashboard writes `commands/web.json` with `{action, ts}` → Web player polls and executes

### External Dependencies
- **LibreOffice**: `soffice --headless --convert-to pdf` for .pptx → PDF
- **ImageMagick**: `convert -density 150 input.pdf output.png` for PDF → PNG sequence
- **FFmpeg** (optional): Could be added for video metadata extraction (duration, resolution)

## Testing Approach
No formal test suite yet. Manual testing workflow:
1. Upload video/presentation via dashboard
2. Add to playlist, set as active
3. Navigate to `http://localhost:8080/player` (web player)
4. Verify content displays and advances correctly
5. Test remote control (next/prev/pause/play buttons)
6. Check `~/signage/logs/` for errors

## Quick Reference: Common Tasks

**Add new playlist route**:
- Add route in [dashboard.py](dashboard.py) with `@login_required`
- Use `read_playlists()`, `write_playlists(store)` from [utils.py](utils.py)
- Return `jsonify({'ok': bool, ...})` format

**Add new content type**:
1. Define `ALLOWED_*_EXTENSIONS` constant
2. Add upload handler in [dashboard.py](dashboard.py) `/upload_*` route
3. Update `get_playlist()` in [web_player.py](web_player.py) to handle new type
4. Add display logic in [web_player.html](templates/web_player.html)

**Modify player UI**:
- Edit [templates/web_player.html](templates/web_player.html) (Jinja2 template)
- JavaScript in template handles auto-advance, sync polling, command processing
- Styling uses inline CSS (no external framework)
