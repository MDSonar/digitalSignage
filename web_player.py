#!/usr/bin/env python3
"""
Digital Signage Web Player
- Serves content as web app for network TVs
- Multiple TVs can connect simultaneously
- Auto-refresh when content changes
"""

from flask import Flask, render_template, send_from_directory, jsonify, request
from pathlib import Path
from datetime import datetime
import logging
import json
import hashlib
import time
import os
import uuid as _uuid_mod
from utils import read_playlists, get_playlist_by_id, get_active_playlist_id, read_clients, write_clients
from scheduler_engine import SchedulerEngine
from ticker_engine import TickerEngine
from environment_service import EnvironmentService

_scheduler = SchedulerEngine()
_ticker = TickerEngine()
_env = EnvironmentService()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

CONFIG_FILE = Path.home() / 'signage' / 'config.json'
SCHEDULES_FILE = Path.home() / 'signage' / 'schedules.json'


def read_schedules():
    if SCHEDULES_FILE.exists():
        try:
            return json.loads(SCHEDULES_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'schedules': []}

# Legacy default playlist file; multi-playlist uses config 'activePlaylist'
PLAYLIST_JSON = Path.home() / 'signage' / 'playlist.json'
PLAYLISTS_DIR = Path.home() / 'signage' / 'playlists'

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
    """Return (playlist, scheduler_enabled, scheduler_default, raw_items).

    Always returns ALL items regardless of schedule — schedule evaluation is
    done client-side (web_player.html) so the display device's local clock is
    used, not the server clock (which may be in a different timezone).
    """
    playlist = []
    scheduler_enabled = False
    scheduler_default = None  # raw item dict or None
    raw_items = []            # raw items from playlists.json (include schedule field)

    try:
        mode = 'both'
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text())
            mode = cfg.get('mode', 'both')
    except Exception:
        mode = 'both'

    selected = []
    used_explicit_source = False
    try:
        pid = selected_id or get_active_playlist_id()
        data_list = None
        if pid:
            pl = get_playlist_by_id(pid)
            if pl:
                scheduler_enabled = bool(pl.get('scheduler_enabled'))
                scheduler_default = pl.get('scheduler_default')
                data_list = pl.get('items', [])
                raw_items = list(data_list)   # keep originals for client
                used_explicit_source = True
        if data_list is None:
            pl_path = get_active_playlist_path()
            if pl_path.exists():
                data_list = json.loads(pl_path.read_text())
                used_explicit_source = True
        if isinstance(data_list, list):
            for entry in data_list:
                if isinstance(entry, str):
                    selected.append({'name': entry, 'repeats': 1, 'schedule': None})
                elif isinstance(entry, dict):
                    name = entry.get('name') or entry.get('filename') or entry.get('url')
                    try:
                        repeats = int(entry.get('repeats', 1))
                    except Exception:
                        repeats = 1
                    if name:
                        selected.append({
                            'name': name,
                            'repeats': max(1, repeats),
                            'schedule': entry.get('schedule'),  # preserve for client eval
                        })
    except Exception:
        logger.exception('Failed to read playlist')

    video_files = {v.name: v for v in get_video_files()}
    if selected:
        logger.info(f"Using JSON playlist with {len(selected)} entries (web player)")
        for entry in selected:
            name    = entry.get('name')
            repeats = entry.get('repeats', 1)
            if not name:
                continue

            if "youtube.com" in name or "youtu.be" in name:
                playlist.append({"type": "youtube", "url": name, "name": name,
                                  "source_name": name})
                continue

            if name in video_files and mode in ('both', 'video'):
                for _ in range(repeats):
                    playlist.append({
                        'type': 'video',
                        'url': f'/content/videos/{video_files[name].name}',
                        'name': video_files[name].name,
                        'source_name': name,   # original item name for schedule matching
                    })
            else:
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
                                'duration': SLIDE_DURATION,
                                'source_name': name,   # original presentation filename
                            })
    else:
        if not used_explicit_source:
            for video in get_video_files():
                if mode in ('both', 'video'):
                    playlist.append({'type': 'video',
                                     'url': f'/content/videos/{video.name}',
                                     'name': video.name, 'source_name': video.name})
            for slide in get_slide_files():
                if mode in ('both', 'presentation'):
                    relative_path = slide.relative_to(SLIDES_CACHE_DIR)
                    playlist.append({'type': 'image',
                                     'url': f'/content/slides/{relative_path.as_posix()}',
                                     'name': slide.name, 'duration': SLIDE_DURATION,
                                     'source_name': relative_path.parts[0] if relative_path.parts else slide.name})

    return playlist, scheduler_enabled, scheduler_default, raw_items

def get_playlist_hash(selected_id: str = None):
    playlist, _, _, _ = get_playlist(selected_id)
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
    base_list, _, _, _ = get_playlist(selected_id)
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
    items, scheduler_enabled, scheduler_default, raw_items = get_playlist(playlist_id)
    items_hash = hashlib.md5(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return jsonify({
        'playlist': items,
        'hash': items_hash,
        'scheduler_enabled': scheduler_enabled,
        'scheduler_default': scheduler_default,
        'raw_items': raw_items,
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

# ---------------------------------------------------------------------------
# Browser Device Registration — lets any browser register as a managed display
# ---------------------------------------------------------------------------

@app.route('/api/device/register', methods=['POST'])
def api_device_register():
    """Register (or re-register) a browser display. Called on first visit."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or 'Browser Display').strip()[:60]
    device_id = (data.get('device_id') or '').strip()
    client_ip = (data.get('client_ip') or '').strip()
    ip = client_ip or request.remote_addr

    if not device_id:
        device_id = str(_uuid_mod.uuid4())

    store = read_clients()
    clients = store.get('clients', [])
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    existing = next((c for c in clients if c.get('id') == device_id), None)
    if existing:
        existing['name'] = name
        existing['ip'] = ip
        existing['last_seen'] = now
        existing['client_type'] = 'browser'
    else:
        clients.append({
            'id': device_id,
            'mac': device_id,          # UUID serves as MAC-equivalent for browsers
            'name': name,
            'ip': ip,
            'hostname': 'browser',
            'client_type': 'browser',
            'agent_version': 'web',
            'assigned_playlist_id': None,
            'pending_command': None,
            'registered_at': now,
            'last_seen': now,
            'stats': {},
        })

    store['clients'] = clients
    write_clients(store)

    assigned_pid = existing.get('assigned_playlist_id') if existing else None
    return jsonify({'ok': True, 'device_id': device_id, 'name': name,
                    'assigned_playlist_id': assigned_pid})


@app.route('/api/device/heartbeat', methods=['POST'])
def api_device_heartbeat():
    """Browser sends heartbeat every 30 s; response carries latest assignment."""
    data = request.get_json(silent=True) or {}
    device_id = (data.get('device_id') or '').strip()
    if not device_id:
        return jsonify({'ok': False, 'error': 'device_id required'}), 400

    store = read_clients()
    client = next((c for c in store.get('clients', []) if c.get('id') == device_id), None)
    if not client:
        return jsonify({'ok': False, 'reregister': True})

    client['last_seen'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    client_ip = (data.get('client_ip') or '').strip()
    client['ip'] = client_ip or request.remote_addr
    write_clients(store)

    return jsonify({'ok': True, 'assigned_playlist_id': client.get('assigned_playlist_id')})




@app.route('/api/device/playlist')
def api_device_playlist():
    """Return the playlist currently assigned to a browser device."""
    device_id = request.args.get('device_id', '').strip()
    if not device_id:
        return jsonify({'error': 'device_id required'}), 400

    store = read_clients()
    client = next((c for c in store.get('clients', []) if c.get('id') == device_id), None)
    if not client:
        return jsonify({'error': 'unknown device', 'reregister': True}), 404

    pid = client.get('assigned_playlist_id')
    sch_id = client.get('assigned_schedule_id')

    if not pid and not sch_id:
        return jsonify({'assigned': False, 'playlist': [], 'hash': '',
                        'scheduler_enabled': False, 'scheduler_default': None, 'raw_items': []})

    if sch_id:
        now = datetime.now()
        store = read_schedules()
        schedule = next((s for s in store.get('schedules', []) if s['id'] == sch_id), None)
        schedule_name = schedule['name'] if schedule else 'Schedule'

        matching_block = None
        next_block = None

        if schedule:
            blocks = schedule.get('blocks', [])
            matching_block = _scheduler.get_current_block(blocks, now)
            if matching_block is None:
                next_block = _scheduler.get_next_block_today(blocks, now)

        if matching_block:
            content_type = matching_block.get('content_type', 'playlist')
            if content_type == 'playlist' and matching_block.get('playlist_id'):
                items, sched_en, sched_def, raw = get_playlist(matching_block['playlist_id'])
                h = hashlib.md5(json.dumps(items, sort_keys=True).encode()).hexdigest()
                return jsonify({'assigned': True, 'assigned_playlist_id': matching_block['playlist_id'],
                                'playlist': items, 'hash': h, 'scheduler_enabled': False,
                                'scheduler_default': None, 'raw_items': raw,
                                'schedule_mode': True, 'schedule_name': schedule_name,
                                'block_title': matching_block.get('title', '')})
            elif content_type == 'media' and matching_block.get('media_name'):
                media_name = matching_block['media_name']
                ext = Path(media_name).suffix.lower()
                if ext in ['.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v', '.wmv', '.flv']:
                    url = f'/video/{media_name}'
                    item = {'name': media_name, 'type': 'video', 'url': url, 'repeats': 1}
                else:
                    url = f'/slides/{media_name}'
                    item = {'name': media_name, 'type': 'slide', 'url': url, 'repeats': 1}
                h = hashlib.md5(json.dumps([item], sort_keys=True).encode()).hexdigest()
                return jsonify({'assigned': True, 'playlist': [item], 'hash': h,
                                'scheduler_enabled': False, 'scheduler_default': None, 'raw_items': [],
                                'schedule_mode': True, 'schedule_name': schedule_name})

        return jsonify({'assigned': False, 'playlist': [], 'hash': '',
                        'schedule_mode': True, 'schedule_name': schedule_name,
                        'next_block': next_block})

    items, sched_en, sched_def, raw = get_playlist(pid)
    h = hashlib.md5(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return jsonify({'assigned': True, 'assigned_playlist_id': pid, 'playlist': items, 'hash': h,
                    'scheduler_enabled': sched_en, 'scheduler_default': sched_def,
                    'raw_items': raw})


@app.route('/api/news/active')
def api_news_active():
    """Public endpoint — returns active ticker messages for web players."""
    try:
        return jsonify({'ok': True, 'messages': _ticker.get_all_messages()})
    except Exception:
        logger.exception('Ticker fetch failed')
        return jsonify({'ok': True, 'messages': []})


_TICKER_DEFAULTS = {
    'ticker_enabled':    False,
    'ticker_position':   'bottom',
    'ticker_speed':      'normal',
    'ticker_height':     'medium',
    'ticker_bg_color':   '#1e293b',
    'ticker_text_color': '#ffffff',
    'ticker_show_live':  True,
    'auto_fullscreen':   True,
}


@app.route('/api/clients/<cid>/ticker')
def api_ticker_get(cid):
    """Public endpoint — returns per-display ticker settings for web players."""
    store = read_clients()
    client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
    if not client:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    ticker = {**_TICKER_DEFAULTS, **(client.get('ticker_settings') or {})}
    return jsonify({'ok': True, 'ticker': ticker})


# ── Idle Cards ────────────────────────────────────────────────

_IDLE_DEFAULTS = {
    'idle_mode':       'clock',
    'card_transition': 'fade',
    'card_duration':   10,
}

_IDLE_CARDS_FILE = Path.home() / 'signage' / 'idle_cards.json'


def _read_idle_cards() -> dict:
    if _IDLE_CARDS_FILE.exists():
        try:
            import json as _json
            return _json.loads(_IDLE_CARDS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'cards': []}


@app.route('/api/idle-cards')
def api_idle_cards():
    """Public endpoint — returns enabled idle cards ordered by priority."""
    try:
        store = _read_idle_cards()
        cards = [c for c in store.get('cards', []) if c.get('enabled', True)]
        cards.sort(key=lambda c: (-int(c.get('priority', 0)), c.get('created_at', '')))
        return jsonify({'ok': True, 'cards': cards})
    except Exception:
        logger.exception('Idle cards fetch failed')
        return jsonify({'ok': True, 'cards': []})


@app.route('/api/environment')
def api_environment():
    """Returns cached location + weather + AQI for the environment widget."""
    try:
        return jsonify(_env.get())
    except Exception:
        logger.exception('Environment service error')
        return jsonify({'ok': False, 'reason': 'error'}), 500


@app.route('/api/clients/<cid>/idle')
def api_idle_settings_get(cid):
    """Public endpoint — returns per-display idle screen settings."""
    store = read_clients()
    client = next((c for c in store.get('clients', []) if c['id'] == cid), None)
    if not client:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    settings = {**_IDLE_DEFAULTS, **(client.get('idle_settings') or {})}
    return jsonify({'ok': True, 'idle': settings})


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Starting Web Player for Network TVs")
    logger.info("TVs should open: http://<pi-ip>:8080")
    logger.info("=" * 60)

    app.run(host='0.0.0.0', port=8080, debug=False)
