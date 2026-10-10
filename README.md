# ShortDrama Player · 六平台短剧聚合播放器

一个本地运行的中文网页播放器，连接 **红果、ReelShort、DramaBox、DramaWave、NetShort、GoodShort**。切换平台即可浏览推荐、搜索剧目、查看完整集数并播放当前可观看的单集。

**开源版的红果内置后端播放官网开放集，其余集数提供官方入口。** 可在“连接设置”中接入自己的兼容后端。本包不附带原桌面应用的本地全集后端；无需原电脑目录、模拟器或签名服务即可启动。

![MIT](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)

## 功能

- 六平台切换、内容语言选择、推荐翻页、关键词搜索和剧目详情。
- 完整选集、MP4 / HLS 播放、下一集、字幕及平台提供的多音轨。
- 平台榜单，红果真人剧、漫剧、AI 剧分类与榜单切换。
- 全平台聚合榜：交错展示六个平台榜单各自的前五部，最多 30 部；展示序号不是跨平台统一热度排名。
- 收藏、观看记录和进度恢复，存于当前浏览器，最多各 200 部。
- 原生 HTML / CSS / JavaScript 界面，播放器库随包附带，运行无需 Node.js 或前端构建。

## 下载与启动

从 [Releases](https://github.com/zuomeizuoyang/shortdrama-player/releases/latest) 下载 `shortdrama-player-v0.2.0.zip`，解压后启动。压缩包包含源码、界面和启动脚本，**不包含 Python 运行时**。

### Windows

安装 Python 3.10+，安装时勾选 **Add Python to PATH**，然后双击 `start.cmd`。首次启动会创建本地 `.venv`、安装依赖并打开：

**http://127.0.0.1:8790**

### macOS / Linux

```shell
git clone https://github.com/zuomeizuoyang/shortdrama-player.git
cd shortdrama-player
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --open
```

### Windows 手动启动

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py --open
```

按 **Ctrl+C** 停止服务。端口被占用时使用 `python app.py --port 8791 --open`。服务默认只监听本机。

## 红果后端

`app.py` 自动启动内置的红果公开内容后端，自动选择空闲本地端口并管理密钥。密钥不返回浏览器，也不传给视频 CDN。内置后端读取公开榜单、搜索、详情和官网播放器页面，查看完整集数时保留官网开放范围。

如已有自己的兼容后端，右上角“连接设置”可填写地址和 API Key，保存后测试。也可以在启动前配置：

```powershell
$env:HONGGUO_BASE_URL = 'http://127.0.0.1:8000'
$env:HONGGUO_API_KEY = 'replace-with-your-own-backend-key'
python app.py --open
```

设置 `HONGGUO_BASE_URL` 后不启动内置后端。自定义后端应支持 `/rank`、`/browse`、`/search`、`/episodes`、`/play` 及可播放资源入口；排行榜和推荐使用 `/rank`。连接设置存在服务内存中，重启后恢复启动配置。`.env.example` 仅提供配置示例，程序不会自动读取 `.env`。

`--hongguo-port 8000` 可以固定内置后端端口，`--no-hongguo-backend` 可以关闭自动启动。独立运行内置后端前需设置自己的 `HONGGUO_API_KEY`，再执行 `python hongguo_backend.py --port 8000`。

## 接口

`platform` 为 `hongguo / reelshort / dramabox / dramawave / netshort / goodshort`。

| 方法 | HTTP 路由 | 请求字段 |
| --- | --- | --- |
| 推荐 | POST `/api/{platform}/feed` | `language, cursor` |
| 搜索 | POST `/api/{platform}/search` | `keyword, language, cursor` |
| 详情 | POST `/api/{platform}/detail` | `drama_id, language` |
| 完整集数 | POST `/api/{platform}/episodes` | `drama_id, language` |
| 播放解析 | POST `/api/{platform}/resolvePlayback` | `episode_id, language` |
| 榜单 | POST `/api/{platform}/rank` | `board, category, cursor, language` |
| 聚合榜 | POST `/api/all/rank` | `language` |

接口返回 `{ok: true, data: ...}`；失败返回 `{ok: false, error: {code, message}}`。剧目使用接口返回的 `drama_id`，单集使用 `episode_id`。播放 `status=playable` 时 `url` 是本地 `/media/{token}` 地址；其他状态保留官方入口。

媒体转发保留所需请求头、支持 Range、改写 HLS 相对地址，并检查浏览器会话。调用接口和播放时需保持同一个 HTTP 会话。

## 验证

```shell
python -m unittest discover -s . -t . -p "test_*.py" -v
```

测试覆盖平台访问状态、分页、完整章节、榜单路由、媒体令牌、HLS 改写和本地 HTTP 服务。GitHub Actions 在 Windows / macOS / Linux 上运行 Python 3.10 与 3.14 的回归检查。

启动播放器后，可用 `python verify_webui.py` 或 `python verify_hongguo.py` 人工执行联网检查。这些脚本会创建验证 JSON；联网结果取决于当时平台响应，不作为 CI 的通过条件。

## 当前边界

- 使用官网访客内容；不提供海外付费账户登录、购买或解锁流程。
- 官网 API 和页面可能变化；某平台故障时可继续使用其他平台。聚合榜会跳过请求失败的平台，因此有时少于 30 部。
- 榜单是各平台栏目或公开接口的当前结果。AI 剧采用上架时间、热度或收藏排序；各平台排序口径不同。
- 推荐通常是有限快照，不代表完整片库或无限个性化推荐。
- 红果搜索读取官网首屏，未实现官网搜索翻页；官网返回的总数可能大于实际获取数。
- NetShort 搜索可能混入官方推荐，最多保留 500 部去重结果。
- DramaWave 正片和花絮分别标记；内容语言与播放音轨分别处理。支持的语言不等于已经逐一验证所有语言播放。
- 收藏和观看进度只存在当前浏览器，未提供跨设备同步。

## 贡献与许可

欢迎提交 Issue 和 Pull Request，见 [CONTRIBUTING.md](CONTRIBUTING.md)。自有源码采用 [MIT](LICENSE)，允许使用、修改和再分发；第三方组件和平台内容保留各自权利，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

**English:** A local, open-source web player for Hongguo, ReelShort, DramaBox, DramaWave, NetShort and GoodShort. It includes recommendations, search, complete episode lists, platform charts, a combined chart view, MP4/HLS playback, subtitles, favorites and watch progress. Python 3.10+ is required; no frontend build is needed. The bundled Hongguo backend plays publicly accessible website episodes and links other episodes to the official platform. You can configure your own compatible backend. Project code is MIT licensed; third-party components retain their licenses.
