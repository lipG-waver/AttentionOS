# AttentionOS — 给 Claude 的产品哲学与开发指南

## 核心哲学：一切皆对话

AttentionOS 的第一原则是**对话即界面**。用户与系统的一切交互，应当优先流经悬浮对话框（`ChatOverlay`），而非弹窗、表单或独立窗口。

### 什么是"一切皆对话"

**是：** 用自然语言表达意图，系统理解意图并执行，不需要用户学习操作路径。

**不是：** 把所有功能都改成聊天输入框形式。对话是交互范式，不是视觉皮肤。

真正的对话具备：
- 双向性：系统与用户都可以主动发起
- 上下文性：记得之前说过什么
- 模糊容忍：能处理不精确的表达，如"等会儿开个番茄"
- 最小打断：不到必要时刻，不中断用户的心流

---

## 设计约束（强制）

### 1. 禁止新增独立弹窗

任何新功能需要向用户提问或通知时，**必须通过 `ChatOverlay` 实现**，不得新增：
- `tkinter` 对话框
- `AppleScript` `display dialog`
- `zenity` / `kdialog` 弹窗
- 独立 `Toplevel` 窗口

唯一例外：全屏强制休息遮罩（`BreakOverlay`），这是功能本质，不是交互方式。

### 2. 禁止新增斜杠命令

`/goals`、`/pomodoro start` 这类命令是 CLI 思维的残留。新功能应当通过自然语言理解处理，或在 `DialogueAgent` 中扩展意图识别，不得新增斜杠命令。

### 3. 功能减法优先

当考虑添加新功能时，先问：
> 这个功能是否能通过已有的对话机制完成？

如果是，不要单独建模块。每新增一个功能入口（Modal、Tab、弹窗），就是对"一切皆对话"的一次背叛。

---

## 对话优先的实现模式

### 主动发起对话（系统 → 用户）

使用 `ChatOverlay._send_ai_message(text, msg_type)` 推送消息。

```python
from attention.ui.chat_overlay import get_chat_overlay
overlay = get_chat_overlay()
overlay._send_ai_message("⏰ 整点了，过去一小时你在做什么？", msg_type="checkin")
```

### 等待用户回复（带回调）

使用 `ChatOverlay.show_checkin_prompt(callback)` 注册一次性回调，下一条用户消息将路由到该回调，而非 `DialogueAgent`。

```python
def on_user_reply(text: str):
    # 处理用户回复
    entry.doing = text
    entry.category = infer_category(text)
    entry.feeling = infer_feeling_from_text(text)

overlay.show_checkin_prompt(on_user_reply)
```

### 降级策略

当 `ChatOverlay` 不可用时（`not overlay.is_ready()`），可以降级使用系统原生对话框。但降级是例外，不是默认。

---

## 产品边界（不做什么）

以下是有意识的取舍，不要试图"补全"它们：

| 不做 | 原因 |
|------|------|
| 多用户支持 | 注意力管理是极度个人化的，多用户会稀释产品焦点 |
| 云同步 | 数据本地存储是信任基础，云同步破坏这个承诺 |
| 移动端 | 桌面是深度工作的主战场，分散到移动端只会产生干扰 |
| 团队协作功能 | 这是个人工具，不是项目管理软件 |
| 复杂的图表 Dashboard | Dashboard 是辅助阅读的，不是主交互入口 |
| 系统级推送通知（macOS Notification Center / Windows Toast） | 是独立弹窗的变体——用户看到一个通知气泡，需要决策是否点击，这和 `display dialog` 本质相同，都在破坏心流 |
| 周报/月报的独立 Dashboard 页面 | 用户不会主动去看；洞察必须由系统在合适时机（如周一早晨）主动推入对话，而不是等待用户导航到页面 |
| 带独立 UI 的插件 | 每个插件不得新增自己的设置页、弹窗或 Tab；插件的一切用户交互必须走 ChatOverlay 对话 |

---

## 已知的哲学性技术债

以下模块与"一切皆对话"哲学存在冲突，未来需要逐步重构：

1. **`hourly_checkin.py` 的平台弹窗**（`show_checkin_dialog_macos/windows/linux`）
   - 现状：使用 AppleScript / tkinter / zenity 弹出独立窗口
   - 目标：通过 `ChatOverlay.show_checkin_prompt()` 在悬浮框内完成

2. **Web Dashboard 的 Settings Tab**
   - 现状：用 HTML 表单修改配置
   - 目标：通过对话完成配置，如"把提醒间隔改成45分钟"

3. **斜杠命令系统**
   - 现状：`/pomodoro start`、`/rest 15` 等
   - 目标：`DialogueAgent` 的意图识别覆盖这些命令

4. **`desktop_overlay.py` 的 `show_intervention()`**（最直接的违规）
   - 现状：`show_intervention()` 内部调用 `_show_intervention_dialog_macos/windows/linux`，分别用 AppleScript `display dialog` / Windows `MessageBoxW` / zenity 弹出"注意力提醒"对话框——正是 Constraint #1 明确禁止的三种形式
   - 目标：通过 `ChatOverlay._send_ai_message()` 推送介入消息，`DesktopOverlay` 只做状态追踪，不直接弹窗

5. **Web Dashboard 的 Plugins Tab**
   - 现状：用 HTML 表单启用/禁用/配置插件（`PluginManager` 对外暴露配置 API）
   - 目标：通过对话完成，如"帮我启用声音提醒插件"、"把 webhook 地址改成 xxx"

## 待清理的遗留模块

以下模块的核心功能已被更符合哲学的实现替代，但文件仍然存在，**不应再向其添加功能**：

| 模块 | 已被替代为 | 说明 |
|------|-----------|------|
| `attention/ui/pomodoro_overlay.py` | `ChatOverlay` 的计时器区域 | `PomodoroTimer._init_floating_overlay()` 已改为集成 ChatOverlay，旧浮窗类已不再启动 |
| `attention/ui/pomodoro_overlay_process.py` | 同上 | 旧浮窗 GUI 子进程，已无调用方 |
| `DesktopOverlay` 的小精灵状态机（`PetState`、`update_mood`）| Web 前端渲染 | macOS/Linux 主循环都只是 `while True: sleep(1)`，小精灵视觉层从未真正运行；状态数据仅通过 WebSocket 传到前端 |

---

## 关键文件导航

| 职责 | 文件 |
|------|------|
| 对话悬浮窗（父进程） | `attention/ui/chat_overlay.py` |
| 对话悬浮窗（GUI子进程） | `attention/ui/chat_overlay_process.py` |
| 对话 Agent | `attention/core/dialogue_agent.py` |
| 每小时签到 | `attention/features/hourly_checkin.py` |
| 每日简报 | `attention/features/daily_briefing.py` |
| 状态融合 | `attention/core/state_fusion.py` |
| 配置 | `attention/config.py` |

---

## 开发原则

- **改之前先读**：不要在没有完整读完相关模块的情况下提议修改
- **最小改动**：只修改完成当前任务所必需的代码
- **保持降级路径**：新的对话交互路径要有 fallback，避免当 ChatOverlay 未启动时功能完全不可用
- **测试签到流程时**：调用 `HourlyCheckin.trigger_now()` 手动触发，不要修改时间间隔来测试
