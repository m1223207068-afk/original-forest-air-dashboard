#!/usr/bin/env python3
import json
import os
import queue
import secrets
import threading
import time
from datetime import datetime
from http import cookies
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
AMAP_KEY = os.getenv("AMAP_KEY", "")
AMAP_CITY = "330183"
WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open_meteo")
WEATHER_LATITUDE = "30.048"
WEATHER_LONGITUDE = "119.960"
WEATHER_CITY_NAME = "杭州富阳"
WEATHER_INTERVAL = int(os.getenv("WEATHER_INTERVAL", "600"))

weather_lock = threading.Lock()
weather_state = {
    "weather": None,
    "weatherError": None,
}
sessions_lock = threading.Lock()
sessions = {}


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def mask_account(value):
    if not value:
        return ""
    if len(value) <= 2:
        return "*" * len(value)
    if len(value) <= 5:
        return f"{value[0]}***{value[-1]}"
    return f"{value[:2]}***{value[-2:]}"


class WstSession:
    def __init__(self, token, user, password):
        self.token = token
        self.user = str(user or "").strip()
        self.password = str(password or "").strip()
        self.lock = threading.Lock()
        self.subscribers = set()
        self.stop_event = threading.Event()
        self.ws = None
        self.thread = None
        self.latest = {
            "connected": False,
            "status": "starting",
            "message": "正在启动",
            "updatedAt": None,
            "device": None,
            "data": None,
            "weather": None,
            "weatherError": None,
            "error": None,
            "config": {"wstUser": mask_account(self.user)},
            "authenticated": True,
        }

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass

    def snapshot(self):
        with self.lock:
            payload = dict(self.latest)
        with weather_lock:
            payload["weather"] = weather_state.get("weather")
            payload["weatherError"] = weather_state.get("weatherError")
        return payload

    def publish(self, payload):
        dead = []
        for sub in list(self.subscribers):
            try:
                sub.put_nowait(payload)
            except Exception:
                dead.append(sub)
        for sub in dead:
            self.subscribers.discard(sub)

    def update(self, **changes):
        with self.lock:
            self.latest.update(changes)
            payload = dict(self.latest)
        with weather_lock:
            payload["weather"] = weather_state.get("weather")
            payload["weatherError"] = weather_state.get("weatherError")
        self.publish(payload)

    def handle_message(self, message):
        for part in split_frames(message):
            part = part.strip()
            if not part:
                continue
            try:
                parsed = json.loads(part)
            except json.JSONDecodeError:
                self.update(message=part[:200], updatedAt=now_iso())
                continue

            if isinstance(parsed, dict) and "Anion" in parsed:
                self.update(
                    connected=True,
                    status="live",
                    message="实时数据已更新",
                    updatedAt=now_iso(),
                    data=parsed,
                    error=None,
                )
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict) and "DeviceID" in parsed[0]:
                self.update(
                    connected=True,
                    status="device",
                    message="设备信息已载入",
                    updatedAt=now_iso(),
                    device=parsed[0],
                    error=None,
                )
            elif isinstance(parsed, dict) and parsed.get("Result") == "Success":
                self.update(
                    connected=True,
                    status="connected",
                    message=parsed.get("Message", "登录成功"),
                    updatedAt=now_iso(),
                    error=None,
                )
            elif isinstance(parsed, dict):
                self.update(
                    connected=True,
                    status="message",
                    message=parsed.get("Message") or parsed.get("msg") or "收到服务器消息",
                    updatedAt=now_iso(),
                    error=None,
                )

    def run(self):
        if not self.user or not self.password:
            self.update(status="missing_credentials", message="请先登录沃斯彤账号", error="missing credentials")
            return

        url = f"ws://register.woston.cn:8888/User/{self.user}/{self.password}"
        while not self.stop_event.is_set():
            self.update(connected=False, status="connecting", message="正在连接检测仪云端", error=None)
            try:
                ws = websocket.create_connection(url, timeout=20)
                self.ws = ws
                self.update(connected=True, status="connected", message="已连接，等待实时数据", error=None)
                ws.settimeout(70)
                while not self.stop_event.is_set():
                    try:
                        self.handle_message(ws.recv())
                    except websocket.WebSocketTimeoutException:
                        self.update(
                            connected=True,
                            status="waiting",
                            message="连接正常，等待下一次设备上报",
                            updatedAt=now_iso(),
                        )
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.update(connected=False, status="reconnecting", message="连接断开，5 秒后重连", error=str(exc))
                    time.sleep(5)
            finally:
                try:
                    if self.ws:
                        self.ws.close()
                except Exception:
                    pass
                self.ws = None


def split_frames(message):
    if "<reg>" in message:
        return [part for part in message.split("<reg>") if part.strip()]
    return [message]


def create_session(user, password):
    token = secrets.token_urlsafe(32)
    session = WstSession(token, user, password)
    with sessions_lock:
        sessions[token] = session
    session.start()
    return session


def get_session(token):
    if not token:
        return None
    with sessions_lock:
        return sessions.get(token)


def remove_session(token):
    with sessions_lock:
        session = sessions.pop(token, None)
    if session:
        session.stop()
    return session


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


def parse_open_meteo_weather(raw, city_name=WEATHER_CITY_NAME):
    payload = json.loads(raw)
    current = payload.get("current") or {}
    if not current:
        return None, "Open-Meteo 天气接口无数据"

    code = current.get("weather_code")
    weather = WEATHER_CODE_TEXT.get(code, "实时天气")
    speed = current.get("wind_speed_10m")
    return {
        "province": "",
        "city": city_name,
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


def open_meteo_weather(latitude, longitude, city_name, timezone="auto"):
    params = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m",
        "timezone": timezone or "auto",
    })
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    with urlopen(url, timeout=12) as resp:
        return parse_open_meteo_weather(resp.read().decode("utf-8", "replace"), city_name)


def fallback_weather():
    weather, err = open_meteo_weather(
        WEATHER_LATITUDE,
        WEATHER_LONGITUDE,
        WEATHER_CITY_NAME,
        "Asia/Shanghai",
    )
    return apply_fixed_location(weather), err


def apply_fixed_location(weather):
    if not weather:
        return weather
    weather["province"] = "浙江"
    weather["city"] = "杭州富阳"
    weather["adcode"] = "330183"
    weather["ipMode"] = False
    weather["fixedLocation"] = True
    return weather


def update_weather_state(weather=None, weather_error=None):
    with weather_lock:
        weather_state["weather"] = weather
        weather_state["weatherError"] = weather_error
    with sessions_lock:
        active_sessions = list(sessions.values())
    for session in active_sessions:
        session.publish(session.snapshot())


def visitor_weather(_headers, _client_address):
    try:
        return fallback_weather()
    except Exception as exc:
        return None, str(exc)


def weather_loop():
    provider = WEATHER_PROVIDER.lower()
    if provider == "amap" and not AMAP_KEY:
        update_weather_state(weather=None, weather_error="weather_not_configured")
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
                with urlopen(url, timeout=12) as resp:
                    weather, err = parser(resp.read().decode("utf-8", "replace"))
                weather = apply_fixed_location(weather)
            else:
                weather, err = fallback_weather()
            update_weather_state(weather=weather, weather_error=err)
        except Exception as exc:
            update_weather_state(weather_error=str(exc))
        time.sleep(max(60, WEATHER_INTERVAL))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send_headers(self, status=200, content_type="text/html; charset=utf-8", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_headers(status, "application/json; charset=utf-8", extra_headers)
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 4096:
            return None
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return json.loads(raw)

    def _session_token(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        morsel = jar.get("wst_session")
        return morsel.value if morsel else ""

    def _session(self):
        return get_session(self._session_token())

    def _cookie_header(self, token, max_age=86400):
        return f"wst_session={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax"

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

        if path == "/session":
            session = self._session()
            self._send_json({
                "authenticated": bool(session),
                "wstUser": mask_account(session.user) if session else "",
            })
            return

        if path == "/latest":
            session = self._session()
            if not session:
                self._send_json({
                    "authenticated": False,
                    "connected": False,
                    "status": "login_required",
                    "message": "请先登录沃斯彤账号",
                    "data": None,
                    "device": None,
                    "error": None,
                }, 401)
                return
            self._send_json(session.snapshot())
            return

        if path == "/weather":
            weather, err = visitor_weather(self.headers, self.client_address)
            self._send_json({
                "weather": weather,
                "weatherError": err,
            })
            return

        if path == "/events":
            session = self._session()
            if not session:
                self._send_json({"authenticated": False, "message": "请先登录沃斯彤账号"}, 401)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = queue.Queue(maxsize=10)
            session.subscribers.add(q)
            try:
                initial = session.snapshot()
                self.wfile.write(f"data: {json.dumps(initial, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    payload = q.get(timeout=25)
                    self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except Exception:
                session.subscribers.discard(q)
            return

        self._send_headers(404, "text/plain; charset=utf-8")
        self.wfile.write(b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/login":
            try:
                payload = self._read_json() or {}
            except Exception:
                self._send_json({"ok": False, "message": "请求格式错误"}, 400)
                return
            user = str(payload.get("user") or "").strip()
            password = str(payload.get("password") or "").strip()
            if not user or not password:
                self._send_json({"ok": False, "message": "请输入沃斯彤账号和密码"}, 400)
                return
            old_token = self._session_token()
            if old_token:
                remove_session(old_token)
            session = create_session(user, password)
            self._send_json({
                "ok": True,
                "message": "登录成功，正在连接设备",
                "wstUser": mask_account(user),
            }, extra_headers={"Set-Cookie": self._cookie_header(session.token)})
            return

        if path == "/logout":
            token = self._session_token()
            if token:
                remove_session(token)
            self._send_json({"ok": True}, extra_headers={"Set-Cookie": self._cookie_header("", max_age=0)})
            return

        self._send_json({"ok": False, "message": "not found"}, 404)


def main():
    threading.Thread(target=weather_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Dashboard: http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop.")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
