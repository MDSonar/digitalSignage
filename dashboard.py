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

            # Note: Presentation conversion to slides is not performed in this setup
            # Web player uses native video playback instead of slide conversion

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
    """Toggle an item (video/presentation/youtube) in/out of the playlist"""

    # decode URL encoded filename/url (client sends encodeURIComponent(...))
    filename = unquote(filename)
    
    logger.info(f"Toggle playlist request - type: {content_type}, filename: {filename}")

    # Allow YouTube links
    if content_type not in ('video', 'presentation', 'youtube'):
        logger.error(f"Invalid content type: {content_type}")
        return jsonify({'ok': False, 'error': 'invalid content type'}), 400

    try:
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
        raw = read_playlist()
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

@app.route('/upload/<content_type>', methods=['POST'])
@login_required
def upload_file(content_type):
    start_time = time.time()
    
    try:
        logger.info(f"Upload started for {content_type}")
        
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('dashboard'))
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('dashboard'))
        
        if content_type == 'video':
            target_dir = VIDEOS_DIR
            allowed_ext = ALLOWED_VIDEO_EXTENSIONS
        elif content_type == 'presentation':
            target_dir = PRESENTATIONS_DIR
            allowed_ext = ALLOWED_PPT_EXTENSIONS
        else:
            flash('Invalid content type', 'error')
            return redirect(url_for('dashboard'))
        
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_ext:
            flash(f'Invalid file type. Allowed: {", ".join(allowed_ext)}', 'error')
            return redirect(url_for('dashboard'))
        
        filename = secure_filename(file.filename)
        filepath = target_dir / filename
        
        # NEW: If presentation exists, delete old cache first
        if content_type == 'presentation' and filepath.exists():
            cache_path = CACHE_DIR / filepath.stem
            if cache_path.exists():
                shutil.rmtree(cache_path)
                logger.info(f"Deleted old cache for: {filename}")
        
        target_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Saving {filename}...")
        file.save(str(filepath))
        
        if filepath.exists():
            elapsed = time.time() - start_time
            file_size = get_file_size(filepath)
            flash(f'✓ Uploaded: {filename} ({file_size}) in {elapsed:.1f}s', 'success')
            logger.info(f"Upload complete: {filename} ({file_size}) in {elapsed:.1f}s")
            # Note: Presentation conversion to slides is not performed in this setup
            # Web player uses native video playback instead of slide conversion
        else:
            flash('Upload failed: File not saved', 'error')
            logger.error(f"File not found after save: {filepath}")
        
    except Exception as e:
        elapsed = time.time() - start_time
        flash(f'Upload failed after {elapsed:.1f}s: {str(e)}', 'error')
        logger.error(f"Upload error after {elapsed:.1f}s: {e}", exc_info=True)
    
    return redirect(url_for('dashboard'))

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
    
    logger.info("=" * 60)
    logger.info("Starting Dashboard...")
    logger.info(f"Max upload: 2GB")
    logger.info(f"System stats (psutil): {'✓ Available' if HAS_PSUTIL else '✗ Not installed'}")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=5000, debug=False)
