# Attention OS v4.0 — 对话即入口（Chat-First Refactoring）

## 核心变更：统一对话悬浮窗

### 设计理念

将 7 种碎片化交互形态（AppleScript 弹窗、桌面小精灵、番茄钟浮窗、全屏遮罩、Web Dashboard、系统托盘、Coach 推送）收敛为 **一个对话窗口**。

用户与 Attention OS 的所有交互通过一种认知模型完成：**跟注意力教练对话**。

### 两种形态

```
收起态（Ball）                     展开态（Chat）
┌──────┐                          ┌─────────────────────────┐
│ 24:30│ ← 计时器进度环            │ ● Attention OS    24:30 │
│  专注 │                          ├─────────────────────────┤
│  🔴  │ ← 未读消息标记            │                         │
└──────┘                          │ 🎯 专注模式已开启        │
  点击展开                         │ — 写 SOP 文档（25分钟）  │
                                  │                         │
                                  │ 💭 你: 明天发邮件给李老师 │
                                  │ 📌 已记录！继续专注 💪    │
                                  │                         │
                                  │ 👀 好像在刷微博...       │
                                  │ 遇到什么阻力了吗？       │
                                  │                         │
                                  ├─────────────────────────┤
                                  │ [输入想法...]         ↑  │
                                  └─────────────────────────┘
```

---

## 新增文件

| 文件 | 职责 |
|------|------|
| `attention/core/dialogue_agent.py` | 对话引擎：多轮上下文管理、思维捕捉、主动提醒生成、命令路由 |
| `attention/ui/chat_overlay.py` | 对话悬浮窗管理器：子进程管理、消息路由、日志保存 |
| `attention/ui/chat_overlay_process.py` | 对话悬浮窗子进程：tkinter GUI、小球/聊天切换、拖动 |
| `attention/features/chat_logger.py` | 对话日志：导出为 Markdown 文件，含思维捕捉汇总 |

## 修改文件

| 文件 | 变更 |
|------|------|
| `attention/main.py` | `_handle_intervention` 和 `_check_goal_deviation` 改用 `chat_overlay.show_nudge()` |
| `attention/ui/tray_app.py` | `_start_background_services` 启动 `chat_overlay` 替代 `desktop_overlay` |
| `attention/ui/web_server.py` | 新增 `/api/chat/*` 端点；overlay API 改用 `chat_overlay` |
| `attention/features/pomodoro.py` | 浮窗集成改用 `chat_overlay.update_timer()`；专注开始/结束发送对话消息 |
| `attention/features/break_reminder.py` | 休息提醒改用 `chat_overlay.show_break_reminder()` |
| `attention/core/agents.py` | 新增 `dialogue` Agent prompt |

## 保留文件（未删除，作为 fallback）

| 文件 | 说明 |
|------|------|
| `attention/ui/desktop_overlay.py` | 原桌面小精灵，保留作为参考 |
| `attention/ui/pomodoro_overlay.py` | 原独立番茄钟浮窗，保留作为参考 |
| `attention/ui/pomodoro_overlay_process.py` | 原番茄钟子进程，保留作为参考 |
| `attention/ui/break_overlay_process.py` | 原全屏遮罩子进程，保留作为参考 |

---

## 架构对比

### Before（v3.3）
```
分心检测 → AppleScript 弹窗（单向，2 个按钮）
番茄钟   → 独立浮窗子进程（按钮控制器）
休息     → 全屏遮罩子进程（倒计时 + 跳过）
签到     → AppleScript 弹窗（表单输入）
提醒     → Coach Agent 生成文本 → 系统通知
```

### After（v4.0）
```
分心检测 → 对话窗口主动开启对话（共情 → 追问原因 → 轻推）
番茄钟   → 小球显示计时 + 进度环，点击展开输入想法
休息     → 对话窗口温和提醒，用户自主决定
签到     → 不再需要！对话过程本身就是自我报告
提醒     → Dialogue Agent 生成对话（带上下文）
```

---

## 关键交互场景

### 1. 专注时思维捕捉（最高优先级）
```
[用户正在专注"写 SOP 文档"，小球显示 18:30]
[用户点击小球展开对话窗]
用户: 明天要给李老师发邮件确认会议时间
AI: 📌 已记录！继续专注，还剩 18 分钟 💪
[对话窗自动收起/用户手动收起]
```
- **不调用 LLM**，本地秒回
- 想法存入 pending_thoughts 列表
- 专注结束后自动汇总

### 2. 分心介入对话
```
[系统检测到用户偏离目标 12 分钟]
[小球显示 👀 表情 + 红色未读标记]
[对话窗自动展开]
AI: 你好像在刷微博，距离「写 SOP」已经偏离 12 分钟了。遇到什么阻力了吗？
用户: 写不下去了，不知道怎么组织结构
AI: 要不先列个大纲？从最容易的部分开始？
```
- LLM 生成（带完整上下文）
- 冷却期 120 秒

### 3. 对话日志 Markdown 导出
```markdown
# 📓 Attention OS 对话日志 — 2026-02-14

## 💬 对话记录

### ⏰ 09:00
> 📢 *09:00* — ☀️ 早上好！准备好开始高效的一天了吗？

### ⏰ 09:15
> 📢 *09:15* — 🎯 专注模式已开启 — 写 SOP 文档（25分钟）
💬 *09:22* **你**: 明天要给李老师发邮件
💬 *09:22* **Attention OS**: 📌 已记录！继续专注，还剩 18 分钟 💪

## 💭 思维捕捉
- **09:22** — 明天要给李老师发邮件确认会议时间
```

---

## API 新增

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/chat/send` | POST | 发送用户消息，返回 AI 回复 |
| `/api/chat/history` | GET | 获取对话历史 |
| `/api/chat/export` | POST | 导出今日对话为 Markdown |

---

## 下一步

1. **Web Dashboard 集成**：在前端 `index.html` 添加侧边栏 Chat Panel（通过 WebSocket 实时同步）
2. **语音输入**：对话窗口集成 SenseVoice 语音按钮
3. **智能路由**：Dialogue Agent 自动识别用户意图，路由到 Todo/Pomodoro/Briefing 等子系统
4. **每日回顾自动生成**：基于对话日志 + 监控数据生成更丰富的回顾报告
