from pathlib import Path
import json
import time
import uuid
import logging
import os

# Load .env so TZ and other variables are available when running locally or
# when docker-compose env_file hasn't injected them yet.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).parent / '.env', override=False)
except Exception:
    pass

logger = logging.getLogger(__name__)

SIGNAGE_HOME = Path.home() / 'signage'
PLAYLISTS_JSON = SIGNAGE_HOME / 'playlists.json'
LEGACY_PLAYLIST_JSON = SIGNAGE_HOME / 'playlist.json'
CONFIG_JSON = SIGNAGE_HOME / 'signage_config.json'


def read_config() -> dict:
    try:
        if CONFIG_JSON.exists():
            return json.loads(CONFIG_JSON.read_text())
    except Exception:
        logger.exception('Failed to read config')
    return {}


def write_config(data: dict) -> bool:
    try:
        CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write(CONFIG_JSON, json.dumps(data))
    except Exception:
        logger.exception('Failed to write config')
        return False


def atomic_write(path: Path, data: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use a unique temp filename per call to prevent concurrent-write collisions
        # between Flask threads (e.g. two simultaneous PUT requests both writing
        # clients.json.tmp would corrupt the file and wipe registered displays).
        tmp = path.parent / f'.{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp'
        tmp.write_text(data, encoding='utf-8')
        tmp.replace(path)
        return True
    except Exception:
        logger.exception('Atomic write failed')
        return False


def ensure_playlist_store():
    """Ensure playlists.json exists; if not, try to migrate legacy list."""
    if PLAYLISTS_JSON.exists():
        return True
    try:
        if LEGACY_PLAYLIST_JSON.exists():
            raw = json.loads(LEGACY_PLAYLIST_JSON.read_text())
            if isinstance(raw, list):
                now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                data = {
                    'version': 2,
                    'playlists': [
                        {
                            'id': 'default',
                            'name': 'default',
                            'items': raw,
                            'created_at': now,
                            'updated_at': now,
                            'meta': {}
                        }
                    ],
                    'active_playlist_id': 'default',
                    'updated_at': now
                }
                return atomic_write(PLAYLISTS_JSON, json.dumps(data))
    except Exception:
        logger.exception('Failed to migrate legacy playlist')
    # If no legacy, initialize with default 'everything' playlist
    try:
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        default_pl = {
            'id': 'everything',
            'name': 'everything',
            'items': [],
            'created_at': now,
            'updated_at': now,
            'meta': {}
        }
        data = {'version': 2, 'playlists': [default_pl], 'active_playlist_id': 'everything', 'updated_at': now}
        return atomic_write(PLAYLISTS_JSON, json.dumps(data))
    except Exception:
        logger.exception('Failed to init playlist store')
        return False


def read_playlists():
    try:
        ensure_playlist_store()
        store = json.loads(PLAYLISTS_JSON.read_text())
        # Ensure at least the default 'everything' playlist always exists
        if not store.get('playlists'):
            now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            default_pl = {
                'id': 'everything',
                'name': 'everything',
                'items': [],
                'created_at': now,
                'updated_at': now,
                'meta': {}
            }
            store['playlists'] = [default_pl]
            store['active_playlist_id'] = 'everything'
            write_playlists(store)
        return store
    except Exception:
        logger.exception('Failed to read playlists.json')
        return {'version': 2, 'playlists': [], 'active_playlist_id': None, 'updated_at': None}


def write_playlists(store: dict) -> bool:
    try:
        store['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        return atomic_write(PLAYLISTS_JSON, json.dumps(store))
    except Exception:
        logger.exception('Failed to write playlists.json')
        return False


# ---------------------------------------------------------------------------
# Clients (RPi devices)
# ---------------------------------------------------------------------------

CLIENTS_JSON = SIGNAGE_HOME / 'clients.json'


def read_clients() -> dict:
    """Return clients store, creating empty store if missing."""
    try:
        if CLIENTS_JSON.exists():
            return json.loads(CLIENTS_JSON.read_text())
    except Exception:
        logger.exception('Failed to read clients.json')
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return {'clients': [], 'updated_at': now}


def write_clients(store: dict) -> bool:
    try:
        store['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        return atomic_write(CLIENTS_JSON, json.dumps(store))
    except Exception:
        logger.exception('Failed to write clients.json')
        return False


def get_playlist_by_id(pid: str):
    try:
        store = read_playlists()
        for pl in store.get('playlists', []):
            if pl.get('id') == pid:
                return pl
    except Exception:
        logger.exception('Failed get playlist by id')
    return None


def create_playlist(name: str, items=None):
    items = items or []
    try:
        store = read_playlists()
        pid = str(uuid.uuid4())
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        pl = {'id': pid, 'name': name, 'items': items, 'created_at': now, 'updated_at': now, 'meta': {}}
        store.setdefault('playlists', []).append(pl)
        if not store.get('active_playlist_id'):
            store['active_playlist_id'] = pid
        ok = write_playlists(store)
        return ok, pl
    except Exception:
        logger.exception('Failed to create playlist')
        return False, None


_UNSET = object()


def update_playlist(pid: str, name=None, items=None, scheduler_enabled=_UNSET, scheduler_default=_UNSET):
    try:
        store = read_playlists()
        changed = False
        for pl in store.get('playlists', []):
            if pl.get('id') == pid:
                if name is not None:
                    pl['name'] = name
                    changed = True
                if items is not None:
                    pl['items'] = items
                    changed = True
                if scheduler_enabled is not _UNSET:
                    pl['scheduler_enabled'] = scheduler_enabled
                    changed = True
                if scheduler_default is not _UNSET:
                    pl['scheduler_default'] = scheduler_default
                    changed = True
                if changed:
                    pl['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                break
        if changed:
            return write_playlists(store), get_playlist_by_id(pid)
        return True, get_playlist_by_id(pid)
    except Exception:
        logger.exception('Failed to update playlist')
        return False, None


def delete_playlist(pid: str) -> bool:
    try:
        store = read_playlists()
        store['playlists'] = [pl for pl in store.get('playlists', []) if pl.get('id') != pid]
        if store.get('active_playlist_id') == pid:
            store['active_playlist_id'] = None
        return write_playlists(store)
    except Exception:
        logger.exception('Failed to delete playlist')
        return False


def duplicate_playlist(pid: str):
    try:
        src = get_playlist_by_id(pid)
        if not src:
            return False, None
        name = f"{src.get('name','playlist')} (copy)"
        items = list(src.get('items', []))
        return create_playlist(name, items)
    except Exception:
        logger.exception('Failed to duplicate playlist')
        return False, None


def set_active_playlist(pid: str) -> bool:
    try:
        store = read_playlists()
        # validate exists
        if not any(pl.get('id') == pid for pl in store.get('playlists', [])):
            return False
        store['active_playlist_id'] = pid
        return write_playlists(store)
    except Exception:
        logger.exception('Failed to set active playlist')
        return False


def get_active_playlist_id():
    try:
        store = read_playlists()
        return store.get('active_playlist_id')
    except Exception:
        return None

def _get_local_now():
    """Return current datetime in the configured timezone.
    Priority: signage_config.json > TZ env var > system local time.
    """
    from datetime import datetime
    # config file wins (set from dashboard UI)
    cfg = read_config()
    tz_name = (cfg.get('timezone') or os.environ.get('TZ', '')).strip()
    if tz_name and tz_name.upper() != 'UTC':
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name)).replace(tzinfo=None)
        except Exception:
            pass
    return datetime.now()


def get_scheduled_items(playlist: dict, now=None) -> list:
    """Return items to play right now for a scheduler-enabled playlist.

    Filters items whose schedule window covers the current time. If nothing
    matches, returns [scheduler_default] when set, else all items as fallback.
    Items with schedule=None are skipped when the scheduler is on.
    """
    if now is None:
        now = _get_local_now()

    active = []
    for item in playlist.get('items', []):
        sched = item.get('schedule')
        if not sched or not sched.get('recurrence'):
            continue
        try:
            start = datetime.strptime(sched['start_time'], '%H:%M').time()
            end   = datetime.strptime(sched['end_time'],   '%H:%M').time()
        except (KeyError, ValueError):
            continue
        in_window = start <= now.time() < end
        if sched['recurrence'] == 'daily' and in_window:
            active.append(item)
        elif sched['recurrence'] == 'once':
            try:
                target = datetime.strptime(sched['date'], '%Y-%m-%d').date()
                if target == now.date() and in_window:
                    active.append(item)
            except (KeyError, ValueError):
                pass

    if active:
        return active

    default = playlist.get('scheduler_default')
    if default:
        return [default]

    return playlist.get('items', [])


def reorder_playlists(id_order: list) -> bool:
    try:
        store = read_playlists()
        id_to_pl = {pl.get('id'): pl for pl in store.get('playlists', [])}
        new_list = []
        for pid in id_order:
            if pid in id_to_pl:
                new_list.append(id_to_pl[pid])
        # append any missing at end to avoid loss
        for pl in store.get('playlists', []):
            if pl.get('id') not in id_order:
                new_list.append(pl)
        store['playlists'] = new_list
        return write_playlists(store)
    except Exception:
        logger.exception('Failed to reorder playlists')
        return False
