# Mercari / AstrBot 本地项目交接说明

这份文档面向没有本次对话上下文的 Agent，说明本地项目关系、运行路径、核心行为和安全操作约定。所有时间均按北京时间（`Asia/Shanghai`）理解。

## 1. 项目地图

| 角色 | 本地路径 | 说明 |
| --- | --- | --- |
| 原始 Web 项目 | `D:\explore\mercari` | 独立的 Mercari 搜索网页：FastAPI 后端 + Vite/React 前端。它不是 AstrBot 插件的运行目录。 |
| AstrBot 插件源码仓库 | `D:\explore\mercari-plugin` | 本项目的主要开发目录、Git 仓库。远端为 `https://github.com/al0se/astrbot_plugin_mercari_monitor.git`。 |
| 本地 AstrBot Docker 项目 | `D:\explore\astrbot` | 启动 AstrBot 与 NapCat 的 Docker Compose 目录。Compose 文件为 `D:\explore\astrbot\astrbot.yml`。 |
| AstrBot 已安装插件 | `D:\explore\astrbot\data\plugins\astrbot_plugin_mercari_monitor` | 正在被容器加载的插件副本；修改源码后必须同步此处并重启容器才会生效。 |
| 用户数据 | `D:\explore\astrbot\data\plugin_data\astrbot_plugin_mercari_monitor\users\<SHA-256(UMO)>.db` | 一名私聊用户/会话一份 SQLite 文件。不要删除或覆盖这些数据库。 |

## 2. 原始 Web 项目（`D:\explore\mercari`）

- 后端入口：`app/main.py`，FastAPI。
- Mercari 查询实现：`app/mercari_service.py`。
- 前端：`frontend/`，Vite + React。
- Python 配置：`pyproject.toml`；开发命令见根目录 `README.md`，典型命令为 `uv run uvicorn app.main:app --reload`。
- 它自己的历史库为 `D:\explore\mercari\data\mercari.db`，与 AstrBot 的用户 SQLite 库完全独立。

除非用户明确要求，不要把 Web 项目的依赖或实现直接搬进 AstrBot 插件。尤其是旧版 `mercari==2.2.1` 会与 AstrBot Core 的依赖产生冲突。

## 3. 插件功能与用户接口

插件仅支持私聊，不考虑群聊；只关注新品，不推送降价。

```text
/mercari 搜索 <关键词>       # 实时查询；不写入数据库
/mercari 订阅 <关键词>       # 记录当前结果为基线；不推送历史商品
/mercari 刷新 <关键词>       # 只返回此前未见的新商品；标记本轮结果为已见
/mercari 订阅列表
/mercari 状态
/mercari 取消订阅 <关键词>
```

### 配置

配置架构在 `_conf_schema.json`：

- `search_result_limit`：每个关键词读取的最新商品数，范围 10–100，默认 50。
- `max_push_items`：单次最多展开推送的新品数；`0` 表示全部展开。超出限制的商品仍会标记为已见。
- `check_on_startup`：启动时是否立即检查，默认关闭。

## 4. 核心实现与数据流

| 文件 | 职责 |
| --- | --- |
| `main.py` | AstrBot 入口、私聊命令、整点调度、主动 `context.send_message()` 推送、消息文本。 |
| `monitoring.py` | 查询去重与聚合：同一整点内相同关键词只向 Mercari 请求一次，再分别比较每位用户的库。 |
| `repository.py` | 每用户 SQLite、订阅、已见商品、整点批次迁移与读写。 |
| `mercari_service.py` | 调用 Mercari Web 搜索 API，按上架时间倒序解析商品。 |
| `patches.py` | Mercari API 请求需要的 DPoP/重试辅助逻辑。 |

### Mercari 商品 URL

不要对所有商品一律生成 `/item/<id>`：

- 普通个人商品：`https://jp.mercari.com/item/<id>`
- Mercari Shops 商品：`https://jp.mercari.com/shops/product/<id>`

在 `mercari_service._item_url()` 通过 `itemType == "ITEM_TYPE_BEYOND"` 或存在 `shop` 字段识别 Shops。

### 新品判定

`seen_items` 的主键是 `(item_id, keyword)`，它是“已见”事实来源。

- `订阅`：当前结果进入 `seen_items`，作为基线。
- `搜索`：只返回结果，不落库。
- `刷新`：只返回未见商品；但本轮全部查询结果都写入 `seen_items`，防止下次整点重复推送。
- 定时检查：只推送未见商品；没有新品时不发送“空提醒”。

### 固定整点批次（重要）

整点调度并不以“距离上次运行满一小时”为条件。每次使用固定批次，例如 `2026-08-31T15:00:00+08:00`。

- `subscriptions.last_scheduled_slot` 记录某订阅已成功处理的最后一个整点批次。
- 若该字段小于当前整点批次或为空，则执行本轮；查询成功后即写入当前批次，即使没有新品。
- 手动刷新和普通搜索不更新该字段。
- 因此任务晚几十毫秒醒来、前一轮在整点后一秒完成，都不会导致下一整点被错误跳过。
- 同一整点查询失败时会重试同一批次；未成功的批次不会标记完成。

当前已实现数据库自动迁移：旧库在首次被 `UserRepository` 打开时，会增加 `last_scheduled_slot` 列。不要手动修改该列。

### 推送与 LLM

新品推送使用已保存的 `unified_msg_origin` 调用 `context.send_message()`，不经由 LLM，不应写入 LLM 上下文。命令结果也由插件直接返回。

已知边界：当前“查询/写入已见”发生在调用消息平台发送之前；如果平台发送失败，商品已经标为已见，不会自动补发。若要解决，应设计持久化的待发送队列和发送成功确认，不能仅改消息文本。

## 5. AstrBot 运行与部署

`D:\explore\astrbot\astrbot.yml` 定义两个容器：

- `astrbot`：镜像 `m.daocloud.io/docker.io/soulter/astrbot:latest`，容器名 `astrbot`，挂载 `./data:/AstrBot/data`。
- `napcat`：容器名 `napcat`，负责 QQ/OneBot 通道。

插件源码不自动热更新。常用部署流程（PowerShell）：

```powershell
Copy-Item -LiteralPath 'D:\explore\mercari-plugin\main.py' `
  -Destination 'D:\explore\astrbot\data\plugins\astrbot_plugin_mercari_monitor\main.py' -Force

# 视改动内容同步 monitoring.py / repository.py / mercari_service.py / patches.py 等文件
docker restart astrbot
docker logs --since 90s astrbot 2>&1
```

对运行中容器、`D:\explore\astrbot` 或用户数据库的写操作，先获得用户授权。重启后日志应包含：

```text
Loading plugin astrbot_plugin_mercari_monitor
Mercari hourly scheduler started
Mercari next scheduled check: ...+08:00
```

## 6. 测试与排查

在插件源码目录执行：

```powershell
D:\explore\mercari\.venv\Scripts\python.exe -m pytest tests -q
```

当前测试覆盖：每用户独立数据库、手动刷新新品判定、同关键词查询合并、固定整点批次、个人/Shop URL。

排查定时器时优先查看：

```powershell
docker logs --since '2026-08-31T14:55:00+08:00' astrbot 2>&1
```

再查看用户库中的 `subscriptions`、`seen_items` 和 `last_scheduled_slot`。查询前先确认数据库绝对路径，严禁删除 `users` 目录。

## 7. Git 与协作约定

- 插件仓库为 `D:\explore\mercari-plugin`，默认分支 `main`。
- 最近已知提交：`0d95c57 fix: use Mercari Shops URLs for shop listings`。
- 用户曾明确要求：除非用户当次明确说“提交/上传/push”，否则可以修改、测试、部署，但不要自动执行 `git commit` 或 `git push`。
- 不要使用 `git reset --hard`、`git checkout --` 等破坏性 Git 操作，除非用户明确指定目标提交和允许丢弃的变更。
- 提交前应运行测试与 `git diff --check`；只提交本任务相关文件。

## 8. 修改时的常见坑

1. AstrBot 的命令 `event.chain_result()` 需要组件列表 `.chain`；主动 `context.send_message()` 则需要 `MessageChain`。当前插件主要使用纯文本，避免混淆。
2. QQ/NapCat 对包含大量 Base64 图片的合并转发可能失败；此前已撤销图片推送实现。未经明确设计与限流，不要重新加入“每条商品一张图片”。
3. 不要安装旧 `mercari` 包到 AstrBot 插件依赖。插件已经使用 AstrBot Core 自带的 `requests` 与 `cryptography` 直连 API。
4. `last_check_time` 与 `last_scheduled_slot` 含义不同；定时是否到期只能依据后者。
5. 默认只向发现新品的关键词发送消息；“已检查但没有新品”会在日志/数据库体现，不会私聊打扰用户。
