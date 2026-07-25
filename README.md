# astrbot_plugin_live_monitor

AstrBot 多平台直播监控插件 · 指令前缀 **`/livecheck`**

监控 **虎牙 / 斗鱼 / 抖音 / B站** 开播与关播，通过 `context.send_message` **主动推送**到订阅群/私聊。

默认二次元文案（「xxx 开始直播了喵～」），封面图可选，LLM 开播文案可选。

## 功能

- 四平台状态轮询 + 开播/关播推送
- 主动消息（保存 `unified_msg_origin`）
- 封面图、@全体、会话静音
- 自定义昵称
- 可选 LLM 文案（可指定 provider）
- `/livecheck testpush` 验证推送通道

## 安装

1. 解压到 `AstrBot/data/plugins/astrbot_plugin_live_monitor`
2. 依赖：`httpx>=0.27.0`
3. 重载插件

## 在设置里添加直播间（推荐）

打开 **AstrBot 管理面板 → 插件 → 多平台直播监控 → 配置**：

1. **监控直播间（点添加）**  
   - 点添加 → 选平台 → 填房间号 → 可选昵称  
2. **批量添加直播间（可选）**  
   - 每行：`平台 房间号 [昵称]`  
   - 例：`bilibili 6 英雄联盟赛事`  
3. **推送目标（必填才能推到群）**  
   - 每行一个 QQ **群号**即可，如 `123456789`  
   - 在对应群里发过任意 `/livecheck` 后会自动匹配会话前缀  
4. 保存配置；下一轮轮询会自动同步，或发 `/livecheck sync` 立即同步  

> 只加直播间不填推送目标 = 监控了但没地方喊你。

## 指令速查

| 指令 | 说明 |
|------|------|
| `/livecheck help` | 帮助 |
| `/livecheck add <平台> <房间号> [昵称]` | 添加并订阅 |
| `/livecheck del <平台> <房间号>` | 取消订阅 |
| `/livecheck remove <平台> <房间号>` | 管理员强制删除 |
| `/livecheck list` / `list all` | 列表 |
| `/livecheck nickname <平台> <房间号> <昵称>` | 改昵称 |
| `/livecheck check` / `check <平台> <房间号>` | 列出正在直播 / 立刻查询 |
| `/livecheck atall / mute ...` | @全体 / 静音 |
| `/livecheck sync` | 立即从插件设置同步直播间 |
| `/livecheck testpush` | 测试主动推送 |
| `/livecheck status` | 状态 |
| `/livecheck poll` | 管理员立即轮询 |

平台：`bilibili/b站` `douyu/斗鱼` `huya/虎牙` `douyin/抖音`  
房间号可直接粘贴直播间链接（自动提取）。  
抖音房间号 = `live.douyin.com/<web_rid>` 中的数字。

## 默认开播模板

```text
✨ {nickname} 开始直播了喵～
━━━━━━━━━━━━
📺 从 {platform} 结界传送过来的信号
🎬 本场标题：{title}
🆔 主播房间：{room_id}
⏰ 开播时刻：{time}
🔗 传送门：{url}
━━━━━━━━━━━━
封面已加载好啦，速速入场围观捏！
```

## 配置要点

| 项 | 默认 | 说明 |
|----|------|------|
| `check_interval` | 60 | 轮询秒数 |
| `enable_cover` | true | 封面 |
| `notify_offline` | true | 关播通知 |
| `enable_llm` | **false** | 可选 LLM 开播文案 |
| `llm_provider_id` | 空 | 下拉选择已配置的提供商，空=默认 |
| `live_template` / `offline_template` | 二次元模板 | 可自定义 |
| `llm_prompt` | 二次元猫娘提示词 | 仅 LLM 开启时 |

变量：`{platform}{nickname}{real_name}{title}{room_id}{url}{time}{duration}`

## 版本

- **v1.4.0** 轮询健壮性：瞬时错误指数退避重试；连续失败房间冷却退避；轮询刷新批量落盘；轮询加抖动  
  设置页优化：LLM 提供商改用原生下拉（`select_provider`）；平台下拉带中文名（`labels`）；LLM 字段按开关条件显示（`condition`）  
  采集/输出：支持粘贴直播间链接；封面 URL 规范化；推送封面失败回退纯文本；多会话并发推送与发送重试
- v1.3.0 插件设置页可添加直播间 + 推送目标群；`/livecheck sync`
- v1.2.0 二次元默认文案与 LLM prompt；主动推送加固
- v1.1.0 前缀 livecheck；LLM 可选
- v1.0.0 首发
