# AstrBot Mercari 新品监控插件

面向**私聊用户**的 Mercari 日本站关键词订阅插件：每位用户使用一份独立 SQLite 数据库，每小时只推送从未见过的新品；不会推送降价或重复商品。

## 安装

将此目录复制或重命名为 `AstrBot/data/plugins/astrbot_plugin_mercari_monitor/`，或在 AstrBot WebUI 的插件管理中从本地安装。AstrBot 会根据 `requirements.txt` 安装依赖。

安装后的数据位于：

```text
data/plugin_data/astrbot_plugin_mercari_monitor/users/<SHA-256(UMO)>.db
```

数据库文件名是用户私聊会话 UMO 的 SHA-256，不暴露平台用户 ID。每份库都包含订阅和已见商品记录。

## 配置

在 WebUI 的插件配置中调整：

- `check_interval_minutes`：检查间隔，默认 `60`。
- `search_result_limit`：每个关键词读取的最新商品数，默认 `50`。
- `max_push_items`：一次消息展开的新品数，默认 `5`；未展开商品仍记为已见。
- `check_on_startup`：启动后是否立即运行一次检查，默认关闭。

## 指令

```text
/mercari 搜索 <关键词>
/mercari 订阅 <关键词>
/mercari 刷新 <关键词>
/mercari 订阅列表
/mercari 取消订阅 <关键词>
```

`订阅` 会先将当前搜索结果写入基线，**不会推送历史商品**。之后的定时检查和 `刷新` 都只推送新品。

## 定时推送机制

插件启动时在 AstrBot 的事件循环创建一个调度任务。任务每隔配置的时长汇总所有到期订阅，同一关键词只请求一次 Mercari，再分别和每个用户的独立库比对。对发现新品的用户，插件使用已保存的 `unified_msg_origin` 直接调用 AstrBot 的 `context.send_message()` 私聊推送。

因此不经过 LLM，也不是由插件发送一条“通知 Bot”的中间消息。AstrBot 进程需要持续运行；进程重启后插件会重新创建调度任务，SQLite 中的订阅和已见记录会保留。

## 本地测试

```powershell
python -m pytest tests -q
```

## 限制

- 仅支持私聊；群聊调用会拒绝执行。
- 目标平台必须支持 AstrBot 的主动消息发送能力。
- Mercari 上游服务不可用时，该轮不写入检查结果，下一轮会继续重试。
