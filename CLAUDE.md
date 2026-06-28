# CLAUDE.md — Josan Fork 项目上下文

> 本文件供 Claude Code 在新会话中快速重建上下文，记录架构决策、指令行为规范和已知技术陷阱。

---

## 一、项目定位

Telegram 媒体下载 / 转发机器人，Fork 自 tangyoha/telegram_media_downloader。
本 Fork 在原项目基础上增加了若干自定义转发指令（详见 README.md）。

运行方式：`python -m utils.meta_data` 或直接 `python main.py`，通过 Telegram Bot 接收命令。

---

## 二、双客户端架构（必读）

项目同时维护两个 Pyrogram 客户端，混淆它们是最常见的 bug 来源：

| 变量 | 类型 | 身份 | 用途 |
|------|------|------|------|
| `_bot.client` | `HookClient`（继承自 `pyrogram.Client`） | **用户账号**（session 文件） | 读取频道消息、获取媒体组、下载媒体 |
| `_bot.bot` | `pyrogram.client.Client` | **Bot Token 账号** | 接收命令、发送反馈消息给用户 |
| `node.upload_user` | `_bot.bot` 或 `_bot.client` | 取决于权限 | 向目标频道发送主帖 |

`node.upload_user` 的赋值逻辑（在 `get_forward_task_node`）：
- 默认 = `_bot.client`（用户账号）
- 若 bot 在目标频道有发送权限 → 改为 `_bot.bot`
- 判断依据：启动日志中有无"Note that the robot"警告；有警告说明 bot 没权限

**关键规则**：读消息用 `_bot.client`，向目标频道发帖用 `node.upload_user`。

---

## 三、自定义指令行为规范

### `/forward_screenshot`

处理粒度：**逐条消息独立处理**。

| 消息类型 | 行为 |
|---------|------|
| 单条视频 | 缩略图 → 主帖；视频 → 评论区 |
| 单条图片/文字/文档 | 普通转发（`item.copy()`） |
| 媒体组（多条合并） | **跳过**；任务结束后向用户发一条汇总反馈，列出被跳过的消息 ID |

实现位置：`bot.py` → `forward_message_impl` 循环内，遇到 `item.media_group_id` 时加入 `skipped_screenshot_ids`，循环结束后发送反馈。

---

### `/forward_multi`

处理粒度：**逐条消息 / 逐媒体组独立处理**（每组独立生成一个主帖，不跨组合并）。

子模式通过 InlineKeyboard 选择，存储在 `node.forward_multi_single_thumb`：
- `True` = 单缩略图模式
- `False` = 多缩略图模式

#### 完整行为表

| 消息类型 | 单缩略图（`single_thumb=True`） | 多缩略图（`single_thumb=False`） |
|---------|-------------------------------|-------------------------------|
| 单条视频 | 缩略图 → 主帖；视频 → 评论区 | 同左 |
| 单条图片/文字/文档 | 普通转发 | 普通转发 |
| 媒体组（纯视频） | 第一个视频缩略图 → 主帖；全部视频 → 评论区 | 全部视频缩略图相册 → 主帖；全部视频 → 评论区 |
| 媒体组（图片+视频）| 看第一项：图片→用图片，视频→用缩略图，作为主帖；**全部**图片+视频 → 评论区 | 仅图片相册 → 主帖；全部视频 → 评论区（视频缩略图不进主帖，图片不进评论区） |
| 媒体组（纯图片） | 第一张图片 → 主帖；全部图片 → 评论区 | 全部图片相册 → 主帖；评论区无内容 |

#### 实现架构

- 循环内遇到 `item.media_group_id` 时，用 `seen_multi_groups` set 去重，只在第一次遇到该 group_id 时调用 `get_media_group` 获取全组消息，然后调用 `process_multi_group()`
- 单条消息调用 `process_multi_single_msg()`
- 两个函数均在 `module/pyrogram_extension.py` 中
- **不使用 buffer**，不需要 `finalize_forward_multi`

---

### `/forward_album`

处理粒度：**buffer 模式**，范围内所有消息收集完后一次性处理。

- 每 10 条媒体为一个批次，调用 `send_media_group` 发出
- 视频需要本地下载 + 重新上传（含转码检测）
- 在 `finalize_forward_multi` → `_flush_album_mode` 中实现
- `node.forward_multi_buffer` 存储所有待处理消息

---

### `/forward_clone`

处理粒度：逐条消息独立处理，额外读取每帖的讨论区评论并复制。
实现在 `module/pyrogram_extension.py` → `forward_clone_impl`。

---

## 四、关键已知技术陷阱

### 1. `SendMultiMedia` vs `SendMedia` 的 MEDIA_FILE_INVALID 问题

**现象**：`send_media_group()` 发送本地上传的图片（含通过 `messages.UploadMedia` 预注册的）时，Telegram 返回 `MEDIA_FILE_INVALID`。但 `send_photo()` 发送同一张图片完全正常。

**根本原因**：
- `send_photo` → `messages.SendMedia` → 接受 `InputMediaUploadedPhoto`（新上传）
- `send_media_group` → `messages.SendMultiMedia` → 拒绝 `InputMediaUploadedPhoto`，只接受已登记的 `InputMediaPhoto`（含 access_hash + file_reference）

**已尝试但失败的方案**：
- 下载缩略图到本地 → `send_media_group(InputMediaPhoto(local_file))` → FAILS
- `cache_media()` 预上传 → `send_media_group_v2(pre_registered)` → FAILS

**当前方案**：直接使用 `video.thumbs[-1].file_id`，这是 Telegram 已存储的照片，`InputMediaPhoto(media=file_id)` 引用的是服务端现有文件，绕过上传，避开 `InputMediaUploadedPhoto`。

> 如果 `file_id` 方案也失败，需要另寻方案（例如把缩略图作为 document 上传，再引用其 file_id）。

### 2. `send_media_group` 需要至少 2 个元素

Pyrogram 对 `send_media_group` 要求媒体列表长度 ≥ 2。单元素时必须改用 `send_photo` / `send_video`。代码中已用 `len(media_list) == 1` 分支处理。

### 3. 媒体组去重

`get_chat_history_v2` 会对媒体组内的每条消息逐一返回。在循环中必须用 `seen_groups` set 去重，只在第一次遇到某 `media_group_id` 时处理整组，避免同一媒体组被处理多次。

### 4. file_reference 过期

Telegram 的 `file_reference` 有效期有限，在 buffer 模式中累积消息后批量发送时可能过期。解决方案：发送前调用 `client.get_messages(chat_id, [ids])` 批量刷新。在 `process_multi_group` 函数开头已做刷新。

### 5. `temp_files` 初始化

`_flush_album_mode` 中下载视频/文档到本地后，所有临时路径需追加到 `temp_files` 列表，函数结束时统一删除。`temp_files = []` 必须在函数最开头初始化，否则 `NameError` 会被 except 静默吞掉，导致所有视频下载静默失败。

### 6. 双计数体系 / multi 任务的完成判定与停止

`TaskNode` 有两套**互不相通**的计数：

- **下载体系**：`total_task`（仅 `add_download_task` +1）/ `total_download_task`（仅 `node.stat()` +1）。这是 `is_finish()` 的**唯一判据**。
- **转发统计**：`total_forward_task` / `success|failed|skip_forward_task`，由 `node.stat_forward()` 更新，**仅供 `report_bot_status` 显示**，不参与 `is_finish()`。

multi / screenshot / album / clone 路径只调 `stat_forward()`，**从不更新 `total_task`**，故这些任务 `total_task` 恒为 0。

**陷阱**：`is_finish()` 若用 `total_task == total_download_task`（0 == 0）会把运行中的 multi 任务误判为"已完成"，后台 `Application.update_reply_message`（每 3 秒）据此 `remove_task_node`，导致 ①任务几秒后从 `_bot.task_node` 消失 → Stop Forward 菜单列不出 → **无法停止**；②进度反馈停更。修复：`is_finish()` 加 `total_task > 0` 前置条件（已落地 `app.py`）。运行中 multi 任务因此 `is_finish()=False` 不被清理；正常结束时 `forward_message_impl` 的 finally 调 `node.stop_transmission()` 使其 `is_finish()=True` 被正常清理。

**停止响应粒度**：`forward_message_impl` 主循环在每条消息/每组**处理前后**检查 `node.is_stop_transmission`（`bot.py`），`process_multi_single_msg` / `process_multi_group` 入口也各有一道检查。但单个媒体组**内部**（评论区逐条上传）不中断，故 stop 最多延迟"一个媒体组的处理时间"才生效——这是正常现象，不是没停。

### 7. `get_chat_history_v2` 分页陷阱（reverse 模式）

- `node.limit = end_offset_id - offset_id + 1`（`bot.py` `get_forward_task_node`）是 **ID 跨度**，不是真实消息数；它被当作迭代器的 `total` 上限。频道有 ID 空洞（删除/服务消息）时真实消息数 < 跨度。
- reverse 模式到达范围/频道末尾后，`GetHistory` 因 `add_offset` 负偏移被服务端 **clamp**，会**重复返回最新窗口而非空列表**，`offset_id` 不再前进 → 仅靠 `current >= total` 兜底，期间会**反复转发最后一批**。
- 修复（已落地 `get_chat_history_v2.py`）：reverse 模式下若本轮 `messages[-1].id < 请求起点` 则 `return`（stall detection）；并对 yield 的消息按 `[start_id, max_id]` 过滤，防止空洞锚点漂移泄漏越界消息。

---

## 五、主要模块职责

| 文件 | 职责 |
|------|------|
| `module/bot.py` | 命令注册、消息路由、`forward_message_impl` 主循环 |
| `module/pyrogram_extension.py` | 所有自定义转发逻辑：`process_multi_single_msg`、`process_multi_group`、`_flush_album_mode`、`forward_clone_impl`、`forward_screenshot_split_group` 等 |
| `module/send_media_group_v2.py` | 低层封装：`cache_media()`（预注册媒体）+ `send_media_group_v2()`（直调 `SendMultiMedia`） |
| `module/app.py` | `TaskNode` 数据类（**注意在 app.py，不在 task_node.py**），存储转发任务全部状态；双计数体系与 `is_finish()`；`Application.update_reply_message` 后台清理循环 |
| `utils/format.py` | 文本格式化工具 |

---

## 六、TaskNode 关键属性速查

```python
node.chat_id                  # 源频道 ID
node.upload_telegram_chat_id  # 目标频道 ID
node.topic_id                 # 目标频道话题 ID（None 表示无话题）
node.upload_user              # 发帖客户端（bot 或用户账号，取决于权限）
node.forward_multi_mode       # bool: /forward_multi 模式
node.forward_multi_single_thumb  # bool: True=单缩略图, False=多缩略图
node.forward_album_mode       # bool: /forward_album 模式
node.forward_video_screenshot # bool: /forward_screenshot 模式
node.forward_multi_buffer     # list: album 模式的消息缓冲区
node.forward_multi_captions   # list: album 模式的文案列表
node.bot                      # bot 客户端引用（用于 report_bot_status）
node.from_user_id             # 发令用户的 chat_id（用于反馈消息）
node.reply_message_id         # 状态消息 ID（实时更新进度）
```
