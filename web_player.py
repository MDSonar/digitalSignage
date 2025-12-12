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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CONFIG_FILE = Path.home() / 'signage' / 'config.json'

PLAYLIST_JSON = Path.home() / 'signage' / 'playlist.json'

VIDEOS_DIR = Path.home() / 'signage' / 'content' / 'videos'
PRESENTATIONS_DIR = Path.home() / 'signage' / 'content' / 'presentations'
SLIDES_CACHE_DIR = Path.home() / 'signage' / 'cache' / 'slides'
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

def get_playlist():
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
    try:
        if PLAYLIST_JSON.exists():
            data = json.loads(PLAYLIST_JSON.read_text())
            if isinstance(data, list):
                # normalize to list of objects with repeats
                for entry in data:
                    if isinstance(entry, str):
                        selected.append({'name': entry, 'repeats': 1})
                    elif isinstance(entry, dict):
                        name = entry.get('name') or entry.get('filename')
                        try:
                            repeats = int(entry.get('repeats', 1))
                        except Exception:
                            repeats = 1
                        if name:
                            selected.append({'name': name, 'repeats': max(1, repeats)})

    except Exception:
        logger.exception('Failed to read playlist.json')

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

def get_playlist_hash():
    playlist = get_playlist()
    playlist_str = json.dumps(playlist, sort_keys=True)
    return hashlib.md5(playlist_str.encode()).hexdigest()

# --- Synchronization helpers (non-breaking additions) ---
# These are used by the optional `/api/playlist-sync` endpoint.
# They are added in a way that does not change the existing `/api/playlist` behavior.

# Global playlist sync state (kept separate so original API is unchanged)
_playlist_cache = None
_playlist_hash_cache = None
_playlist_start_time = None

def get_video_duration(video_path):
    """Get video duration in seconds (placeholder).
    For a production system integrate ffprobe or mediainfo. Using
    a sensible default prevents the sync endpoint from breaking.
    """
    return 30  # default estimate in seconds

def build_playlist_with_durations():
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

    for video in get_video_files():
        if mode in ('both', 'video'):
            duration = get_video_duration(video)
            playlist.append({
                'type': 'video',
                'url': f'/content/videos/{video.name}',
                'name': video.name,
                'duration': duration
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

@app.route('/api/playlist')
def api_playlist():
    return jsonify({
        'playlist': get_playlist(),
        'hash': get_playlist_hash()
    })


@app.route('/api/playlist-sync')
def api_playlist_sync():
    """Optional synchronized playlist endpoint.
    Returns playlist with durations and server timing so clients can
    align playback. This does not replace the original `/api/playlist`.
    """
    global _playlist_cache, _playlist_hash_cache, _playlist_start_time

    playlist = build_playlist_with_durations()
    playlist_hash = get_playlist_hash_from(playlist)

    # If playlist changed, reset start time
    if playlist_hash != _playlist_hash_cache:
        _playlist_cache = playlist
        _playlist_hash_cache = playlist_hash
        _playlist_start_time = time.time()
        logger.info(f"Playlist updated (sync): {len(playlist)} items")

    if _playlist_start_time and playlist:
        elapsed = time.time() - _playlist_start_time
        current_index, item_elapsed = get_current_item_index(playlist, elapsed)
    else:
        current_index = 0
        item_elapsed = 0

    return jsonify({
        'playlist': playlist,
        'hash': playlist_hash,
        'serverTime': time.time(),
        'playlistStartTime': _playlist_start_time or time.time(),
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

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting Web Player for Network TVs")
    logger.info("TVs should open: http://<pi-ip>:8080")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=8080, debug=False)
