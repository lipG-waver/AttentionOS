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
