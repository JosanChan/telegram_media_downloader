# Telegram Media Downloader — Josan Fork

> 本项目 Fork 自 [tangyoha/telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader)。
>
> 原项目包含完整的下载、转发、过滤、Web UI、Docker 部署等功能说明，**更多基础功能详见原项目文档**。
>
> 本文档仅记录本 Fork 新增 / 修改的内容。

---

## 新增指令说明

本 Fork 在原项目基础上新增了 4 条自定义 Bot 指令，均通过 Bot 发送，格式统一为：

```
/指令名 源频道链接 目标频道链接 起始消息ID 结束消息ID [过滤条件]
```

---

### 1. `/forward_screenshot` — 截图转发

**功能**

将源频道的视频消息以"封面图发主帖、视频发评论区"的形式转发到目标频道，模拟频道常见的缩略图展示风格。

**效果**

- 目标频道主帖：视频封面图（thumbnail）+ 原标题 / 文案
- 目标频道讨论组（评论区）：原始视频文件

**适用场景**

源频道是普通视频消息，希望在目标频道以"图片预览 + 评论区视频"的形式展示。

**使用方法**

```
/forward_screenshot https://t.me/源频道 https://t.me/目标频道 起始ID 结束ID
```

**示例**

```
/forward_screenshot https://t.me/src_channel https://t.me/dst_channel 100 200
```

> 注意：目标频道必须开启讨论组（linked discussion group），否则视频无处投放。

---

### 2. `/forward_multi` — 多视频截图批量转发

**功能**

批量将多条视频消息以截图模式转发，支持两种子模式，通过弹出的内联按钮选择：

| 模式 | 说明 |
|------|------|
| **单图模式（Single）** | 每条视频单独生成一个封面图帖子 + 评论区视频 |
| **多图模式（Multi）** | 将多条视频的封面图合并为一个媒体组帖子，视频统一发到评论区 |

**使用方法**

```
/forward_multi https://t.me/源频道 https://t.me/目标频道 起始ID 结束ID
```

发送命令后，Bot 会弹出模式选择按钮，点击选择即可开始处理。

**示例**

```
/forward_multi https://t.me/src_channel https://t.me/dst_channel 100 200
```

> 注意：不支持源频道开启了"转发限制"（protected content）的情况。

---

### 3. `/forward_album` — 多视频合并媒体组转发

**功能**

将多条视频消息打包合并为一个 Telegram 媒体组（Album）发送到目标频道，所有视频作为一个帖子呈现。

**效果**

- 目标频道：一个包含多个视频的媒体组帖子（最多 10 个）
- 保留原始视频文件和标题

**适用场景**

需要把多个相关视频合为一组发布，避免频道被大量单条帖子刷屏。

**使用方法**

```
/forward_album https://t.me/源频道 https://t.me/目标频道 起始ID 结束ID
```

**示例**

```
/forward_album https://t.me/src_channel https://t.me/dst_channel 100 110
```

> 注意：不支持源频道开启了"转发限制"的情况。

---

### 4. `/forward_clone` — 克隆频道（保留讨论区结构）

**功能**

将源频道的帖子**连同其讨论区评论**一起复制到目标频道，完整还原原频道的内容结构。

专为以下场景设计：源频道的帖子是"图片 / 封面图 + 评论区视频"结构，克隆后目标频道也保持同样的结构。

**处理逻辑**

1. 逐条读取源频道指定范围内的帖子
2. 将主帖（图片、视频、文字等）复制到目标频道
3. 读取该帖在源频道讨论组中的所有评论
4. 将这些评论复制到目标频道的讨论组，作为对应新帖的回复

**效果**

- 源频道：`[图片帖]` → 评论区：`[视频1]` `[视频2]`
- 目标频道：`[图片帖（复制）]` → 评论区：`[视频1（复制）]` `[视频2（复制）]`

**使用方法**

```
/forward_clone https://t.me/源频道 https://t.me/目标频道 起始ID 结束ID
```

**示例**

```
/forward_clone https://t.me/src_channel https://t.me/dst_channel 100 200
```

**前提条件**

- 源频道和目标频道都需要有已关联的讨论组（linked discussion group）
- 源频道不能开启"转发限制"（protected content）
- Bot 需要有目标频道及其讨论组的发送权限

---

## 通用说明

### 消息 ID 获取方式

在 Telegram Web（或桌面端）打开频道，鼠标悬停在帖子上，URL 末尾的数字即为消息 ID。

### 过滤条件（可选参数）

所有指令均支持在末尾添加过滤条件，语法与原项目 `/forward` 指令一致，例如：

```
/forward_clone https://t.me/src https://t.me/dst 1 500 message_date >= 2024-01-01
```

### 停止任务

发送 `/stop` 可中断当前正在执行的任务。

---

## 原项目文档

完整的安装、配置、Docker 部署、下载功能、过滤器语法等说明请参阅原项目：

**[https://github.com/tangyoha/telegram_media_downloader](https://github.com/tangyoha/telegram_media_downloader)**
