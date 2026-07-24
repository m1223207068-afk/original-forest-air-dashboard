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
- `AMAP_KEY`: 高德开放平台 Web 服务 Key
- `WEATHER_INTERVAL`: 天气刷新秒数，默认 `600`

看板天气已强制固定为浙江杭州富阳（`30.048, 119.960`，高德 adcode `330183`），不再读取访问者公网 IP；所有门店和电视端会显示同一地点的实时天气。

默认使用 Open-Meteo 免费天气接口，无需 `AMAP_KEY`。如果 `WEATHER_PROVIDER=amap` 但未设置 `AMAP_KEY`，看板会显示“天气接口待接入”，不会影响仪器实时数据展示。

Open-Meteo 接口地址为 `https://api.open-meteo.com/v1/forecast`，无须注册和 Key。高德天气接口使用官方 Web 服务天气查询，地址为 `https://restapi.amap.com/v3/weather/weatherInfo`，需要高德开放平台的 Web 服务 Key。

网页左侧天气通过 Open-Meteo 查询富阳实时天气，避免内网、代理和电视端网络造成定位漂移。

## 免费部署

这个看板需要 Python 后端持续连接检测仪云端 WebSocket，建议部署到 Render 或 Koyeb 这类可运行长期 Web Service 的平台。Vercel、Netlify、Cloudflare Pages 更适合纯静态网页，不适合直接跑这个实时后端。

部署时环境变量建议配置:

```text
WEATHER_PROVIDER=open_meteo
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
```

如果使用高德天气，把 `WEATHER_PROVIDER` 改为 `amap`，并额外设置:

```text
AMAP_KEY=你的高德Web服务Key
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

## Cloudflare Workers 部署

项目已准备 Cloudflare Worker 版本，目录在 `cloudflare-worker/`。这个版本使用 Worker + Durable Object 承载登录会话和沃斯彤实时 WebSocket。

注意：Cloudflare Pages 不能原样运行当前 Python 后端；要部署到 Cloudflare，需要使用 `cloudflare-worker/` 里的 Worker 版本。

部署步骤：

```bash
cd cloudflare-worker
npm install
npx wrangler login
npx wrangler deploy
```

部署后，打开 Cloudflare 返回的 `workers.dev` 地址即可。网页登录沃斯彤账号密码，每个浏览器会话独立连接设备。

Cloudflare 版本注意事项：

- Durable Object 会保存当前浏览器会话的沃斯彤账号密码，用于维持实时连接
- 出站 WebSocket 在 Cloudflare 上不支持 hibernation，会产生持续运行费用
- 如果沃斯彤 WebSocket 的 `ws://register.woston.cn:8888` 被 Cloudflare 边缘网络限制，需要回退到容器部署方案，例如 CloudBase / Render
