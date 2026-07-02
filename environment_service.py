#!/usr/bin/env python3
"""
Environment Service — Location, Weather, and Air Quality for the clock widget.

Zero-config by default: uses public free APIs that require no API key.
Optional env-var overrides for alternative providers:
  OPENWEATHER_KEY  → use OpenWeatherMap instead of Open-Meteo
  WAQI_TOKEN       → use World Air Quality Index instead of Open-Meteo AQI
"""

import json
import time
import logging
import threading
from urllib.request import urlopen, Request
from urllib.error import URLError
from urllib.parse import urlencode
import concurrent.futures

logger = logging.getLogger(__name__)

CACHE_TTL = 1800  # 30 minutes


# ── AQI helpers ────────────────────────────────────────────────────────────────

def _eaqi_category(value: int) -> dict:
    """European AQI (Open-Meteo scale 0–100+) → label + hex color."""
    if value <= 20:  return {'label': 'Excellent', 'color': '#22c55e'}
    if value <= 40:  return {'label': 'Good',       'color': '#86efac'}
    if value <= 60:  return {'label': 'Moderate',   'color': '#eab308'}
    if value <= 80:  return {'label': 'Poor',        'color': '#f97316'}
    if value <= 100: return {'label': 'Very Poor',   'color': '#ef4444'}
    return               {'label': 'Hazardous',  'color': '#991b1b'}


def _usaqi_category(value: int) -> dict:
    """US AQI (WAQI scale 0–500) → label + hex color."""
    if value <= 50:  return {'label': 'Good',                  'color': '#22c55e'}
    if value <= 100: return {'label': 'Moderate',              'color': '#eab308'}
    if value <= 150: return {'label': 'Sensitive Groups',      'color': '#f97316'}
    if value <= 200: return {'label': 'Unhealthy',             'color': '#ef4444'}
    if value <= 300: return {'label': 'Very Unhealthy',        'color': '#8b5cf6'}
    return               {'label': 'Hazardous',            'color': '#991b1b'}


# WMO weather interpretation codes → (human label, emoji icon)
_WMO_MAP = {
    0:  ('Sunny',           '☀'),
    1:  ('Mostly Clear',    '🌤'),
    2:  ('Partly Cloudy',   '⛅'),
    3:  ('Overcast',        '☁'),
    45: ('Fog',             '🌫'),
    48: ('Freezing Fog',    '🌫'),
    51: ('Light Drizzle',   '🌦'),
    53: ('Drizzle',         '🌦'),
    55: ('Heavy Drizzle',   '🌧'),
    61: ('Light Rain',      '🌧'),
    63: ('Rain',            '🌧'),
    65: ('Heavy Rain',      '🌧'),
    71: ('Light Snow',      '❄'),
    73: ('Snow',            '🌨'),
    75: ('Heavy Snow',      '🌨'),
    77: ('Snow Grains',     '❄'),
    80: ('Rain Showers',    '🌦'),
    81: ('Showers',         '🌧'),
    82: ('Heavy Showers',   '🌧'),
    85: ('Snow Showers',    '🌨'),
    86: ('Heavy Snow',      '🌨'),
    95: ('Thunderstorm',    '⛈'),
    96: ('Hail Storm',      '⛈'),
    99: ('Severe Storm',    '⛈'),
}


def _http_get(url: str, timeout: int = 10) -> dict:
    req = Request(url, headers={'User-Agent': 'LitmusSignage/1.0'})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


# ── LocationService ────────────────────────────────────────────────────────────

class LocationService:
    """
    Detects the server's geographic location from its public IP.
    Uses ipapi.co (primary) and ip-api.com (fallback) — both free, no key required.
    """

    def get(self) -> dict:
        """Returns {'city', 'region', 'country', 'lat', 'lon'} or raises."""
        # Primary: ipapi.co
        try:
            d = _http_get('https://ipapi.co/json/', timeout=8)
            if d.get('latitude') and d.get('longitude') and not d.get('error'):
                return {
                    'city':    d.get('city', ''),
                    'region':  d.get('region', ''),
                    'country': d.get('country_code', ''),
                    'lat':     float(d['latitude']),
                    'lon':     float(d['longitude']),
                }
        except Exception:
            logger.debug('Location primary (ipapi.co) failed, trying fallback')

        # Fallback: ip-api.com
        d = _http_get('http://ip-api.com/json/', timeout=8)
        if d.get('status') == 'success' and d.get('lat') and d.get('lon'):
            return {
                'city':    d.get('city', ''),
                'region':  d.get('regionName', ''),
                'country': d.get('countryCode', ''),
                'lat':     float(d['lat']),
                'lon':     float(d['lon']),
            }

        raise RuntimeError('Location detection failed (both providers unavailable)')


# ── WeatherService ─────────────────────────────────────────────────────────────

class WeatherService:
    """
    Fetches current weather conditions.
    Default: Open-Meteo (free, no API key).
    Set OPENWEATHER_KEY env var to switch to OpenWeatherMap.
    """

    def __init__(self, api_key: str = ''):
        self._api_key = api_key

    def get(self, lat: float, lon: float) -> dict:
        if self._api_key:
            return self._openweather(lat, lon)
        return self._open_meteo(lat, lon)

    def _open_meteo(self, lat: float, lon: float) -> dict:
        params = urlencode({
            'latitude':   lat,
            'longitude':  lon,
            'current':    ','.join([
                'temperature_2m', 'apparent_temperature', 'relative_humidity_2m',
                'weather_code', 'wind_speed_10m', 'visibility',
                'surface_pressure', 'cloud_cover', 'uv_index',
            ]),
            'temperature_unit': 'celsius',
            'wind_speed_unit':  'kmh',
            'timezone':         'auto',
        })
        d = _http_get(f'https://api.open-meteo.com/v1/forecast?{params}')
        c = d['current']
        code = int(c.get('weather_code', 0))
        cond, icon = _WMO_MAP.get(code, ('Unknown', '🌡'))
        vis_raw = c.get('visibility', 0)
        return {
            'temp_c':        round(c.get('temperature_2m', 0)),
            'feels_like_c':  round(c.get('apparent_temperature', 0)),
            'humidity':      int(c.get('relative_humidity_2m', 0)),
            'condition':     cond,
            'icon':          icon,
            'wind_kph':      round(c.get('wind_speed_10m', 0), 1),
            'visibility_km': round(float(vis_raw) / 1000, 1) if vis_raw else None,
            'pressure_hpa':  round(c.get('surface_pressure', 0)),
            'cloud_cover':   int(c.get('cloud_cover', 0)),
            'uv_index':      round(float(c.get('uv_index', 0)), 1) if c.get('uv_index') is not None else None,
        }

    # Map OpenWeather icon code prefix → emoji
    _OW_ICON = {
        '01': '☀', '02': '🌤', '03': '⛅', '04': '☁',
        '09': '🌦', '10': '🌧', '11': '⛈', '13': '❄', '50': '🌫',
    }

    def _openweather(self, lat: float, lon: float) -> dict:
        params = urlencode({'lat': lat, 'lon': lon, 'appid': self._api_key, 'units': 'metric'})
        d = _http_get(f'https://api.openweathermap.org/data/2.5/weather?{params}')
        cw   = (d.get('weather') or [{}])[0]
        icon_code = (cw.get('icon') or '01d')[:2]
        return {
            'temp_c':        round(d['main']['temp']),
            'feels_like_c':  round(d['main']['feels_like']),
            'humidity':      int(d['main']['humidity']),
            'condition':     cw.get('description', cw.get('main', 'Unknown')).title(),
            'icon':          self._OW_ICON.get(icon_code, '🌡'),
            'wind_kph':      round(d['wind']['speed'] * 3.6, 1),
            'visibility_km': round(d.get('visibility', 0) / 1000, 1),
            'pressure_hpa':  int(d['main']['pressure']),
            'cloud_cover':   int(d['clouds']['all']),
            'uv_index':      None,
        }


# ── AirQualityService ──────────────────────────────────────────────────────────

class AirQualityService:
    """
    Fetches air quality index.
    Default: Open-Meteo Air Quality API (free, no key) → European AQI.
    Set WAQI_TOKEN env var to switch to WAQI → US AQI.
    """

    def __init__(self, waqi_token: str = ''):
        self._waqi_token = waqi_token

    def get(self, lat: float, lon: float) -> dict:
        if self._waqi_token:
            return self._waqi(lat, lon)
        return self._open_meteo(lat, lon)

    def _open_meteo(self, lat: float, lon: float) -> dict:
        params = urlencode({
            'latitude':  lat,
            'longitude': lon,
            'current':   'european_aqi,pm2_5,pm10',
            'timezone':  'auto',
        })
        d = _http_get(f'https://air-quality-api.open-meteo.com/v1/air-quality?{params}')
        c    = d['current']
        eaqi = int(c.get('european_aqi') or 0)
        cat  = _eaqi_category(eaqi)
        return {
            'value': eaqi,
            'label': cat['label'],
            'color': cat['color'],
            'pm25':  round(float(c.get('pm2_5') or 0), 1),
            'pm10':  round(float(c.get('pm10')  or 0), 1),
            'scale': 'EAQI',
        }

    def _waqi(self, lat: float, lon: float) -> dict:
        params = urlencode({'token': self._waqi_token})
        d = _http_get(f'https://api.waqi.info/feed/geo:{lat};{lon}/?{params}')
        if d.get('status') != 'ok':
            raise RuntimeError('WAQI returned non-ok status')
        val = int(d['data']['aqi'])
        cat = _usaqi_category(val)
        return {
            'value': val,
            'label': cat['label'],
            'color': cat['color'],
            'pm25':  None,
            'pm10':  None,
            'scale': 'USAQI',
        }


# ── EnvironmentService ─────────────────────────────────────────────────────────

class EnvironmentService:
    """
    Orchestrates location, weather, and AQI; caches results for CACHE_TTL seconds.
    Thread-safe. Kicks off an initial background fetch on construction so that
    the first HTTP request to /api/environment returns instantly from cache.
    """

    def __init__(self):
        import os
        self._location_svc = LocationService()
        self._weather_svc  = WeatherService(api_key=os.environ.get('OPENWEATHER_KEY', ''))
        self._aqi_svc      = AirQualityService(waqi_token=os.environ.get('WAQI_TOKEN', ''))
        self._cache        = None
        self._cached_at    = 0.0
        self._refreshing   = False
        self._lock         = threading.Lock()
        # Warm the cache in the background so the first client request is instant
        t = threading.Thread(target=self._background_refresh, daemon=True)
        t.start()

    def get(self) -> dict:
        """Returns current environment data (from cache or triggers refresh)."""
        with self._lock:
            age = time.time() - self._cached_at
            if self._cache and age < CACHE_TTL:
                return self._cache
            if self._refreshing:
                # Return stale (or loading stub) rather than block the request
                return self._cache or {'ok': False, 'reason': 'loading'}
            self._refreshing = True

        fresh = self._fetch()
        with self._lock:
            self._cache      = fresh
            self._cached_at  = time.time()
            self._refreshing = False
        return fresh

    def _background_refresh(self):
        fresh = self._fetch()
        with self._lock:
            self._cache      = fresh
            self._cached_at  = time.time()
            self._refreshing = False
        logger.info(f"[Env] Initial refresh complete — ok={fresh.get('ok')}, "
                    f"city={fresh.get('location', {}).get('city', '?')}")

    def _fetch(self) -> dict:
        try:
            location = self._location_svc.get()
        except Exception as e:
            logger.warning(f'[Env] Location failed: {e}')
            return {'ok': False, 'reason': 'location_failed'}

        weather = None
        aqi     = None

        def _get_weather():
            try:
                return self._weather_svc.get(location['lat'], location['lon'])
            except Exception as e:
                logger.warning(f'[Env] Weather failed: {e}')
                return None

        def _get_aqi():
            try:
                return self._aqi_svc.get(location['lat'], location['lon'])
            except Exception as e:
                logger.warning(f'[Env] AQI failed: {e}')
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fw = ex.submit(_get_weather)
            fa = ex.submit(_get_aqi)
            weather = fw.result(timeout=20)
            aqi     = fa.result(timeout=20)

        return {
            'ok':        True,
            'location':  location,
            'weather':   weather,
            'aqi':       aqi,
            'cached_at': time.time(),
            'cache_ttl': CACHE_TTL,
        }
