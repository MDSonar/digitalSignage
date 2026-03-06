#!/usr/bin/env python3
"""
Digital Signage Web Player
- Serves content as web app for network TVs
- Multiple TVs can connect simultaneously
- Auto-refresh when content changes
"""

from flask import Flask, render_template, send_from_directory, jsonify
from pathlib import Path
import logging
import json
import hashlib
import time
import os
from utils import read_playlists, get_playlist_by_id, get_active_playlist_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CONFIG_FILE = Path.home() / 'signage' / 'config.json'

# Legacy default playlist file; multi-playlist uses config 'activePlaylist'
PLAYLIST_JSON = Path.home() / 'signage' / 'playlist.json'
PLAYLISTS_DIR = Path.home() / 'signage' / 'playlists'

VIDEOS_DIR = Path.home() / 'signage' / 'content' / 'videos'
PRESENTATIONS_DIR = Path.home() / 'signage' / 'content' / 'presentations'
SLIDES_CACHE_DIR = Path.home() / 'signage' / 'cache' / 'slides'
QUOTES_FILE = Path.home() / 'signage' / 'quotes.json'
QUOTE_BACKGROUNDS_DIR = Path.home() / 'signage' / 'content' / 'quote_backgrounds'
VIDEO_FORMATS = ['.mp4', '.avi', '.mov', '.mkv', '.webm']
SLIDE_DURATION = 10

def get_video_files():
    files = []
    if VIDEOS_DIR.exists():
        for ext in VIDEO_FORMATS:
            files.extend(VIDEOS_DIR.glob(f"*{ext}"))
            files.extend(VIDEOS_DIR.glob(f"*{ext.upper()}"))
    return sorted(files)

def get_slide_files():
    slides = []
    if SLIDES_CACHE_DIR.exists():
        for presentation_dir in sorted(SLIDES_CACHE_DIR.iterdir()):
            if presentation_dir.is_dir():
                slides.extend(sorted(presentation_dir.glob("slide_*.png")))
    return slides

def get_active_playlist_path():
    """Return Path for active playlist.
    Backward compatible: falls back to legacy playlist.json.
    """
    try:
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text())
            active = cfg.get('activePlaylist')
            if active:
                p = PLAYLISTS_DIR / f"{Path(active).stem}.json"
                if p.exists():
                    return p
    except Exception:
        pass
    return PLAYLIST_JSON

def get_playlist(selected_id: str = None):
    playlist = []
    
    # read mode config (both|video|presentation)
    try:
        mode = 'both'
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text())
            mode = cfg.get('mode', 'both')
    except Exception:
        mode = 'both'

    # If a JSON playlist exists, honor it (selected filenames). It should be a list of filenames
    # (videos or presentation filenames). Presentations are expanded to their cached slides.
    selected = []
    used_explicit_source = False
    try:
        # Prefer new playlists.json if present
        store = read_playlists()
        pid = selected_id or get_active_playlist_id()
        data_list = None
        if pid:
            pl = get_playlist_by_id(pid)
            if pl:
                data_list = pl.get('items', [])
                used_explicit_source = True
        if data_list is None:
            # Fallback to legacy file
            pl_path = get_active_playlist_path()
            if pl_path.exists():
                data_list = json.loads(pl_path.read_text())
                used_explicit_source = True
        if isinstance(data_list, list):
            for entry in data_list:
                if isinstance(entry, str):
                    selected.append({'name': entry, 'repeats': 1})
                elif isinstance(entry, dict):
                    name = entry.get('name') or entry.get('filename') or entry.get('url')
                    try:
                        repeats = int(entry.get('repeats', 1))
                    except Exception:
                        repeats = 1
                    if name:
                        selected.append({'name': name, 'repeats': max(1, repeats)})
    except Exception:
        logger.exception('Failed to read playlist')

    # Build maps
    video_files = {v.name: v for v in get_video_files()}
    # slides are stored under SLIDES_CACHE_DIR/<presentation_stem>/slide_*.png
    if selected:
        logger.info(f"Using JSON playlist with {len(selected)} entries (web player)")
        for entry in selected:
            name = entry.get('name')
            repeats = entry.get('repeats', 1)
            
            # Check if name is valid before string operations
            if not name:
                continue
            
            # Check for YouTube URLs
            if "youtube.com" in name or "youtu.be" in name:
                playlist.append({
                    "type": "youtube",
                    "url": name,
                    "name": name
                })
                continue
            if name in video_files and mode in ('both', 'video'):
                for _ in range(repeats):
                    playlist.append({
                        'type': 'video',
                        'url': f'/content/videos/{video_files[name].name}',
                        'name': video_files[name].name
                    })
            else:
                # treat as presentation filename; expand to slides by stem
                stem = Path(name).stem
                pres_dir = SLIDES_CACHE_DIR / stem
                if pres_dir.exists() and pres_dir.is_dir() and mode in ('both', 'presentation'):
                    for _ in range(repeats):
                        for slide in sorted(pres_dir.glob('slide_*.png')):
                            rel = slide.relative_to(SLIDES_CACHE_DIR)
                            playlist.append({
                                'type': 'image',
                                'url': f'/content/slides/{rel.as_posix()}',
                                'name': slide.name,
                                'duration': SLIDE_DURATION
                            })
    else:
        # Only auto-scan directories if we did not use playlists.json or legacy file
        if not used_explicit_source:
            for video in get_video_files():
                if mode in ('both', 'video'):
                    playlist.append({
                    'type': 'video',
                    'url': f'/content/videos/{video.name}',
                    'name': video.name
                    })
            
            for slide in get_slide_files():
                if mode in ('both', 'presentation'):
                    relative_path = slide.relative_to(SLIDES_CACHE_DIR)
                    playlist.append({
                    'type': 'image',
                    'url': f'/content/slides/{relative_path.as_posix()}',
                    'name': slide.name,
                    'duration': SLIDE_DURATION
                    })
    
    return playlist

def get_playlist_hash(selected_id: str = None):
    playlist = get_playlist(selected_id)
    playlist_str = json.dumps(playlist, sort_keys=True)
    return hashlib.md5(playlist_str.encode()).hexdigest()

# --- Synchronization helpers (non-breaking additions) ---
# These are used by the optional `/api/playlist-sync` endpoint.
# They are added in a way that does not change the existing `/api/playlist` behavior.

# Global playlist sync state (kept separate so original API is unchanged)
_sync_states = {}

def get_video_duration(video_path):
    """Get video duration in seconds (placeholder).
    For a production system integrate ffprobe or mediainfo. Using
    a sensible default prevents the sync endpoint from breaking.
    """
    return 30  # default estimate in seconds

def build_playlist_with_durations(selected_id: str = None):
    """Build playlist including per-item durations for sync playback."""
    playlist = []

    # Respect configured mode
    try:
        mode = 'both'
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text())
            mode = cfg.get('mode', 'both')
    except Exception:
        mode = 'both'

    # Use the same source as get_playlist() to respect selected_id
    base_list = get_playlist(selected_id)
    for item in base_list:
        if item.get('type') == 'video':
            # derive filename from URL
            name = item.get('name')
            duration = get_video_duration(VIDEOS_DIR / name)
            p = dict(item)
            p['duration'] = duration
            playlist.append(p)
        else:
            p = dict(item)
            p['duration'] = item.get('duration', SLIDE_DURATION)
            playlist.append(p)

    return playlist

def get_playlist_hash_from(playlist):
    playlist_str = json.dumps(playlist, sort_keys=True)
    return hashlib.md5(playlist_str.encode()).hexdigest()

def calculate_total_duration(playlist):
    return sum(item.get('duration', 0) for item in playlist)

def get_current_item_index(playlist, elapsed_time):
    total_duration = calculate_total_duration(playlist)
    if total_duration == 0:
        return 0, 0

    position_in_loop = elapsed_time % total_duration
    cumulative = 0
    for idx, item in enumerate(playlist):
        if cumulative + item.get('duration', 0) > position_in_loop:
            item_elapsed = position_in_loop - cumulative
            return idx, item_elapsed
        cumulative += item.get('duration', 0)

    return 0, 0

@app.route('/')
def player():
    return render_template('web_player.html')

@app.route('/api/playlists')
def api_playlists():
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

@app.route('/api/playlist')
def api_playlist():
    playlist_id = None
    try:
        playlist_id = os.environ.get('PLAYLIST_ID')
    except Exception:
        pass
    try:
        from flask import request
        playlist_id = request.args.get('playlist_id') or playlist_id
    except Exception:
        pass
    return jsonify({
        'playlist': get_playlist(playlist_id),
        'hash': get_playlist_hash(playlist_id)
    })


@app.route('/api/playlist-sync')
def api_playlist_sync():
    """Optional synchronized playlist endpoint.
    Returns playlist with durations and server timing so clients can
    align playback. This does not replace the original `/api/playlist`.
    """
    try:
        from flask import request
        selected_id = request.args.get('playlist_id')
    except Exception:
        selected_id = None

    state = _sync_states.get(selected_id or '__default__', {
        'playlist_cache': None,
        'playlist_hash': None,
        'start_time': None
    })

    playlist = build_playlist_with_durations(selected_id)
    playlist_hash = get_playlist_hash_from(playlist)

    if playlist_hash != state['playlist_hash']:
        state['playlist_cache'] = playlist
        state['playlist_hash'] = playlist_hash
        state['start_time'] = time.time()
        _sync_states[selected_id or '__default__'] = state
        logger.info(f"Playlist updated (sync): {len(playlist)} items")

    if state['start_time'] and playlist:
        elapsed = time.time() - state['start_time']
        current_index, item_elapsed = get_current_item_index(playlist, elapsed)
    else:
        current_index = 0
        item_elapsed = 0

    return jsonify({
        'playlist': playlist,
        'hash': playlist_hash,
        'serverTime': time.time(),
        'playlistStartTime': state['start_time'] or time.time(),
        'currentIndex': current_index,
        'itemElapsed': item_elapsed
    })


@app.route('/api/command')
def api_command():
    """Return any pending command intended for web players and clear it."""
    try:
        cmdfile = CONFIG_FILE.parent.joinpath('commands', 'web.json')
        if cmdfile.exists():
            try:
                data = json.loads(cmdfile.read_text())
            except Exception:
                data = {}
            # Do NOT delete the command file here — keep it for other connected clients.
            # Clients will deduplicate using the timestamp (ts) value.
            return jsonify({'ok': True, 'command': data})
    except Exception:
        logger.exception('Failed reading command file')
    return jsonify({'ok': True, 'command': None})

@app.route('/content/videos/<path:filename>')
def serve_video(filename):
    return send_from_directory(VIDEOS_DIR, filename)

@app.route('/content/slides/<path:filename>')
def serve_slide(filename):
    return send_from_directory(SLIDES_CACHE_DIR, filename)

@app.route('/content/quote_backgrounds/<path:filename>')
def serve_quote_background(filename):
    return send_from_directory(QUOTE_BACKGROUNDS_DIR, filename)

@app.route('/api/quotes')
def api_quotes():
    """Return quotes data for web player"""
    try:
        if QUOTES_FILE.exists():
            return jsonify(json.loads(QUOTES_FILE.read_text()))
        return jsonify({'quotes': [], 'settings': {'duration': 60, 'shuffle': False}})
    except Exception:
        logger.exception('Failed to read quotes')
        return jsonify({'quotes': [], 'settings': {'duration': 60, 'shuffle': False}})

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting Web Player for Network TVs")
    logger.info("TVs should open: http://<pi-ip>:8080")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=8080, debug=False)
