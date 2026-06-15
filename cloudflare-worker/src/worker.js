import html from "../../index.html";
import logo from "../../assets/original_forest_logo.png";

const WST_COOKIE = "wst_session";
const WST_WS_BASE = "ws://register.woston.cn:8888/User/";

const WEATHER_CODE_TEXT = {
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
};

function jsonResponse(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...headers,
    },
  });
}

function textResponse(body, contentType = "text/plain; charset=utf-8", headers = {}, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": contentType,
      "cache-control": "no-store",
      ...headers,
    },
  });
}

function parseCookies(header = "") {
  const result = {};
  for (const part of header.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key) result[key] = decodeURIComponent(rest.join("="));
  }
  return result;
}

function cookieHeader(token, maxAge = 86400) {
  return `${WST_COOKIE}=${encodeURIComponent(token)}; Path=/; Max-Age=${maxAge}; HttpOnly; SameSite=Lax`;
}

function maskAccount(value = "") {
  if (!value) return "";
  if (value.length <= 2) return "*".repeat(value.length);
  if (value.length <= 5) return `${value[0]}***${value[value.length - 1]}`;
  return `${value.slice(0, 2)}***${value.slice(-2)}`;
}

function sessionStub(env, token) {
  if (!token) return null;
  const id = env.WST_SESSIONS.idFromName(token);
  return env.WST_SESSIONS.get(id);
}

function windDirectionName(degrees) {
  const value = Number(degrees);
  if (!Number.isFinite(value)) return "--";
  return ["北", "东北", "东", "东南", "南", "西南", "西", "西北"][Math.round(value / 45) % 8];
}

async function openMeteoWeather(latitude, longitude, cityName, timezone = "auto") {
  const params = new URLSearchParams({
    latitude,
    longitude,
    current: "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m",
    timezone: timezone || "auto",
  });
  const res = await fetch(`https://api.open-meteo.com/v1/forecast?${params}`, { cf: { cacheTtl: 60 } });
  const payload = await res.json();
  const current = payload.current || {};
  if (!current.weather_code && current.weather_code !== 0) return [null, "Open-Meteo 天气接口无数据"];
  const speed = current.wind_speed_10m;
  return [{
    province: "",
    city: cityName,
    adcode: "",
    weather: WEATHER_CODE_TEXT[current.weather_code] || "实时天气",
    temperature: current.temperature_2m,
    winddirection: windDirectionName(current.wind_direction_10m),
    windpower: speed !== undefined ? `${speed} km/h` : "--",
    windUnit: "",
    humidity: current.relative_humidity_2m,
    reporttime: current.time,
    source: "Open-Meteo",
  }, null];
}

async function visitorWeather(request, env) {
  const latitude = env.WEATHER_LATITUDE || "30.42";
  const longitude = env.WEATHER_LONGITUDE || "120.30";
  const fallbackCity = env.WEATHER_CITY_NAME || "浙江 杭州临平";
  const provider = env.IP_WEATHER_PROVIDER || "ipwhois";
  const ip = request.headers.get("cf-connecting-ip") || "";

  try {
    if (provider === "ipwhois" && ip) {
      const geo = await fetch(`https://ipwho.is/${encodeURIComponent(ip)}?fields=success,message,country,region,city,latitude,longitude,timezone`, { cf: { cacheTtl: 300 } });
      const location = await geo.json();
      if (location.success && location.latitude !== undefined && location.longitude !== undefined) {
        const cityName = [location.country, location.region, location.city].filter(Boolean).join(" ") || "访问者所在地";
        const timezone = typeof location.timezone === "object" ? location.timezone.id : "auto";
        const [weather, err] = await openMeteoWeather(String(location.latitude), String(location.longitude), cityName, timezone);
        if (weather) {
          weather.ipMode = true;
          weather.ip = ip;
          return [weather, null];
        }
        const [fallback] = await openMeteoWeather(latitude, longitude, fallbackCity, "Asia/Shanghai");
        if (fallback) {
          fallback.ipMode = false;
          fallback.fallbackReason = err || "IP 天气失败";
        }
        return [fallback, err];
      }
    }
    const [weather, err] = await openMeteoWeather(latitude, longitude, fallbackCity, "Asia/Shanghai");
    if (weather) weather.ipMode = false;
    return [weather, err];
  } catch (err) {
    try {
      const [weather] = await openMeteoWeather(latitude, longitude, fallbackCity, "Asia/Shanghai");
      if (weather) {
        weather.ipMode = false;
        weather.fallbackReason = String(err?.message || err);
      }
      return [weather, String(err?.message || err)];
    } catch (fallbackErr) {
      return [null, `${err}; fallback: ${fallbackErr}`];
    }
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const cookies = parseCookies(request.headers.get("cookie") || "");
    const token = cookies[WST_COOKIE] || "";

    if (request.method === "GET" && path === "/") {
      return textResponse(html, "text/html; charset=utf-8");
    }

    if (request.method === "GET" && path === "/assets/original_forest_logo.png") {
      return new Response(logo, {
        headers: {
          "content-type": "image/png",
          "cache-control": "public, max-age=86400",
        },
      });
    }

    if (request.method === "GET" && path === "/weather") {
      const [weather, weatherError] = await visitorWeather(request, env);
      return jsonResponse({ weather, weatherError });
    }

    if (request.method === "GET" && path === "/session") {
      const stub = sessionStub(env, token);
      if (!stub) return jsonResponse({ authenticated: false, wstUser: "" });
      return stub.fetch("https://session/session");
    }

    if (request.method === "GET" && path === "/latest") {
      const stub = sessionStub(env, token);
      if (!stub) {
        return jsonResponse({
          authenticated: false,
          connected: false,
          status: "login_required",
          message: "请先登录沃斯彤账号",
          data: null,
          device: null,
          error: null,
        }, 401);
      }
      return stub.fetch("https://session/latest");
    }

    if (request.method === "GET" && path === "/events") {
      const stub = sessionStub(env, token);
      if (!stub) return jsonResponse({ authenticated: false, message: "请先登录沃斯彤账号" }, 401);
      return stub.fetch("https://session/events");
    }

    if (request.method === "POST" && path === "/login") {
      let payload = {};
      try {
        payload = await request.json();
      } catch {
        return jsonResponse({ ok: false, message: "请求格式错误" }, 400);
      }
      const user = String(payload.user || "").trim();
      const password = String(payload.password || "").trim();
      if (!user || !password) return jsonResponse({ ok: false, message: "请输入沃斯彤账号和密码" }, 400);

      if (token) {
        const old = sessionStub(env, token);
        if (old) await old.fetch("https://session/logout", { method: "POST" }).catch(() => {});
      }
      const newToken = crypto.randomUUID();
      const stub = sessionStub(env, newToken);
      const res = await stub.fetch("https://session/login", {
        method: "POST",
        body: JSON.stringify({ user, password }),
      });
      const body = await res.json();
      return jsonResponse(body, res.status, { "set-cookie": cookieHeader(newToken) });
    }

    if (request.method === "POST" && path === "/logout") {
      const stub = sessionStub(env, token);
      if (stub) await stub.fetch("https://session/logout", { method: "POST" }).catch(() => {});
      return jsonResponse({ ok: true }, 200, { "set-cookie": cookieHeader("", 0) });
    }

    return textResponse("not found", "text/plain; charset=utf-8", {}, 404);
  },
};

export class WstSession {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.ws = null;
    this.subscribers = new Map();
    this.nextSubscriberId = 1;
    this.reconnectTimer = null;
    this.latest = {
      connected: false,
      status: "starting",
      message: "正在启动",
      updatedAt: null,
      device: null,
      data: null,
      weather: null,
      weatherError: null,
      error: null,
      config: { wstUser: "" },
      authenticated: false,
    };
    this.ready = this.load();
  }

  async load() {
    const stored = await this.state.storage.get(["user", "password", "latest"]);
    this.user = stored.user || "";
    this.password = stored.password || "";
    this.latest = stored.latest || this.latest;
    if (this.user && this.password) this.connect();
  }

  async fetch(request) {
    await this.ready;
    const url = new URL(request.url);
    if (url.pathname === "/session") {
      return jsonResponse({ authenticated: Boolean(this.user), wstUser: maskAccount(this.user || "") });
    }
    if (url.pathname === "/latest") {
      return jsonResponse(this.latest);
    }
    if (url.pathname === "/events") {
      return this.events();
    }
    if (url.pathname === "/login" && request.method === "POST") {
      const payload = await request.json();
      this.user = String(payload.user || "").trim();
      this.password = String(payload.password || "").trim();
      this.latest = {
        connected: false,
        status: "connecting",
        message: "正在连接检测仪云端",
        updatedAt: null,
        device: null,
        data: null,
        weather: null,
        weatherError: null,
        error: null,
        config: { wstUser: maskAccount(this.user) },
        authenticated: true,
      };
      await this.state.storage.put({ user: this.user, password: this.password, latest: this.latest });
      this.closeSocket();
      this.connect();
      return jsonResponse({ ok: true, message: "登录成功，正在连接设备", wstUser: maskAccount(this.user) });
    }
    if (url.pathname === "/logout") {
      this.closeSocket();
      this.user = "";
      this.password = "";
      await this.state.storage.deleteAll();
      return jsonResponse({ ok: true });
    }
    return jsonResponse({ ok: false, message: "not found" }, 404);
  }

  events() {
    const { readable, writable } = new TransformStream();
    const writer = writable.getWriter();
    const id = this.nextSubscriberId++;
    const write = async (payload) => {
      try {
        await writer.write(new TextEncoder().encode(`data: ${JSON.stringify(payload)}\n\n`));
      } catch {
        this.subscribers.delete(id);
      }
    };
    this.subscribers.set(id, write);
    write(this.latest);
    return new Response(readable, {
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
        connection: "keep-alive",
      },
    });
  }

  async update(changes) {
    this.latest = { ...this.latest, ...changes, config: { wstUser: maskAccount(this.user || "") }, authenticated: Boolean(this.user) };
    await this.state.storage.put("latest", this.latest);
    for (const write of this.subscribers.values()) write(this.latest);
  }

  connect() {
    if (!this.user || !this.password) return;
    this.closeSocket();
    this.update({ connected: false, status: "connecting", message: "正在连接检测仪云端", error: null });
    const ws = new WebSocket(`${WST_WS_BASE}${encodeURIComponent(this.user)}/${encodeURIComponent(this.password)}`);
    this.ws = ws;

    ws.addEventListener("open", () => {
      this.update({ connected: true, status: "connected", message: "已连接，等待实时数据", error: null });
    });
    ws.addEventListener("message", (event) => {
      this.handleUpstreamMessage(String(event.data || ""));
    });
    ws.addEventListener("close", () => {
      this.update({ connected: false, status: "reconnecting", message: "连接断开，5 秒后重连" });
      this.scheduleReconnect();
    });
    ws.addEventListener("error", (event) => {
      this.update({ connected: false, status: "reconnecting", message: "连接异常，5 秒后重连", error: String(event?.message || "websocket error") });
      this.scheduleReconnect();
    });
  }

  closeSocket() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    if (this.ws) {
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
  }

  scheduleReconnect() {
    if (!this.user || !this.password || this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, 5000);
  }

  handleUpstreamMessage(message) {
    const frames = message.includes("<reg>") ? message.split("<reg>").filter((part) => part.trim()) : [message];
    for (const frame of frames) {
      const part = frame.trim();
      if (!part) continue;
      let parsed;
      try {
        parsed = JSON.parse(part);
      } catch {
        this.update({ message: part.slice(0, 200), updatedAt: new Date().toISOString().slice(0, 19) });
        continue;
      }

      if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && "Anion" in parsed) {
        this.update({
          connected: true,
          status: "live",
          message: "实时数据已更新",
          updatedAt: new Date().toISOString().slice(0, 19),
          data: parsed,
          error: null,
        });
      } else if (Array.isArray(parsed) && parsed[0] && "DeviceID" in parsed[0]) {
        this.update({
          connected: true,
          status: "device",
          message: "设备信息已载入",
          updatedAt: new Date().toISOString().slice(0, 19),
          device: parsed[0],
          error: null,
        });
      } else if (parsed && parsed.Result === "Success") {
        this.update({
          connected: true,
          status: "connected",
          message: parsed.Message || "登录成功",
          updatedAt: new Date().toISOString().slice(0, 19),
          error: null,
        });
      } else if (parsed && typeof parsed === "object") {
        this.update({
          connected: true,
          status: "message",
          message: parsed.Message || parsed.msg || "收到服务器消息",
          updatedAt: new Date().toISOString().slice(0, 19),
          error: null,
        });
      }
    }
  }
}
