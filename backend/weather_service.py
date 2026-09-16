import time
import threading
import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

CACHE_SECONDS = 300

_lock = threading.Lock()
_cache = {}


def get_weather(lat, lon):
    key = (round(lat, 3), round(lon, 3))

    with _lock:
        cached = _cache.get(key)

    if cached and time.time() - cached["timestamp"] < CACHE_SECONDS:
        return cached["data"]

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "precipitation,"
            "rain,"
            "showers,"
            "snowfall,"
            "wind_speed_10m,"
            "weather_code"
        ),
        "timezone": "auto"
    }

    response = requests.get(
        OPEN_METEO_URL,
        params=params,
        timeout=10
    )

    response.raise_for_status()

    data = response.json()

    current = data.get("current", {})

    result = {
        "temperature": current.get("temperature_2m", 0),
        "humidity": current.get("relative_humidity_2m", 0),
        "precipitation": current.get("precipitation", 0),
        "rain": current.get("rain", 0),
        "showers": current.get("showers", 0),
        "snowfall": current.get("snowfall", 0),
        "wind_speed": current.get("wind_speed_10m", 0),
        "weather_code": current.get("weather_code", 0),
        "updated_at": time.time()
    }

    with _lock:
        _cache[key] = {
            "timestamp": time.time(),
            "data": result
        }

    return result


def calculate_weather_risk(weather):
    risk = 0

    rain = weather.get("rain", 0)
    precipitation = weather.get("precipitation", 0)
    wind = weather.get("wind_speed", 0)
    snowfall = weather.get("snowfall", 0)

    if rain >= 10 or precipitation >= 10:
        risk += 25
    elif rain >= 5 or precipitation >= 5:
        risk += 15
    elif rain > 0 or precipitation > 0:
        risk += 5

    if wind >= 50:
        risk += 20
    elif wind >= 30:
        risk += 10
    elif wind >= 20:
        risk += 5

    if snowfall > 0:
        risk += 20

    return min(risk, 50)