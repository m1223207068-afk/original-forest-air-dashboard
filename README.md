# 原始森林生态空气数据看板

本地网页看板会连接沃斯彤实时 WebSocket，并在浏览器中显示最新负氧离子值、设备环境指标、趋势曲线，以及实时天气。打开网页后先输入沃斯彤账号密码登录，每个浏览器会话独立连接自己的设备，适合多个门店使用同一个网址。

## 启动

方式一：直接启动，打开网页后登录沃斯彤账号。

```bash
python3 server.py
```

方式二：复制 `.env.example` 为 `.env`，填入 `AMAP_KEY` 后启动。

```bash
cp .env.example .env
python3 server.py
```

然后打开:

```text
http://127.0.0.1:8787
```

## 接口

- `GET /` 看板页面
- `GET /session` 当前浏览器登录状态
- `POST /login` 使用沃斯彤账号密码创建当前浏览器会话
- `POST /logout` 退出当前浏览器会话
- `GET /latest` 最新状态 JSON
- `GET /events` Server-Sent Events 实时流

## 配置

源码不保存门店账号密码。沃斯彤账号密码由用户打开网页后输入，后端只保存在当前运行进程的浏览器会话中。

- `WEATHER_PROVIDER`: 天气源，默认 `open_meteo`，无需 Key；也可设为 `amap`
- `WEATHER_LATITUDE`: Open-Meteo 纬度，默认 `30.42`
- `WEATHER_LONGITUDE`: Open-Meteo 经度，默认 `120.30`
- `WEATHER_CITY_NAME`: 看板显示的城市名，默认 `浙江 杭州临平`
- `AMAP_KEY`: 高德开放平台 Web 服务 Key
- `AMAP_CITY`: 高德城市 adcode，默认 `330113`，即杭州市临平区
- `WEATHER_INTERVAL`: 天气刷新秒数，默认 `600`
- `IP_WEATHER_PROVIDER`: 访问者 IP 天气定位，默认 `ipwhois`，无需 Key；定位失败时回退到 `WEATHER_LATITUDE` / `WEATHER_LONGITUDE`

默认使用 Open-Meteo 免费天气接口，无需 `AMAP_KEY`。如果 `WEATHER_PROVIDER=amap` 但未设置 `AMAP_KEY`，看板会显示“天气接口待接入”，不会影响仪器实时数据展示。

Open-Meteo 接口地址为 `https://api.open-meteo.com/v1/forecast`，无须注册和 Key。高德天气接口使用官方 Web 服务天气查询，地址为 `https://restapi.amap.com/v3/weather/weatherInfo`，需要高德开放平台的 Web 服务 Key。

网页左侧天气会优先按访问者公网 IP 自动定位，并通过 Open-Meteo 查询当地实时天气。IP 定位使用 `https://ipwho.is` 的免费接口，无须 Key。若访问者 IP 无法定位，例如本地调试、内网、代理异常，系统会回退到默认坐标。

## 免费部署

这个看板需要 Python 后端持续连接检测仪云端 WebSocket，建议部署到 Render 或 Koyeb 这类可运行长期 Web Service 的平台。Vercel、Netlify、Cloudflare Pages 更适合纯静态网页，不适合直接跑这个实时后端。

部署时环境变量建议配置:

```text
WEATHER_PROVIDER=open_meteo
WEATHER_LATITUDE=30.42
WEATHER_LONGITUDE=120.30
WEATHER_CITY_NAME=浙江 杭州临平
IP_WEATHER_PROVIDER=ipwhois
```

启动命令:

```bash
python3 server.py
```

## 国内访问部署

Render 在国内访问可能不稳定。国内优先建议部署到腾讯云 CloudBase 云托管，项目已提供 `Dockerfile`，可直接按容器服务部署。

CloudBase 云托管关键配置:

```text
服务类型: Web 服务 / 云托管
部署方式: GitHub 仓库或本地代码上传
构建方式: Dockerfile
容器端口: 8080
启动命令: 使用 Dockerfile 默认 CMD
```

环境变量建议配置:

```text
WEATHER_PROVIDER=open_meteo
WEATHER_LATITUDE=30.42
WEATHER_LONGITUDE=120.30
WEATHER_CITY_NAME=浙江 杭州临平
IP_WEATHER_PROVIDER=ipwhois
```

如果使用高德天气，把 `WEATHER_PROVIDER` 改为 `amap`，并额外设置:

```text
AMAP_KEY=你的高德Web服务Key
AMAP_CITY=330113
```

本地可先用 Docker 验证:

```bash
docker build -t original-forest-air-dashboard .
docker run --rm -p 8080:8080 --env-file .env original-forest-air-dashboard
```

然后打开:

```text
http://127.0.0.1:8080
```
