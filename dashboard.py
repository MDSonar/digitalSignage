#!/usr/bin/env python3
"""
Digital Signage Web Dashboard
"""

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from urllib.parse import unquote
from functools import wraps
import os
from pathlib import Path
import logging
import time
import shutil  # NEW: For cache deletion
import subprocess
import signal
import sys
import json
from pathlib import PurePath
import platform
import hashlib
from datetime import date as _date
from recurrence_engine import fields_to_rrule
import hmac as _hmac
import threading
import queue
import re

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    _requests = None
from utils import (
    read_playlists, write_playlists, ensure_playlist_store, get_playlist_by_id,
    create_playlist, update_playlist, delete_playlist, duplicate_playlist,
    set_active_playlist, get_active_playlist_id, reorder_playlists,
    read_clients, write_clients, read_config, write_config
)

# Optional: psutil for system stats (graceful degradation if not installed)
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    psutil = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'change-this-secret-key-12345'

app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

VIDEOS_DIR = Path.home() / 'signage' / 'content' / 'videos'
PRESENTATIONS_DIR = Path.home() / 'signage' / 'content' / 'presentations'
YOUTUBE_LINKS_FILE = Path.home() / 'signage' / 'youtube_links.json'
CACHE_DIR = Path.home() / 'signage' / 'cache' / 'slides'  # NEW: Cache directory
CONFIG_FILE = Path.home() / 'signage' / 'config.json'
PLAYLIST_FILE = Path.home() / 'signage' / 'playlist.json'
PLAYLISTS_DIR = Path.home() / 'signage' / 'playlists'
PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)
WEB_PLAYER_PID = Path.home() / 'signage' / 'web_player.pid'
SIGNAGE_PLAYER_PID = Path.home() / 'signage' / 'signage_player.pid'
COMMANDS_DIR = Path.home() / 'signage' / 'commands'
COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
WEB_COMMAND_FILE = COMMANDS_DIR / 'web.json'
SIGNAGE_COMMAND_FILE = COMMANDS_DIR / 'signage.json'
LOGS_DIR = Path.home() / 'signage' / 'logs'
LOGS_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_TMP_DIR = Path.home() / 'signage' / 'uploads_tmp'
UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm'}
ALLOWED_PPT_EXTENSIONS = {'.pptx', '.ppt', '.pdf'}

USERS = {'admin': generate_password_hash('signage')}

# Background task queue for Slack file ingestion (maxsize=50 buffers bursts)
_slack_task_queue: queue.Queue = queue.Queue(maxsize=50)

def allowed_file(filename, allowed_extensions):
    return Path(filename).suffix.lower() in allowed_extensions

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_file_size(filepath):
    size = filepath.stat().st_size
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def get_file_format(filepath):
    ext = filepath.suffix.lower()
    if ext == '.pdf':
        return 'PDF'
    elif ext in ['.pptx', '.ppt']:
        return 'PPTX'
    elif ext in ['.mp4', '.avi', '.mov', '.mkv', '.webm']:
        return 'VIDEO'
    return 'UNKNOWN'

# NEW: Helper function to get cache size
def get_cache_size(presentation_name):
    """Get size of cached slides for a presentation"""
    cache_path = CACHE_DIR / Path(presentation_name).stem
    if cache_path.exists() and cache_path.is_dir():
        total_size = sum(f.stat().st_size for f in cache_path.rglob('*') if f.is_file())
        return get_file_size_from_bytes(total_size)
    return "0 B"

def get_file_size_from_bytes(size):
    """Convert bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def convert_presentation_to_slides(presentation_path: Path):
    """Convert presentation (PPTX/PPT/PDF) to PNG slides.
    
    Pipeline:
    1. If PPTX/PPT: Convert to PDF using LibreOffice
    2. Convert PDF to PNG images using ImageMagick
    3. Save PNGs to cache/slides/{stem}/slide_001.png, slide_002.png, etc.
    """
    try:
        stem = presentation_path.stem
        cache_path = CACHE_DIR / stem
        cache_path.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Converting {presentation_path.name} to slides...")
        
        # Step 1: Convert to PDF if not already PDF
        if presentation_path.suffix.lower() in ['.pptx', '.ppt']:
            pdf_path = cache_path / f"{stem}.pdf"
            
            # Try LibreOffice conversion
            try:
                # Use soffice for conversion (LibreOffice headless)
                cmd = [
                    'soffice',
                    '--headless',
                    '--convert-to', 'pdf',
                    '--outdir', str(cache_path),
                    str(presentation_path)
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                
                if result.returncode != 0:
                    logger.error(f"LibreOffice conversion failed: {result.stderr}")
                    return False
                    
                logger.info(f"✓ Converted to PDF: {pdf_path}")
            except FileNotFoundError:
                logger.error("LibreOffice (soffice) not found. Install libreoffice-impress.")
                return False
            except subprocess.TimeoutExpired:
                logger.error("LibreOffice conversion timed out")
                return False
        else:
            # Already a PDF
            pdf_path = presentation_path
        
        # Step 2: Convert PDF to PNG images using ImageMagick
        if pdf_path.exists():
            try:
                # Use ImageMagick convert command
                output_pattern = str(cache_path / "slide_%03d.png")
                cmd = [
                    'convert',
                    '-density', '150',  # DPI for quality
                    str(pdf_path),
                    output_pattern
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                
                if result.returncode != 0:
                    logger.error(f"ImageMagick conversion failed: {result.stderr}")
                    return False
                
                # Count generated slides
                slides = list(cache_path.glob("slide_*.png"))
                logger.info(f"✓ Generated {len(slides)} slides in {cache_path}")
                
                # Clean up intermediate PDF if we created it
                if pdf_path != presentation_path and pdf_path.exists():
                    pdf_path.unlink()
                
                return True
            except FileNotFoundError:
                logger.error("ImageMagick (convert) not found. Install imagemagick.")
                return False
            except subprocess.TimeoutExpired:
                logger.error("ImageMagick conversion timed out")
                return False
        
        return False
    except Exception:
        logger.exception(f"Failed to convert presentation {presentation_path.name}")
        return False

def read_playlist():
    try:
        if not PLAYLIST_FILE.exists():
            # initialize empty playlist file for first-time runs
            PLAYLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
            PLAYLIST_FILE.write_text(json.dumps([]))
            return []
        return json.loads(PLAYLIST_FILE.read_text())
    except Exception:
        logger.exception('Failed to read playlist')
    return []


def write_playlist(lst):
    try:
        PLAYLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAYLIST_FILE.write_text(json.dumps(lst))
        return True
    except Exception:
        logger.exception('Failed to write playlist')
        return False

# --- Multi-playlist helpers (backward compatible) ---
def list_playlists():
    """Return list of playlist names available in PLAYLISTS_DIR."""
    out = []
    try:
        for p in sorted(PLAYLISTS_DIR.glob('*.json')):
            out.append(p.stem)
    except Exception:
        logger.exception('Failed to list playlists')
    return out

def read_playlist_by_name(name: str):
    """Read a named playlist (stored as JSON list) from PLAYLISTS_DIR."""
    try:
        path = PLAYLISTS_DIR / f"{Path(name).stem}.json"
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        logger.exception('Failed to read named playlist')
    return []

def write_playlist_by_name(name: str, lst):
    try:
        path = PLAYLISTS_DIR / f"{Path(name).stem}.json"
        path.write_text(json.dumps(lst))
        return True
    except Exception:
        logger.exception('Failed to write named playlist')
        return False

def get_active_playlist_name():
    cfg = read_config()
    return cfg.get('activePlaylist')

def set_active_playlist_name(name: str):
    cfg = read_config()
    if name:
        cfg['activePlaylist'] = Path(name).stem
    else:
        cfg.pop('activePlaylist', None)
    return write_config(cfg)

@app.route('/playlists', methods=['GET'])
@login_required
def playlists_index():
    return jsonify({
        'active': get_active_playlist_name(),
        'items': list_playlists()
    })

@app.route('/playlists/create', methods=['POST'])
@login_required
def playlists_create():
    name = request.form.get('name')
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    if name in list_playlists():
        return jsonify({'ok': False, 'error': 'playlist exists'}), 409
    ok = write_playlist_by_name(name, [])
    return jsonify({'ok': ok, 'name': name})

@app.route('/playlists/select', methods=['POST'])
@login_required
def playlists_select():
    name = request.form.get('name')
    if not name or name not in list_playlists():
        return jsonify({'ok': False, 'error': 'unknown playlist'}), 404
    ok = set_active_playlist_name(name)
    return jsonify({'ok': ok, 'active': name})

@app.route('/playlists/delete', methods=['POST'])
@login_required
def playlists_delete():
    name = request.form.get('name')
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    try:
        path = PLAYLISTS_DIR / f"{Path(name).stem}.json"
        if path.exists():
            path.unlink()
        # clear active if deleting active
        if get_active_playlist_name() == Path(name).stem:
            set_active_playlist_name(None)
        return jsonify({'ok': True})
    except Exception:
        logger.exception('Failed to delete playlist')
        return jsonify({'ok': False}), 500

@app.route('/playlists/update', methods=['POST'])
@login_required
def playlists_update():
    """Update items of a named playlist. Expects JSON body with {'name': str, 'items': list}"""
    try:
        payload = request.get_json(force=True)
        name = payload.get('name')
        items = payload.get('items')
        if not isinstance(items, list) or not name:
            return jsonify({'ok': False, 'error': 'invalid payload'}), 400
        ok = write_playlist_by_name(name, items)
        return jsonify({'ok': ok})
    except Exception:
        logger.exception('Failed to update playlist')
        return jsonify({'ok': False}), 500

def read_youtube_links():
    try:
        if YOUTUBE_LINKS_FILE.exists():
            return json.loads(YOUTUBE_LINKS_FILE.read_text())
    except Exception:
        logger.exception("Failed to read youtube links")
    return []

def write_youtube_links(data):
    try:
        YOUTUBE_LINKS_FILE.write_text(json.dumps(data))
        return True
    except Exception:
        logger.exception("Failed to write youtube links")
        return False


def normalize_playlist_to_objects(raw):
    """Return playlist as list of objects: {'name':..., 'repeats': int, 'type': ...}
    Accepts legacy list of strings or list of objects and returns normalized list.
    """
    out = []
    if not raw:
        return out
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out.append({'name': item, 'repeats': 1})
            elif isinstance(item, dict) and item.get('type') == 'youtube':
                url = item.get('url') or item.get('name')
                try:
                    repeats_val = int(item.get('repeats', 1))
                except Exception:
                    repeats_val = 1
                out.append({
                    'name': url,
                    'url': url,
                    'type': 'youtube',
                    'repeats': repeats_val
                })
            elif isinstance(item, dict):
                name = item.get('name') or item.get('filename')
                try:
                    repeats = int(item.get('repeats', 1))
                except Exception:
                    repeats = 1
                if name:
                    # Preserve type if present
                    obj = {'name': name, 'repeats': max(1, repeats)}
                    if item.get('type'):
                        obj['type'] = item.get('type')
                    out.append(obj)

    return out


def playlist_names_set(raw):
    """Return a set of playlist filenames regardless of format."""
    s = set()
    if not raw:
        return s
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                s.add(item)
            elif isinstance(item, dict) and item.get('type') == 'youtube':
                s.add(item.get('url') or item.get('name'))
            elif isinstance(item, dict):
                n = item.get('name') or item.get('filename')
                if n:
                    s.add(n)

    return s


def remove_from_playlist(name_or_url: str) -> bool:
    """Remove any playlist entries matching the given name or URL."""
    try:
        raw = read_playlist()
        items = normalize_playlist_to_objects(raw)
        filtered = [it for it in items if (it.get('name') != name_or_url and it.get('url') != name_or_url)]
        if len(filtered) != len(items):
            return write_playlist(filtered)
    except Exception:
        logger.exception('Failed to remove item from playlist')
    return False


def read_config():
    """Read configuration from config file."""
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text())
    except Exception:
        logger.exception('Failed to read config')
    return {}


def write_config(cfg: dict):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg))
        return True
    except Exception:
        logger.exception('Failed to write config')
        return False


def get_pid_from_pidfile(pidfile: Path):
    try:
        if not pidfile.exists():
            return None
        raw = pidfile.read_text().strip()
        return int(raw)
    except Exception:
        return None


def is_process_running(pidfile: Path, expected_cmd_substr: str = None):
    """Return True if the PID exists and (optionally) matches an expected command substring.
    On Linux, verifies /proc/<pid>/cmdline contains expected_cmd_substr. This prevents stale pidfiles.
    """
    pid = get_pid_from_pidfile(pidfile)
    if not pid:
        return False
    try:
        # Check process exists
        os.kill(pid, 0)
    except Exception:
        return False
    if expected_cmd_substr:
        # Only verify cmdline on Linux where /proc filesystem is available
        if platform.system() == 'Linux':
            try:
                cmdline = Path(f"/proc/{pid}/cmdline").read_text().replace('\x00', ' ')
                return expected_cmd_substr in cmdline
            except Exception:
                return False
        # On other platforms, just return True if process exists
        # (less precise but prevents crashes)
    return True


def start_process(cmd: list, pidfile: Path):
    try:
        # For Raspberry Pi (POSIX), create a new session (process group leader)
        # so we can kill the entire group later.
        stdout_log = LOGS_DIR / f"{Path(cmd[-1]).stem}.out.log"
        stderr_log = LOGS_DIR / f"{Path(cmd[-1]).stem}.err.log"
        logger.info("Starting process: %s, stdout=%s, stderr=%s", cmd, stdout_log, stderr_log)
        
        # Windows compatibility: use CREATE_NEW_PROCESS_GROUP instead of os.setsid
        if platform.system() == 'Windows':
            proc = subprocess.Popen(cmd, stdout=open(stdout_log, 'ab'), stderr=open(stderr_log, 'ab'), 
                                  creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=False)
        else:
            proc = subprocess.Popen(cmd, stdout=open(stdout_log, 'ab'), stderr=open(stderr_log, 'ab'), 
                                  preexec_fn=os.setsid, close_fds=True)
        
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(proc.pid))
        logger.info(f"Started process {cmd[0]} pid={proc.pid}")
        return True
    except Exception:
        logger.exception('Failed to start process')
        return False


def stop_process(pidfile: Path):
    try:
        if not pidfile.exists():
            return False
        raw = pidfile.read_text().strip()
        pid = int(raw)

        # Try graceful terminate of the process group
        try:
            if platform.system() == 'Windows':
                # Windows: use taskkill for process tree termination
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                # POSIX: use killpg to terminate process group
                os.killpg(pid, signal.SIGTERM)
        except Exception:
            try:
                # Fallback to killing single process
                if platform.system() == 'Windows':
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                logger.warning('Failed to SIGTERM pid/pgrp=%s', pid)

        # wait a short time for process to exit
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.2)
            except Exception:
                break

        # If still running, force kill the group
        try:
            if platform.system() == 'Windows':
                # Already killed forcefully with /F flag above
                pass
            else:
                # POSIX: force kill with SIGKILL
                os.killpg(pid, signal.SIGKILL)
        except Exception:
            try:
                if platform.system() != 'Windows':
                    os.kill(pid, signal.SIGKILL)
            except Exception:
                logger.warning('Failed to SIGKILL pid/pgrp=%s', pid)

        try:
            pidfile.unlink()
        except Exception:
            pass

        logger.info(f"Stopped pid={pid}")
        return True
    except Exception:
        logger.exception('Failed to stop process')
        try:
            pidfile.unlink(missing_ok=True)
        except Exception:
            pass
        return False


# Serve static files (logos, etc) from ~/signage/logo
@app.route('/static/<path:filename>')
def serve_static(filename):
    logo_dir = Path.home() / 'signage' / 'logo'
    logo_dir.mkdir(parents=True, exist_ok=True)
    return send_from_directory(logo_dir, filename)


@app.route('/control/send_command', methods=['POST'])
@login_required
def send_command():
    target = request.form.get('target')  # 'web' or 'signage'
    action = request.form.get('action')  # 'next','prev','pause','play'
    if target not in ('web', 'signage') or action not in ('next','prev','pause','play'):
        flash('Invalid command', 'error')
        return redirect(url_for('dashboard'))

    cmdfile = WEB_COMMAND_FILE if target == 'web' else SIGNAGE_COMMAND_FILE
    try:
        cmdfile.write_text(json.dumps({'action': action, 'ts': time.time()}))
        flash(f'Sent {action} to {target}', 'success')
    except Exception:
        logger.exception('Failed to write command')
        flash('Failed to send command', 'error')
    return redirect(url_for('dashboard'))


@app.route('/upload_chunk/<content_type>', methods=['POST'])
@login_required
def upload_chunk(content_type):
    """Receive a chunked upload. Expects form-data fields:
    - file: the binary chunk
    - filename: original filename
    - index: zero-based chunk index
    - total: total number of chunks
    The server writes chunks to UPLOAD_TMP_DIR/<secure_filename(filename)>/part_<index>
    When the last chunk is received, the server assembles the parts into the final file
    and triggers the same background conversion logic used by the normal upload endpoint.
    """
    if content_type not in ('video', 'presentation'):
        return jsonify({'ok': False, 'error': 'invalid content type'}), 400

    if 'file' not in request.files:
        return jsonify({'ok': False, 'error': 'no file part'}), 400

    file = request.files['file']
    filename = request.form.get('filename') or file.filename
    try:
        index = int(request.form.get('index', 0))
        total = int(request.form.get('total', 1))
    except Exception:
        return jsonify({'ok': False, 'error': 'invalid index/total'}), 400

    safe_name = secure_filename(filename)
    tmp_dir = UPLOAD_TMP_DIR / safe_name
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        part_path = tmp_dir / f'part_{index:06d}'
        # Save chunk
        file.save(str(part_path))
        logger.info(f'Received chunk {index+1}/{total} for {safe_name}')

        # If last chunk, assemble
        if index + 1 >= total:
            # Determine final path
            if content_type == 'video':
                target_dir = VIDEOS_DIR
            else:
                target_dir = PRESENTATIONS_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            final_path = target_dir / safe_name

            # Assemble parts in order
            with final_path.open('wb') as fout:
                for i in range(total):
                    part_file = tmp_dir / f'part_{i:06d}'
                    if not part_file.exists():
                        logger.warning(f'Missing chunk {i} while assembling {safe_name}')
                        continue
                    with part_file.open('rb') as fin:
                        shutil.copyfileobj(fin, fout)

            logger.info(f'Assembled upload to {final_path}')

            # cleanup parts
            try:
                shutil.rmtree(tmp_dir)
            except Exception:
                logger.exception('Failed to cleanup tmp upload dir')

            # Convert presentation to slides if it's a presentation
            if content_type == 'presentation':
                logger.info(f'Starting presentation conversion for {safe_name}')
                convert_presentation_to_slides(final_path)

        return jsonify({'ok': True, 'index': index, 'total': total})
    except Exception:
        logger.exception('Chunk upload failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500

@app.route('/add_youtube_link', methods=['POST'])
@login_required
def add_youtube_link():
    url = request.form.get('youtube_url', "").strip()
    name = request.form.get('youtube_name', "").strip() or url

    if not url or (("youtube.com" not in url) and ("youtu.be" not in url)):
        flash("Invalid YouTube link", "error")
        return redirect(url_for('dashboard'))

    links = read_youtube_links()
    links.append({"name": name, "url": url})
    write_youtube_links(links)

    flash("YouTube link added!", "success")
    return redirect(url_for('dashboard'))

@app.route('/delete_youtube_link', methods=['POST'])
@login_required
def delete_youtube_link():
    url = request.form.get("url")
    links = read_youtube_links()
    links = [l for l in links if l["url"] != url]
    write_youtube_links(links)
    # Also remove from playlist if present
    remove_from_playlist(url)
    flash("YouTube link deleted!", "success")
    return redirect(url_for("dashboard"))

@app.route('/')
def index():
    if 'username' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username in USERS and check_password_hash(USERS[username], password):
            session['username'] = username
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    flash('Logged out', 'success')
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    videos = []
    presentations = []
    youtube_links = []
    yt_data = read_youtube_links()
    ensure_playlist_store()
    active_id = get_active_playlist_id()
    if active_id:
        pl = get_playlist_by_id(active_id)
        playlist = pl.get('items', []) if pl else []
    else:
        playlist = read_playlist()
    playlist_set = playlist_names_set(playlist)
    
    try:
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        PRESENTATIONS_DIR.mkdir(parents=True, exist_ok=True)
        
        if VIDEOS_DIR.exists():
            for file in sorted(VIDEOS_DIR.iterdir()):
                if file.is_file() and file.suffix.lower() in ALLOWED_VIDEO_EXTENSIONS:
                    videos.append({
                        'name': file.name,
                        'size': get_file_size(file),
                        'type': 'video',
                        'format': get_file_format(file),
                        'in_playlist': file.name in playlist_set
                    })
        
        if PRESENTATIONS_DIR.exists():
            for file in sorted(PRESENTATIONS_DIR.iterdir()):
                if file.is_file() and file.suffix.lower() in ALLOWED_PPT_EXTENSIONS:
                    presentations.append({
                        'name': file.name,
                        'size': get_file_size(file),
                        'type': 'presentation',
                        'format': get_file_format(file),
                        'in_playlist': file.name in playlist_set
                    })

        for item in yt_data:
            youtube_links.append({
                "name": item.get("name"),
                "url": item.get("url"),
                "type": "youtube",
                "in_playlist": item.get("url") in playlist_set
            })
        
    except Exception as e:
        logger.error(f"Error loading dashboard: {e}", exc_info=True)
        flash('Error loading files', 'error')
    
    # Check process status
    web_running = is_process_running(WEB_PLAYER_PID, 'web_player.py')

    return render_template('dashboard.html', 
                        videos=videos, 
                        presentations=presentations, 
                        youtube_links=youtube_links,
                        username=session.get('username'),
                        web_running=web_running,
                        playlist=playlist,
                        playlist_set=playlist_set)


@app.route('/control/toggle_playlist/<content_type>/<path:filename>', methods=['POST'])
# Made public so playlist toggles don't break if session cookies expire during long dashboard use
def toggle_playlist(content_type, filename):
    """Toggle an item (video/presentation/youtube) in/out of the playlist
    
    Optional query param: ?playlist_id=<id> to target specific playlist.
    If not provided, uses active playlist for backward compatibility.
    """

    # decode URL encoded filename/url (client sends encodeURIComponent(...))
    filename = unquote(filename)
    
    # Check for explicit playlist_id param
    target_playlist_id = request.args.get('playlist_id')
    
    logger.info(f"Toggle playlist request - type: {content_type}, filename: {filename}, playlist_id: {target_playlist_id}")

    # Allow YouTube links
    if content_type not in ('video', 'presentation', 'youtube'):
        logger.error(f"Invalid content type: {content_type}")
        return jsonify({'ok': False, 'error': 'invalid content type'}), 400

    try:
        # Use explicit playlist_id or fallback to active for backward compat
        playlist_id = target_playlist_id or get_active_playlist_id()
        if playlist_id:
            pl = get_playlist_by_id(playlist_id)
            raw = pl.get('items', []) if pl else []
        else:
            raw = read_playlist()
        items = normalize_playlist_to_objects(raw)
        logger.info(f"Current playlist items: {len(items)}")

        # -----------------------------
        # SPECIAL HANDLING: YOUTUBE LINK
        # -----------------------------
        if content_type == 'youtube':
            # normalize filename/url for matching
            target_url = filename

            # Check if URL already exists (match on name or url field)
            found = None
            for it in items:
                if it.get('type') == 'youtube' and (it.get('name') == target_url or it.get('url') == target_url):
                    found = it
                    break

            if found:
                # Remove it
                items = [
                    it for it in items
                    if not (it.get('type') == 'youtube' and (it.get('name') == target_url or it.get('url') == target_url))
                ]
                in_playlist = False
            else:
                # Add new YouTube entry
                items.append({
                    'name': target_url,   # This is the URL
                    'url': target_url,
                    'type': 'youtube',
                    'repeats': 1
                })
                in_playlist = True

            if playlist_id:
                ok, _ = update_playlist(playlist_id, items=items)
            else:
                ok = write_playlist(items)
            if not ok:
                logger.error("Failed to write playlist to file")
                return jsonify({'ok': False, 'error': 'failed to save playlist'}), 500
            logger.info(f"YouTube toggle success - in_playlist: {in_playlist}")
            return jsonify({'ok': True, 'in_playlist': in_playlist, 'filename': filename})

        # -----------------------------
        # NORMAL HANDLING (video/presentation)
        # -----------------------------
        found = None
        for it in items:
            if it.get('name') == filename:
                found = it
                break

        if found:
            # Remove entries
            items = [it for it in items if it.get('name') != filename]
            in_playlist = False
        else:
            # Add new entry — include explicit type for clarity
            items.append({'name': filename, 'type': content_type, 'repeats': 1})
            in_playlist = True

        if playlist_id:
            ok, _ = update_playlist(playlist_id, items=items)
        else:
            ok = write_playlist(items)
        if ok:
            logger.info(f"Toggle success for {filename} - in_playlist: {in_playlist}")
            return jsonify({'ok': True, 'in_playlist': in_playlist, 'filename': filename})
        else:
            logger.error("Failed to write playlist")
            return jsonify({'ok': False, 'error': 'failed to save'}), 500

    except Exception as e:
        logger.exception('Failed to toggle playlist')
        return jsonify({'ok': False, 'error': str(e)}), 500

@app.route('/api/playlist_order', methods=['GET'])
def api_playlist_order_get():
    """Return normalized playlist (ordered) as list of objects {name, repeats}."""
    try:
        # Use active playlist id if configured; otherwise legacy single file
        active_id = get_active_playlist_id()
        if active_id:
            pl = get_playlist_by_id(active_id)
            raw = pl.get('items', []) if pl else []
        else:
            raw = read_playlist()
        items = normalize_playlist_to_objects(raw)
        return jsonify({'ok': True, 'playlist': items})
    except Exception:
        logger.exception('Failed to return playlist order')
        return jsonify({'ok': False, 'error': 'server error'}), 500


# Public playlist endpoint for diagnostics and player checks (no auth)
@app.route('/api/playlist', methods=['GET'])
def api_playlist():
    try:
        active = get_active_playlist_name()
        raw = read_playlist_by_name(active) if active else read_playlist()
        playlist = normalize_playlist_to_objects(raw)
        playlist_hash = hashlib.md5(json.dumps(playlist, sort_keys=True).encode()).hexdigest()
        return jsonify({'playlist': playlist, 'hash': playlist_hash, 'count': len(playlist)})
    except Exception:
        logger.exception('Failed to return playlist')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/playlist_order', methods=['POST'])
def api_playlist_order_post():
    """Accept a JSON array of objects {name, repeats, [type]} and save as the playlist order."""
    try:
        data = request.get_json()
        if not isinstance(data, list):
            return jsonify({'ok': False, 'error': 'expected a JSON array'}), 400

        normalized = []
        for entry in data:
            # Allow caller to supply string (name) or dict
            if isinstance(entry, str):
                # detect youtube-like urls and tag appropriately
                if entry.startswith('http') and ('youtube.com' in entry or 'youtu.be' in entry):
                    normalized.append({'name': entry, 'type': 'youtube', 'repeats': 1})
                else:
                    normalized.append({'name': entry, 'repeats': 1})
            elif isinstance(entry, dict):
                name = entry.get('name') or entry.get('url') or entry.get('filename')
                if not name:
                    continue
                try:
                    repeats = int(entry.get('repeats', 1))
                except Exception:
                    repeats = 1

                # preserve type if provided, otherwise infer youtube by URL
                etype = entry.get('type')
                if not etype:
                    if isinstance(name, str) and name.startswith('http') and ('youtube.com' in name or 'youtu.be' in name):
                        etype = 'youtube'

                item = {'name': name, 'repeats': max(1, repeats)}
                if etype:
                    item['type'] = etype
                    # prefer explicit url key for youtube if available
                    if etype == 'youtube':
                        # keep both fields safe: some code expects item['url']
                        item['url'] = entry.get('url') or name

                normalized.append(item)

        # Save to active playlist id if configured; otherwise legacy single file
        active_id = get_active_playlist_id()
        if active_id:
            ok, _ = update_playlist(active_id, items=normalized)
        else:
            ok = write_playlist(normalized)
        if ok:
            return jsonify({'ok': True, 'playlist': normalized})
        else:
            return jsonify({'ok': False, 'error': 'failed to save'}), 500
    except Exception:
        logger.exception('Failed to save playlist order')
        return jsonify({'ok': False, 'error': 'server error'}), 500

@app.route('/control/start_web_player', methods=['POST'])
@login_required
def start_web_player():
    # Start the web player Flask app in background
    if is_process_running(WEB_PLAYER_PID, 'web_player.py'):
        flash('Web player already running', 'error')
        return redirect(url_for('dashboard'))
    python = sys.executable or 'python3'
    script = Path(__file__).parent / 'web_player.py'
    ok = start_process([python, str(script)], WEB_PLAYER_PID)
    flash('Web player started' if ok else 'Failed to start web player', 'success' if ok else 'error')
    return redirect(url_for('dashboard'))


@app.route('/control/stop_web_player', methods=['POST'])
@login_required
def stop_web_player():
    ok = stop_process(WEB_PLAYER_PID)
    flash('Web player stopped' if ok else 'Failed to stop web player', 'success' if ok else 'error')
    return redirect(url_for('dashboard'))


@app.route('/control/start_signage_player', methods=['POST'])
@login_required
def start_signage_player():
    flash('HDMI player (player.py) is not deployed in this setup. Use Web Player instead.', 'info')
    return redirect(url_for('dashboard'))


@app.route('/control/stop_signage_player', methods=['POST'])
@login_required
def stop_signage_player():
    flash('HDMI player (player.py) is not deployed in this setup.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/api/playlists', methods=['GET'])
@login_required
def api_playlists_list():
    store = read_playlists()
    items = []
    for pl in store.get('playlists', []):
        items.append({
            'id': pl.get('id'),
            'name': pl.get('name'),
            'itemCount': len(pl.get('items', [])),
            'created_at': pl.get('created_at'),
            'updated_at': pl.get('updated_at')
        })
    return jsonify({'playlists': items, 'active_playlist_id': store.get('active_playlist_id')})

@app.route('/api/playlists', methods=['POST'])
@login_required
def api_playlists_create():
    name = request.form.get('name') or (request.json and request.json.get('name'))
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    ok, pl = create_playlist(name)
    if not ok:
        return jsonify({'ok': False, 'error': 'create failed'}), 500
    return jsonify({'id': pl['id'], 'playlist': pl}), 201

@app.route('/api/playlists/<pid>', methods=['GET'])
@login_required
def api_playlists_get(pid):
    pl = get_playlist_by_id(pid)
    if not pl:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    phash = hashlib.md5(json.dumps(pl.get('items', []), sort_keys=True).encode()).hexdigest()
    return jsonify({'playlist': pl, 'hash': phash})

@app.route('/api/playlists/<pid>', methods=['PUT'])
@login_required
def api_playlists_put(pid):
    payload = request.get_json(force=True)
    name = payload.get('name')
    items = payload.get('items')
    kwargs = {}
    if 'scheduler_enabled' in payload:
        kwargs['scheduler_enabled'] = bool(payload['scheduler_enabled'])
    if 'scheduler_default' in payload:
        kwargs['scheduler_default'] = payload['scheduler_default']
    ok, pl = update_playlist(pid, name=name, items=items, **kwargs)
    if not ok or not pl:
        return jsonify({'ok': False, 'error': 'update failed'}), 500
    return jsonify({'playlist': pl})

@app.route('/api/playlists/<pid>', methods=['DELETE'])
@login_required
def api_playlists_delete(pid):
    ok = delete_playlist(pid)
    return jsonify({'ok': ok})

@app.route('/api/playlists/<pid>/reorder', methods=['POST'])
@login_required
def api_playlists_reorder(pid):
    payload = request.get_json(force=True)
    items = payload.get('items')
    if not isinstance(items, list):
        return jsonify({'ok': False, 'error': 'items required'}), 400
    ok, pl = update_playlist(pid, items=items)
    if not ok:
        return jsonify({'ok': False, 'error': 'reorder failed'}), 500
    return jsonify({'playlist': pl})

@app.route('/api/playlists/<pid>/duplicate', methods=['POST'])
@login_required
def api_playlists_duplicate(pid):
    ok, pl = duplicate_playlist(pid)
    if not ok or not pl:
        return jsonify({'ok': False, 'error': 'duplicate failed'}), 500
    return jsonify({'id': pl['id'], 'playlist': pl}), 201

@app.route('/api/playlists/set_active', methods=['POST'])
@login_required
def api_playlists_set_active():
    pid = request.form.get('playlist_id') or (request.json and request.json.get('playlist_id'))
    if not pid:
        return jsonify({'ok': False, 'error': 'playlist_id required'}), 400
    ok = set_active_playlist(pid)
    if not ok:
        return jsonify({'ok': False, 'error': 'set active failed'}), 400
    return jsonify({'active_playlist_id': pid})

@app.route('/api/playlists/reorder', methods=['POST'])
@login_required
def api_playlists_reorder_list():
    payload = request.get_json(force=True)
    order = payload.get('order')
    if not isinstance(order, list):
        return jsonify({'ok': False, 'error': 'order array required'}), 400
    ok = reorder_playlists(order)
    return jsonify({'ok': ok})


# ── Display Schedules ────────────────────────────────────────────────────────

SCHEDULES_FILE = Path.home() / 'signage' / 'schedules.json'


def read_schedules():
    if SCHEDULES_FILE.exists():
        try:
            return json.loads(SCHEDULES_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'schedules': []}


def write_schedules(data):
    SCHEDULES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


@app.route('/api/schedules', methods=['GET'])
@login_required
def api_schedules_list():
    return jsonify(read_schedules())


@app.route('/api/schedules', methods=['POST'])
@login_required
def api_schedules_create():
    data = request.get_json(force=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'name required'}), 400
    store = read_schedules()
    sid = 'sch_' + hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:8]
    schedule = {'id': sid, 'name': name, 'blocks': []}
    store['schedules'].append(schedule)
    write_schedules(store)
    return jsonify({'ok': True, 'schedule': schedule})


@app.route('/api/schedules/<sid>', methods=['PUT'])
@login_required
def api_schedules_update(sid):
    data = request.get_json(force=True) or {}
    store = read_schedules()
    sch = next((s for s in store.get('schedules', []) if s['id'] == sid), None)
    if not sch:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    if 'name' in data:
        sch['name'] = (data['name'] or '').strip()
    write_schedules(store)
    return jsonify({'ok': True, 'schedule': sch})


@app.route('/api/schedules/<sid>', methods=['DELETE'])
@login_required
def api_schedules_delete(sid):
    store = read_schedules()
    store['schedules'] = [s for s in store.get('schedules', []) if s['id'] != sid]
    write_schedules(store)
    try:
        cs = read_clients()
        for c in cs.get('clients', []):
            if c.get('assigned_schedule_id') == sid:
                c['assigned_schedule_id'] = None
        write_clients(cs)
    except Exception:
        pass
    return jsonify({'ok': True})


def _block_from_data(data: dict, existing: dict = None) -> dict:
    """Build a block dict from request data, computing recurrence_rule."""
    repeat_type = data.get('recurrence') or data.get('repeat_type') or 'weekly'
    start_date = data.get('start_date') or ''
    end_date = data.get('end_date') or ''
    no_end_date = bool(data.get('no_end_date')) or (repeat_type == 'once') or not end_date
    days = data.get('days') or []
    rrule = fields_to_rrule(repeat_type, days, start_date)

    base = existing or {}
    base.update({
        'title': data.get('title', base.get('title', '')),
        'enabled': data.get('enabled', base.get('enabled', True)),
        'priority': int(data.get('priority', base.get('priority', 0))),
        'repeat_type': repeat_type,
        'recurrence_rule': rrule,
        'start_date': start_date,
        'end_date': end_date if not no_end_date else '',
        'no_end_date': no_end_date,
        'days': days,
        'start_time': data.get('start_time', base.get('start_time', '08:00')),
        'end_time': data.get('end_time', base.get('end_time', '09:00')),
        'content_type': data.get('content_type', base.get('content_type', 'playlist')),
        'playlist_id': data.get('playlist_id') or base.get('playlist_id', ''),
        'playlist_name': data.get('playlist_name') or base.get('playlist_name', ''),
        'media_name': data.get('media_name') or base.get('media_name', ''),
        'color': data.get('color') or base.get('color', '#5b50f0'),
    })
    return base


@app.route('/api/schedules/<sid>/blocks', methods=['POST'])
@login_required
def api_schedules_add_block(sid):
    data = request.get_json(force=True) or {}
    store = read_schedules()
    sch = next((s for s in store.get('schedules', []) if s['id'] == sid), None)
    if not sch:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    bid = 'blk_' + hashlib.md5(f"{sid}{time.time()}".encode()).hexdigest()[:8]
    block = _block_from_data(data)
    block['id'] = bid
    sch.setdefault('blocks', []).append(block)
    write_schedules(store)
    return jsonify({'ok': True, 'block': block})


@app.route('/api/schedules/<sid>/blocks/<bid>', methods=['PUT'])
@login_required
def api_schedules_update_block(sid, bid):
    data = request.get_json(force=True) or {}
    store = read_schedules()
    sch = next((s for s in store.get('schedules', []) if s['id'] == sid), None)
    if not sch:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    block = next((b for b in sch.get('blocks', []) if b['id'] == bid), None)
    if not block:
        return jsonify({'ok': False, 'error': 'block not found'}), 404
    _block_from_data(data, block)
    write_schedules(store)
    return jsonify({'ok': True, 'block': block})


@app.route('/api/schedules/<sid>/blocks/<bid>', methods=['DELETE'])
@login_required
def api_schedules_delete_block(sid, bid):
    store = read_schedules()
    sch = next((s for s in store.get('schedules', []) if s['id'] == sid), None)
    if not sch:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    sch['blocks'] = [b for b in sch.get('blocks', []) if b['id'] != bid]
    write_schedules(store)
    return jsonify({'ok': True})


@app.route('/delete/<content_type>/<filename>', methods=['POST'])
@login_required
def delete_file(content_type, filename):
    try:
        source_dir = VIDEOS_DIR if content_type == 'video' else PRESENTATIONS_DIR
        filepath = source_dir / filename
        
        if filepath.exists() and filepath.is_file():
            # NEW: Delete cache folder for presentations
            if content_type == 'presentation':
                cache_path = CACHE_DIR / filepath.stem
                if cache_path.exists() and cache_path.is_dir():
                    try:
                        shutil.rmtree(cache_path)
                        logger.info(f"✓ Deleted cache folder: {cache_path.name}")
                    except Exception as cache_error:
                        logger.warning(f"Failed to delete cache: {cache_error}")
            
            # Delete the file
            filepath.unlink()
            flash(f'✓ Deleted: {filename}', 'success')
            logger.info(f"Deleted {content_type}: {filename}")
            # Ensure playlist no longer references this file
            remove_from_playlist(filename)
        else:
            flash(f'File not found: {filename}', 'error')
            
    except Exception as e:
        flash(f'Delete failed: {str(e)}', 'error')
        logger.error(f"Delete error: {e}", exc_info=True)
    
    return redirect(url_for('dashboard'))

@app.route('/api/system_stats')
@login_required
def api_system_stats():
    """Return system statistics: CPU, RAM, disk, temperature."""
    if not HAS_PSUTIL:
        logger.warning('psutil module not available')
        return jsonify({'ok': False, 'error': 'psutil not installed'}), 503
    
    try:
        stats = {}
        
        # CPU usage
        try:
            stats['cpu_percent'] = psutil.cpu_percent(interval=0.1)
        except Exception:
            stats['cpu_percent'] = None
        
        # RAM usage
        try:
            vm = psutil.virtual_memory()
            stats['ram_percent'] = vm.percent
            stats['ram_used_gb'] = round(vm.used / (1024**3), 2)
            stats['ram_total_gb'] = round(vm.total / (1024**3), 2)
        except Exception:
            stats['ram_percent'] = None
        
        # Disk usage (root /)
        try:
            disk = psutil.disk_usage('/')
            stats['disk_percent'] = disk.percent
            stats['disk_used_gb'] = round(disk.used / (1024**3), 2)
            stats['disk_total_gb'] = round(disk.total / (1024**3), 2)
        except Exception:
            stats['disk_percent'] = None
        
        # Temperature (try Linux thermal zones or system temp)
        stats['temp_c'] = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                # Try to get CPU temp from common sources
                for name, entries in temps.items():
                    if name.lower() in ('cpu', 'coretemp', 'k10temp', 'acpitz'):
                        if entries:
                            stats['temp_c'] = round(entries[0].current, 1)
                            break
                # If no CPU temp found, use first available
                if stats['temp_c'] is None and temps:
                    first_key = next(iter(temps))
                    if temps[first_key]:
                        stats['temp_c'] = round(temps[first_key][0].current, 1)
        except Exception:
            pass
        
        # System info
        stats['hostname'] = platform.node()
        stats['platform'] = platform.system()
        
        return jsonify({'ok': True, 'stats': stats})
    except Exception:
        logger.exception('Failed to get system stats')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/content')
@login_required
def api_content():
    """Return all available content files for the scheduler default-content picker."""
    videos = []
    if VIDEOS_DIR.exists():
        videos = sorted({
            f.name
            for ext in ALLOWED_VIDEO_EXTENSIONS
            for f in VIDEOS_DIR.glob(f'*{ext}')
        })
    presentations = []
    if PRESENTATIONS_DIR.exists():
        presentations = sorted({
            f.name
            for ext in ALLOWED_PPT_EXTENSIONS
            for f in PRESENTATIONS_DIR.glob(f'*{ext}')
        })
    youtube = []
    try:
        yt_data = read_youtube_links()
        youtube = [{'url': y.get('url'), 'title': y.get('name') or y.get('url')} for y in yt_data if y.get('url')]
    except Exception:
        pass
    return jsonify({'videos': videos, 'presentations': presentations, 'youtube': youtube})


@app.route('/api/server_time')
@login_required
def api_server_time():
    """Return current server time in the configured timezone."""
    from datetime import datetime
    cfg = read_config()
    tz_name = (cfg.get('timezone') or os.environ.get('TZ', 'UTC') or 'UTC').strip()
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz_name))
        display_tz = tz_name
    except Exception:
        now = datetime.now()
        display_tz = 'system'
    return jsonify({
        'time': now.strftime('%H:%M'),
        'datetime': now.strftime('%Y-%m-%d %H:%M:%S'),
        'timezone': display_tz,
    })


@app.route('/api/config', methods=['GET'])
@login_required
def api_get_config():
    """Return current signage configuration (timezone, etc.)."""
    cfg = read_config()
    tz_name = (cfg.get('timezone') or os.environ.get('TZ', 'UTC') or 'UTC').strip()
    return jsonify({'timezone': tz_name})


@app.route('/api/config', methods=['POST'])
@login_required
def api_set_config():
    """Update signage configuration. Body: {timezone: 'Asia/Kolkata'}"""
    data = request.get_json(silent=True) or {}
    cfg = read_config()
    if 'timezone' in data:
        tz = (data['timezone'] or '').strip()
        if tz:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(tz)           # raises if invalid
                cfg['timezone'] = tz
            except Exception:
                return jsonify({'ok': False, 'error': f'Invalid timezone: {tz}'}), 400
        else:
            cfg.pop('timezone', None)
    ok = write_config(cfg)
    return jsonify({'ok': ok, 'config': cfg})


# ---------------------------------------------------------------------------
# Client (RPi) API — registration and heartbeat are public (no login_required)
# so RPis can self-register without a browser session.
# ---------------------------------------------------------------------------

@app.route('/api/clients/register', methods=['POST'])
def api_clients_register():
    """RPi calls this on startup to register or re-register itself."""
    try:
        data = request.get_json(force=True) or {}
        mac = data.get('mac', '').strip()
        ip  = data.get('ip', request.remote_addr).strip()
        name = data.get('name', data.get('hostname', 'RPi')).strip()
        hostname = data.get('hostname', '').strip()
        agent_version = data.get('agent_version', '1.0')

        if not mac:
            return jsonify({'ok': False, 'error': 'mac required'}), 400

        store = read_clients()
        clients = store.get('clients', [])
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

        existing = next((c for c in clients if c.get('mac') == mac), None)
        if existing:
            existing['ip'] = ip
            existing['hostname'] = hostname
            existing['agent_version'] = agent_version
            existing['last_seen'] = now
            client_id = existing['id']
        else:
            import uuid as _uuid
            client_id = str(_uuid.uuid4())
            clients.append({
                'id': client_id,
                'name': name,
                'ip': ip,
                'mac': mac,
                'hostname': hostname,
                'agent_version': agent_version,
                'assigned_playlist_id': None,
                'pending_command': None,
                'registered_at': now,
                'last_seen': now,
                'stats': {}
            })
        store['clients'] = clients
        write_clients(store)
        logger.info(f'Client registered: {name} ({ip}) id={client_id}')
        return jsonify({'ok': True, 'client_id': client_id})
    except Exception:
        logger.exception('Client register failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients/<cid>/heartbeat', methods=['POST'])
def api_clients_heartbeat(cid):
    """RPi posts stats here every ~30s."""
    try:
        data = request.get_json(force=True) or {}
        store = read_clients()
        client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
        if not client:
            return jsonify({'ok': False, 'error': 'unknown client'}), 404
        client['last_seen'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        client['ip'] = data.get('ip', client.get('ip', ''))
        client['stats'] = data.get('stats', client.get('stats', {}))
        # Return any pending command so RPi can act on it
        pending = client.get('pending_command')
        client['pending_command'] = None   # clear after delivery
        write_clients(store)
        return jsonify({'ok': True, 'pending_command': pending})
    except Exception:
        logger.exception('Heartbeat failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients/<cid>/config', methods=['GET'])
def api_clients_config(cid):
    """RPi polls this to get its assigned playlist."""
    try:
        store = read_clients()
        client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
        if not client:
            return jsonify({'ok': False, 'error': 'unknown client'}), 404
        pid = client.get('assigned_playlist_id')
        playlist_name = None
        if pid:
            pl = get_playlist_by_id(pid)
            playlist_name = pl.get('name') if pl else None
        return jsonify({
            'ok': True,
            'client_id': cid,
            'assigned_playlist_id': pid,
            'playlist_name': playlist_name
        })
    except Exception:
        logger.exception('Client config failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients', methods=['GET'])
@login_required
def api_clients_list():
    """List all clients with online/offline status."""
    try:
        store = read_clients()
        clients = store.get('clients', [])
        now_ts = time.time()
        result = []
        for c in clients:
            last_seen_str = c.get('last_seen')
            seconds_ago = None
            online = False
            if last_seen_str:
                try:
                    import calendar
                    t = time.strptime(last_seen_str, '%Y-%m-%dT%H:%M:%SZ')
                    last_ts = calendar.timegm(t)
                    seconds_ago = int(now_ts - last_ts)
                    online = seconds_ago < 90
                except Exception:
                    pass
            result.append({
                **c,
                'online': online,
                'seconds_ago': seconds_ago
            })
        return jsonify({'ok': True, 'clients': result})
    except Exception:
        logger.exception('List clients failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients/<cid>/assign', methods=['POST'])
@login_required
def api_clients_assign(cid):
    """Assign a playlist or schedule to a client."""
    try:
        data = request.get_json(force=True) or {}
        pid = data.get('playlist_id')
        sid = data.get('schedule_id') or None
        store = read_clients()
        client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
        if not client:
            return jsonify({'ok': False, 'error': 'unknown client'}), 404
        client['assigned_playlist_id'] = pid
        client['assigned_schedule_id'] = sid
        write_clients(store)
        return jsonify({'ok': True})
    except Exception:
        logger.exception('Assign playlist failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients/<cid>/command', methods=['POST'])
@login_required
def api_clients_command(cid):
    """Queue a CEC command for the RPi to pick up on next heartbeat."""
    try:
        data = request.get_json(force=True) or {}
        cmd = data.get('command')  # e.g. 'tv_on', 'tv_off', 'reboot'
        if not cmd:
            return jsonify({'ok': False, 'error': 'command required'}), 400
        store = read_clients()
        client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
        if not client:
            return jsonify({'ok': False, 'error': 'unknown client'}), 404
        client['pending_command'] = cmd
        write_clients(store)
        return jsonify({'ok': True})
    except Exception:
        logger.exception('Queue command failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients/<cid>/rename', methods=['POST'])
@login_required
def api_clients_rename(cid):
    try:
        data = request.get_json(force=True) or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'ok': False, 'error': 'name required'}), 400
        store = read_clients()
        client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
        if not client:
            return jsonify({'ok': False, 'error': 'unknown client'}), 404
        client['name'] = name
        write_clients(store)
        return jsonify({'ok': True})
    except Exception:
        logger.exception('Rename client failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


@app.route('/api/clients/<cid>', methods=['DELETE'])
@login_required
def api_clients_delete(cid):
    try:
        store = read_clients()
        before = len(store.get('clients', []))
        store['clients'] = [c for c in store.get('clients', []) if c['id'] != cid]
        if len(store['clients']) == before:
            return jsonify({'ok': False, 'error': 'not found'}), 404
        write_clients(store)
        return jsonify({'ok': True})
    except Exception:
        logger.exception('Delete client failed')
        return jsonify({'ok': False, 'error': 'server error'}), 500


# ---------------------------------------------------------------------------
# Power Automate Ingest — receives file + schedule from Power Automate flow
# ---------------------------------------------------------------------------

@app.route('/api/ingest', methods=['POST'])
def api_ingest():
    """
    Called by Power Automate when a file is shared in Slack.
    Accepts multipart/form-data OR JSON (base64).
    No login_required — Power Automate calls this from the cloud via On-premises gateway.
    Secured by a shared secret key in the X-Ingest-Key header.
    """
    # Simple shared-secret auth (set via Settings modal, stored in config)
    cfg = read_config()
    expected_key = cfg.get('ingest_api_key', '').strip()
    if expected_key:
        provided_key = request.headers.get('X-Ingest-Key', '').strip()
        if not _hmac.compare_digest(provided_key, expected_key):
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401

    try:
        # --- Accept multipart/form-data (Power Automate HTTP with file body) ---
        if request.content_type and 'multipart/form-data' in request.content_type:
            f = request.files.get('file')
            if not f:
                return jsonify({'ok': False, 'error': 'no file in request'}), 400
            filename = secure_filename(f.filename or 'upload.pptx')
            schedule_text = request.form.get('schedule_text', '')
            channel_id = request.form.get('channel_id', '')
            file_bytes = f.read()

        # --- Accept JSON with base64-encoded file content ---
        else:
            data = request.get_json(silent=True) or {}
            filename = secure_filename(data.get('filename', 'upload.pptx'))
            schedule_text = data.get('schedule_text', '')
            channel_id = data.get('channel_id', '')
            b64 = data.get('file_content', '')
            if not b64:
                return jsonify({'ok': False, 'error': 'file_content missing'}), 400
            import base64 as _b64
            try:
                file_bytes = _b64.b64decode(b64)
            except Exception:
                return jsonify({'ok': False, 'error': 'invalid base64'}), 400

        # Validate file type
        ext = Path(filename).suffix.lower()
        if ext in {'.pptx', '.ppt', '.pdf'}:
            subdir = 'presentations'
        elif ext in {'.mp4', '.avi', '.mov', '.mkv', '.webm'}:
            subdir = 'videos'
        else:
            return jsonify({'ok': False, 'error': f'Unsupported file type: {ext}'}), 400

        # Parse schedule from message text
        sched = _slack_parse_schedule(schedule_text)
        if not sched['valid']:
            return jsonify({
                'ok': False,
                'error': 'No schedule found. Add text like: schedule: 14:00-14:30'
            }), 400

        # Save file to local storage
        save_path = Path.home() / 'signage' / 'content' / subdir / filename
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(file_bytes)
        logger.info(f'Ingest: saved {filename} ({len(file_bytes)} bytes) → {save_path}')

        # Queue background processing (PPT conversion + playlist creation)
        _slack_task_queue.put_nowait({
            'event_type': 'power_automate',
            'local_path': str(save_path),
            'filename': filename,
            'schedule': sched,
            'channel_id': channel_id,
        })

        return jsonify({
            'ok': True,
            'message': f'Received {filename} — converting and scheduling in background',
            'schedule': f"{sched['start_time']} – {sched['end_time']}",
        }), 202

    except queue.Full:
        return jsonify({'ok': False, 'error': 'Server busy — try again in a minute'}), 503
    except Exception as e:
        logger.exception('Ingest endpoint error')
        return jsonify({'ok': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Slack Integration — Settings, Webhook, Background Worker
# ---------------------------------------------------------------------------

@app.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    """Return current integration settings (tokens masked)."""
    cfg = read_config()
    def _mask(v):
        return ('•' * (len(v) - 4) + v[-4:]) if len(v) > 4 else '•' * len(v)
    token  = cfg.get('slack_bot_token', '')
    secret = cfg.get('slack_signing_secret', '')
    ikey   = cfg.get('ingest_api_key', '')
    return jsonify({
        'slack_bot_token':      _mask(token)  if token  else '',
        'slack_signing_secret': _mask(secret) if secret else '',
        'slack_channel_id':     cfg.get('slack_channel_id', ''),
        'ingest_api_key':       _mask(ikey)   if ikey   else '',
        'slack_configured':     bool(token and secret),
        'ingest_configured':    bool(ikey),
    })


@app.route('/api/settings', methods=['POST'])
@login_required
def api_set_settings():
    """Save integration settings. Send empty string to clear a field."""
    data = request.get_json(silent=True) or {}
    cfg = read_config()
    for key in ('slack_bot_token', 'slack_signing_secret', 'slack_channel_id', 'ingest_api_key'):
        if key in data:
            val = (data[key] or '').strip()
            # Ignore masked placeholders — only save real values
            if val and not val.startswith('•'):
                cfg[key] = val
            elif not val:
                cfg.pop(key, None)
    ok = write_config(cfg)
    return jsonify({'ok': ok})


@app.route('/api/slack/test', methods=['POST'])
@login_required
def api_slack_test():
    """Send a test message to the configured Slack channel."""
    if not HAS_REQUESTS:
        return jsonify({'ok': False, 'error': 'requests library not installed'}), 500
    cfg = read_config()
    token = cfg.get('slack_bot_token', '').strip()
    channel = cfg.get('slack_channel_id', '').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'Slack Bot Token not configured'}), 400
    if not channel:
        return jsonify({'ok': False, 'error': 'Slack Channel ID not configured'}), 400
    try:
        resp = _requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'channel': channel, 'text': '✅ Litmus Signage connected successfully! Upload a PPT with a comment like `schedule: 14:00-14:30` to auto-create a playlist.'},
            timeout=10,
        )
        body = resp.json()
        if body.get('ok'):
            return jsonify({'ok': True, 'message': 'Test message sent to Slack channel!'})
        return jsonify({'ok': False, 'error': body.get('error', 'unknown Slack error')})
    except Exception as e:
        logger.exception('Slack test connection failed')
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/webhooks/slack', methods=['POST'])
def webhook_slack():
    """Receive Slack Events API (file_shared). No login_required — Slack calls this."""
    raw_body = request.get_data()

    # 1. URL verification challenge (first-time Slack app setup)
    try:
        body = json.loads(raw_body)
    except Exception:
        return jsonify({'error': 'invalid JSON'}), 400

    if body.get('type') == 'url_verification':
        return body.get('challenge', ''), 200, {'Content-Type': 'text/plain'}

    # 2. Verify HMAC signature
    cfg = read_config()
    signing_secret = cfg.get('slack_signing_secret', '').strip()
    if signing_secret:
        ts = request.headers.get('X-Slack-Request-Timestamp', '')
        sig = request.headers.get('X-Slack-Signature', '')
        if not ts or not sig:
            return jsonify({'error': 'missing signature headers'}), 400
        if abs(time.time() - int(ts)) > 300:
            return jsonify({'error': 'request too old'}), 401
        basestring = f'v0:{ts}:{raw_body.decode()}'
        computed = 'v0=' + _hmac.new(signing_secret.encode(), basestring.encode(), 'sha256').hexdigest()
        if not _hmac.compare_digest(computed, sig):
            return jsonify({'error': 'invalid signature'}), 401

    # 3. Queue background task for file_shared events
    event = body.get('event', {})
    if event.get('type') == 'file_shared':
        task = {
            'file_id': event.get('file_id'),
            'user_id': event.get('user_id'),
            'channel_id': event.get('channel_id'),
            'ts': event.get('ts'),
        }
        try:
            _slack_task_queue.put_nowait(task)
        except queue.Full:
            logger.warning('Slack task queue full — dropping event')

    return jsonify({'ok': True}), 200


# --- Slack background helpers ---

def _slack_download_file(file_obj: dict, token: str) -> Path:
    """Download a Slack file to local storage, returns saved path."""
    filename = file_obj['name']
    ext = Path(filename).suffix.lower()
    if ext in {'.pptx', '.ppt', '.pdf'}:
        subdir = 'presentations'
    elif ext in {'.mp4', '.avi', '.mov', '.mkv', '.webm'}:
        subdir = 'videos'
    else:
        raise ValueError(f'Unsupported file type: {ext}')
    save_path = Path.home() / 'signage' / 'content' / subdir / filename
    save_path.parent.mkdir(parents=True, exist_ok=True)
    url = file_obj.get('url_private_download') or file_obj.get('url_private', '')
    resp = _requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=120)
    resp.raise_for_status()
    save_path.write_bytes(resp.content)
    logger.info(f'Slack file saved → {save_path}')
    return save_path


def _slack_parse_schedule(text: str) -> dict:
    """Extract HH:MM-HH:MM schedule from free text. Returns {'valid', 'start_time', 'end_time'}."""
    m = re.search(r'schedule[:\s]+(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})', text, re.IGNORECASE)
    if not m:
        return {'valid': False}
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 <= 23 and 0 <= m1 <= 59 and 0 <= h2 <= 23 and 0 <= m2 <= 59):
        return {'valid': False}
    return {'valid': True, 'start_time': f'{h1:02d}:{m1:02d}', 'end_time': f'{h2:02d}:{m2:02d}'}


def _slack_send_dm(token: str, user_id: str, text: str):
    """Send a direct message to a Slack user."""
    try:
        _requests.post(
            'https://slack.com/api/chat.postMessage',
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            json={'channel': user_id, 'text': text},
            timeout=10,
        )
    except Exception:
        logger.exception('Failed to send Slack DM')


def _process_ingest_task(filename: str, save_path: Path, sched: dict):
    """Shared logic: convert PPT + create scheduled playlist. Called by worker for both sources."""
    playlist_name = f'Slack: {Path(filename).stem}'
    logger.info(f'Converting {filename}...')
    convert_presentation_to_slides(save_path)
    ok, playlist = create_playlist(
        name=playlist_name,
        items=[{
            'name': filename,
            'repeats': 1,
            'schedule': [{
                'recurrence': 'daily',
                'start_time': sched['start_time'],
                'end_time': sched['end_time'],
            }],
        }],
    )
    if not ok:
        raise RuntimeError('create_playlist returned not-ok')
    update_playlist(playlist['id'], scheduler_enabled=True, scheduler_default={'name': filename})
    logger.info(f"Created playlist '{playlist_name}' scheduled {sched['start_time']}–{sched['end_time']}")
    return playlist_name


def _slack_worker():
    """Background thread: handles both Slack webhook tasks and Power Automate ingest tasks."""
    logger.info('Slack/ingest worker thread started')
    while True:
        try:
            task = _slack_task_queue.get(timeout=5)
        except queue.Empty:
            continue
        try:
            event_type = task.get('event_type', 'file_shared')

            # --- Power Automate path: file already saved locally ---
            if event_type == 'power_automate':
                save_path = Path(task['local_path'])
                filename = task['filename']
                sched = task['schedule']
                playlist_name = _process_ingest_task(filename, save_path, sched)
                logger.info(f'Power Automate ingest complete: {playlist_name}')
                _slack_task_queue.task_done()
                continue

            # --- Slack webhook path: download file from Slack API ---
            cfg = read_config()
            token = cfg.get('slack_bot_token', '').strip()
            if not token or not HAS_REQUESTS:
                logger.warning('Slack bot token not configured — skipping file_shared task')
                _slack_task_queue.task_done()
                continue

            file_id = task['file_id']
            user_id = task.get('user_id', '')

            r = _requests.get(
                'https://slack.com/api/files.info',
                headers={'Authorization': f'Bearer {token}'},
                params={'file': file_id},
                timeout=15,
            )
            info = r.json()
            if not info.get('ok'):
                raise RuntimeError(f"files.info error: {info.get('error')}")

            file_obj = info['file']
            filename = file_obj['name']

            initial_comment = file_obj.get('initial_comment') or {}
            comment_text = initial_comment.get('comment', '') if isinstance(initial_comment, dict) else str(initial_comment)
            search_text = ' '.join([file_obj.get('title', ''), comment_text, filename])
            sched = _slack_parse_schedule(search_text)

            if not sched['valid']:
                _slack_send_dm(token, user_id,
                    f"⚠️ *{filename}* uploaded but no schedule found.\n"
                    "Add a comment like `schedule: 14:00-14:30` to auto-schedule.")
                _slack_task_queue.task_done()
                continue

            save_path = _slack_download_file(file_obj, token)
            playlist_name = _process_ingest_task(filename, save_path, sched)
            _slack_send_dm(token, user_id,
                f"✅ *{playlist_name}* created!\n"
                f"📅 Scheduled *{sched['start_time']} – {sched['end_time']}* daily\n"
                "🎬 Will play automatically at the scheduled time.")

        except Exception:
            logger.exception('Worker task failed')
            try:
                cfg2 = read_config()
                tok2 = cfg2.get('slack_bot_token', '').strip()
                uid2 = task.get('user_id', '')
                if tok2 and uid2:
                    _slack_send_dm(tok2, uid2, '❌ Something went wrong. Check the dashboard logs.')
            except Exception:
                pass
        finally:
            _slack_task_queue.task_done()


@app.errorhandler(413)
def too_large(e):
    flash('File too large! Maximum: 2GB', 'error')
    return redirect(url_for('dashboard'))

@app.errorhandler(500)
def internal_error(e):
    flash('Internal error. Check logs.', 'error')
    logger.error(f"Internal error: {e}", exc_info=True)
    return redirect(url_for('dashboard'))

if __name__ == '__main__':
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    PRESENTATIONS_DIR.mkdir(parents=True, exist_ok=True)

    # Start Slack background worker (daemon so it exits when Flask exits)
    _t = threading.Thread(target=_slack_worker, daemon=True, name='slack-worker')
    _t.start()

    logger.info("=" * 60)
    logger.info("Starting Dashboard...")
    logger.info(f"Max upload: 2GB")
    logger.info(f"System stats (psutil): {'✓ Available' if HAS_PSUTIL else '✗ Not installed'}")
    logger.info(f"Slack worker: started (thread={_t.name})")
    logger.info("=" * 60)

    app.run(host='0.0.0.0', port=5000, debug=False)
