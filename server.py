#!/usr/bin/env python3
import json
import os
import queue
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen

import websocket


ROOT = Path(__file__).resolve().parent


def load_env_file(path):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(ROOT / ".env")

HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
PORT = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or "8787")
WST_USER = os.getenv("WST_USER", "")
WST_PASS = os.getenv("WST_PASS", "")
AMAP_KEY = os.getenv("AMAP_KEY", "")
AMAP_CITY = os.getenv("AMAP_CITY", "330113")
WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open_meteo")
WEATHER_LATITUDE = os.getenv("WEATHER_LATITUDE", "30.42")
WEATHER_LONGITUDE = os.getenv("WEATHER_LONGITUDE", "120.30")
WEATHER_CITY_NAME = os.getenv("WEATHER_CITY_NAME", "浙江 杭州临平")
WEATHER_INTERVAL = int(os.getenv("WEATHER_INTERVAL", "600"))

state_lock = threading.Lock()
latest = {
    "connected": False,
    "status": "starting",
    "message": "正在启动",
    "updatedAt": None,
    "device": None,
    "data": None,
    "weather": None,
    "weatherError": None,
    "error": None,
}
subscribers = set()


def publish(payload):
    dead = []
    for sub in list(subscribers):
        try:
            sub.put_nowait(payload)
        except Exception:
            dead.append(sub)
    for sub in dead:
        subscribers.discard(sub)


def update_state(**changes):
    with state_lock:
        latest.update(changes)
        payload = dict(latest)
    publish(payload)


def split_frames(message):
    if "<reg>" in message:
        return [part for part in message.split("<reg>") if part.strip()]
    return [message]


def handle_upstream_message(message):
    for part in split_frames(message):
        part = part.strip()
        if not part:
            continue
        try:
            parsed = json.loads(part)
        except json.JSONDecodeError:
            update_state(message=part[:200], updatedAt=datetime.now().isoformat(timespec="seconds"))
            continue

        if isinstance(parsed, dict) and "Anion" in parsed:
            update_state(
                connected=True,
                status="live",
                message="实时数据已更新",
                updatedAt=datetime.now().isoformat(timespec="seconds"),
                data=parsed,
                error=None,
            )
        elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "DeviceID" in parsed[0]:
            update_state(
                connected=True,
                status="device",
                message="设备信息已载入",
                updatedAt=datetime.now().isoformat(timespec="seconds"),
                device=parsed[0],
                error=None,
            )
        elif isinstance(parsed, dict) and parsed.get("Result") == "Success":
            update_state(
                connected=True,
                status="connected",
                message=parsed.get("Message", "登录成功"),
                updatedAt=datetime.now().isoformat(timespec="seconds"),
                error=None,
            )
        elif isinstance(parsed, dict):
            update_state(
                connected=True,
                status="message",
                message=parsed.get("Message") or parsed.get("msg") or "收到服务器消息",
                updatedAt=datetime.now().isoformat(timespec="seconds"),
                error=None,
            )


def upstream_loop():
    if not WST_USER or not WST_PASS:
        update_state(status="missing_credentials", message="请设置 WST_USER 和 WST_PASS", error="missing credentials")
        return

    url = f"ws://register.woston.cn:8888/User/{WST_USER}/{WST_PASS}"
    while True:
        update_state(connected=False, status="connecting", message="正在连接检测仪云端", error=None)
        try:
            ws = websocket.create_connection(url, timeout=20)
            update_state(connected=True, status="connected", message="已连接，等待实时数据", error=None)
            ws.settimeout(70)
            while True:
                try:
                    handle_upstream_message(ws.recv())
                except websocket.WebSocketTimeoutException:
                    update_state(
                        connected=True,
                        status="waiting",
                        message="连接正常，等待下一次设备上报",
                        updatedAt=datetime.now().isoformat(timespec="seconds"),
                    )
        except Exception as exc:
            update_state(connected=False, status="reconnecting", message="连接断开，5 秒后重连", error=str(exc))
            time.sleep(5)


WEATHER_CODE_TEXT = {
    0: "晴",
    1: "大部晴朗",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "霜雾",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "阵雨",
    81: "强阵雨",
    82: "暴雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "雷暴伴小冰雹",
    99: "雷暴伴冰雹",
}


def wind_direction_name(degrees):
    try:
        value = float(degrees)
    except (TypeError, ValueError):
        return "--"
    names = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return names[round(value / 45) % 8]


def parse_amap_weather(raw):
    payload = json.loads(raw)
    if payload.get("status") != "1" or not payload.get("lives"):
        return None, payload.get("info") or "高德天气接口无数据"
    live = payload["lives"][0]
    return {
        "province": live.get("province"),
        "city": live.get("city"),
        "adcode": live.get("adcode"),
        "weather": live.get("weather"),
        "temperature": live.get("temperature"),
        "winddirection": live.get("winddirection"),
        "windpower": live.get("windpower"),
        "humidity": live.get("humidity"),
        "reporttime": live.get("reporttime"),
        "source": "高德天气",
    }, None


def parse_open_meteo_weather(raw):
    payload = json.loads(raw)
    current = payload.get("current") or {}
    if not current:
        return None, "Open-Meteo 天气接口无数据"

    code = current.get("weather_code")
    weather = WEATHER_CODE_TEXT.get(code, "实时天气")
    speed = current.get("wind_speed_10m")
    return {
        "province": "",
        "city": WEATHER_CITY_NAME,
        "adcode": "",
        "weather": weather,
        "temperature": current.get("temperature_2m"),
        "winddirection": wind_direction_name(current.get("wind_direction_10m")),
        "windpower": f"{speed} km/h" if speed is not None else "--",
        "windUnit": "",
        "humidity": current.get("relative_humidity_2m"),
        "reporttime": current.get("time"),
        "source": "Open-Meteo",
    }, None


def weather_loop():
    provider = WEATHER_PROVIDER.lower()
    if provider == "amap" and not AMAP_KEY:
        update_state(
            weather=None,
            weatherError="weather_not_configured",
        )
        return

    while True:
        try:
            if provider == "amap" or (provider == "auto" and AMAP_KEY):
                params = urlencode({
                    "key": AMAP_KEY,
                    "city": AMAP_CITY,
                    "extensions": "base",
                    "output": "JSON",
                })
                url = f"https://restapi.amap.com/v3/weather/weatherInfo?{params}"
                parser = parse_amap_weather
            else:
                params = urlencode({
                    "latitude": WEATHER_LATITUDE,
                    "longitude": WEATHER_LONGITUDE,
                    "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m",
                    "timezone": "Asia/Shanghai",
                })
                url = f"https://api.open-meteo.com/v1/forecast?{params}"
                parser = parse_open_meteo_weather

            with urlopen(url, timeout=12) as resp:
                weather, err = parser(resp.read().decode("utf-8", "replace"))
            update_state(weather=weather, weatherError=err)
        except Exception as exc:
            update_state(weatherError=str(exc))
        time.sleep(max(60, WEATHER_INTERVAL))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send_headers(self, status=200, content_type="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = (ROOT / "index.html").read_bytes()
            self._send_headers(200, "text/html; charset=utf-8")
            self.wfile.write(body)
            return

        if path.startswith("/assets/"):
            rel = path.lstrip("/")
            target = (ROOT / rel).resolve()
            if ROOT.resolve() in target.parents and target.exists():
                content_type = "application/octet-stream"
                if target.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                    content_type = f"image/{'jpeg' if target.suffix.lower() in {'.jpg', '.jpeg'} else 'png'}"
                self._send_headers(200, content_type)
                self.wfile.write(target.read_bytes())
                return

        if path == "/latest":
            with state_lock:
                body = json.dumps(latest, ensure_ascii=False).encode("utf-8")
            self._send_headers(200, "application/json; charset=utf-8")
            self.wfile.write(body)
            return

        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = queue.Queue(maxsize=10)
            subscribers.add(q)
            try:
                with state_lock:
                    initial = dict(latest)
                self.wfile.write(f"data: {json.dumps(initial, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    payload = q.get(timeout=25)
                    self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                subscribers.discard(q)
            return

        self._send_headers(404, "text/plain; charset=utf-8")
        self.wfile.write(b"not found")


def main():
    threading.Thread(target=upstream_loop, daemon=True).start()
    threading.Thread(target=weather_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard: http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
