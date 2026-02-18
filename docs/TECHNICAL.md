# Attention OS v3.3 — 技术文档

## 1. 系统总览

### 1.1 设计理念

Attention OS 的核心假设是：**单纯的数据记录不能改变行为，只有在行为发生时的精准干预才能。**

基于这个假设，系统设计了三层递进的干预策略：

1. **意图声明**（Briefing）— 让用户在开工前明确"今天要做什么"，为后续干预提供依据
2. **实时干预**（Task-aware Nudge）— 在用户偏离意图时，引用具体任务名和上下文提醒
3. **反思闭环**（Evening Review）— 用客观数据对照主观计划，帮助用户校准自我认知

### 1.2 架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                           用户界面层                                  │
├──────────────┬──────────────┬──────────────┬────────────────────────┤
│ Web 仪表盘    │ Briefing     │ 系统托盘      │ 桌面悬浮窗/介入弹窗     │
│ (index.html) │ 弹窗         │ (pystray)    │ (desktop_overlay)      │
│ FastAPI +    │ (modal)      │              │ AppleScript/ctypes/    │
│ Chart.js     │              │              │ zenity                 │
└──────┬───────┴──────┬───────┴──────┬───────┴────────────┬───────────┘
       │              │              │                    │
       ▼              ▼              ▼                    ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           业务逻辑层                                  │
├──────────┬───────────┬──────────┬──────────┬──────────┬─────────────┤
│ 状态融合  │ Daily     │ 番茄钟   │ TodoList │ 签到     │ 回归提醒    │
│ Engine   │ Briefing  │ + Focus  │ + NLP    │ + 晚间   │ Recovery    │
│          │ + Nudge   │ Session  │ 解析     │ 总结     │ Reminder    │
│          │ + Review  │          │          │          │             │
└────┬─────┴─────┬─────┴────┬─────┴────┬─────┴────┬─────┴─────┬───────┘
     │           │          │          │          │           │
     ▼           ▼          ▼          ▼          ▼           ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           数据采集层                                  │
├──────────────┬──────────────────────┬────────────────────────────────┤
│ 截图模块      │ 活动监控模块           │ VL 模型分析                     │
│ (mss)        │ (pynput + 系统API)    │ (Qwen2.5-VL-72B)              │
└──────────────┴──────────────────────┴────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           数据存储层                                  │
├──────────────┬──────────────┬──────────────┬─────────────────────────┤
│ work_logs    │ daily_       │ todos.json   │ work_start_times.json   │
│ .json        │ briefing.json│              │ pomodoro_settings.json  │
└──────────────┴──────────────┴──────────────┴─────────────────────────┘
```

### 1.3 监控主循环

`main.py` → `AttentionAgent._monitor_cycle()` 每 60 秒执行一次：

```
步骤 1  截图              capture_screen()
步骤 2  VL 模型分析        analyze_screen(image_data)
步骤 3  获取活动状态        activity_monitor.get_current_state()
步骤 4  多信号融合          fuse_state(screen + activity + idle)
步骤 5  持久化              save_to_database()
步骤 6  显示结果            _display_result()
步骤 7  更新回归提醒器      recovery_reminder.update_user_state()
步骤 8  检查介入需求        fused.needs_intervention → show_intervention()
步骤 9  任务感知提醒  ★新   daily_briefing.check_off_track(fused)
```

步骤 9 是 v3.3 新增的关键步骤，将被动监控连接到了主动干预。

---

## 2. 核心模块详解

### 2.1 状态融合引擎（`state_fusion.py`）

将 VL 模型的截屏分析与本地活动信号融合，输出 `FusedState`。

#### 应用分类

```python
APP_CATEGORIES = {
    "work":          ["vscode", "pycharm", "word", "excel", "powerpoint", ...],
    "communication": ["slack", "微信", "discord", "teams", "zoom", ...],
    "learning":      ["kindle", "coursera", "arxiv", "知乎", ...],
    "entertainment": ["bilibili", "youtube", "旦挞", "danta", "游戏", ...],
    "browser":       ["chrome", "safari", "firefox", "edge", ...],
}
```

浏览器分类逻辑（v3.2 修复）：
- 有窗口标题 → 按标题关键词分类（bilibili→entertainment, github→work, ...）
- 无窗口标题 → 标记为 `"unknown"`（不再错误地标记为 work）

#### 参与类型判定

| 活动状态 | 应用类别 | 判定结果 |
|----------|----------|----------|
| 有键盘输入 | work | 主动工作 ACTIVE_WORKING |
| 有键盘输入 | communication | 沟通交流 COMMUNICATING |
| 有键盘输入 | entertainment | 被动消费 PASSIVE_CONSUMING |
| 无输入但有鼠标 | work | 阅读思考 READING_THINKING |
| 无输入但有鼠标 | entertainment | 被动消费 PASSIVE_CONSUMING |
| 活动率 = 0 | 任意 | **分心离开 DISTRACTED**（v3.2 修复） |
| 长时间无输入 | 任意 | 分心离开 DISTRACTED |

#### 注意力级别

```
专注 FOCUSED    ← 工作类应用 + 高活动 + 低切换
投入 ENGAGED    ← 工作类应用 + 中等活动
游离 DRIFTING   ← 高切换 或 娱乐类应用
分心 DISTRACTED ← 长时间娱乐 或 频繁切换
离开 AWAY       ← 空闲超时 或 活动率为零
```

#### FusedState 数据结构

```python
@dataclass
class FusedState:
    timestamp: str
    screen_work_status: str        # VL 模型判断的工作状态
    screen_applications: list      # VL 模型检测到的应用
    screen_content_type: str       # 内容类型
    activity_ratio: float          # 活动率 (0.0 ~ 1.0)
    engagement_level: str          # 活动等级
    keyboard_events: int           # 键盘事件数
    mouse_events: int              # 鼠标事件数
    window_switches: int           # 窗口切换次数
    idle_duration: float           # 空闲时长（秒）
    active_window_app: str         # 焦点窗口应用
    active_window_title: str       # 焦点窗口标题
    user_engagement: str           # 参与类型
    attention_level: str           # 注意力级别
    app_category: str              # 应用分类
    is_productive: bool            # 是否生产性
    is_distracted: bool            # 是否分心
    needs_intervention: bool       # 是否需要介入
    intervention_reason: str       # 介入原因
    confidence: float              # 判断置信度
```

---

### 2.2 每日 Briefing 与任务感知提醒（`daily_briefing.py`）

这是 v3.3 的核心新模块，承担三个职责：

#### 2.2.1 每日 Briefing

**数据结构**（按日期存储在 `daily_briefing.json`）：

```json
{
  "2026-02-08": {
    "briefed": true,
    "briefed_at": "08:32:15",
    "goals": [
      {"text": "写完 SOP 文档", "done": false},
      {"text": "review PR #42", "done": true}
    ],
    "dismissed": false,
    "evening_review": { ... }
  }
}
```

**触发逻辑**：前端 `DOMContentLoaded` → `checkBriefing()` → `GET /api/briefing` → 如果 `needs_briefing=true` → 弹出 Briefing Modal。

每日仅触发一次（`briefed=true` 或 `dismissed=true` 后不再弹出）。

#### 2.2.2 任务感知提醒（Nudge）

在 `main.py` 的每个监控周期中调用 `check_off_track(fused_state)`：

```
check_off_track(fused_state) 逻辑流：

1. 有未完成目标吗？
   └─ 否 → return None（不提醒）

2. 当前状态是否偏离？
   ├─ is_distracted = True → 偏离
   ├─ app_category = "entertainment" → 偏离
   ├─ engagement = "被动消费" / "分心离开" → 偏离
   └─ 其他 → 未偏离，计数归零

3. 连续偏离 >= 10 个周期（~10分钟）？
   └─ 否 → return None

4. 冷却期检查（15分钟内不重复提醒）？
   └─ 冷却中 → return None

5. 生成提醒消息（三级优先）：
   ├─ 级别 1: 番茄钟专注中 → "你正在专注「XXX」，还剩N分钟"
   ├─ 级别 2: 今日有 deadline → "今天有任务要交付！"
   └─ 级别 3: 一般偏离 → "你今早定的目标「XXX」还在等你"
```

**关键参数**：
- `_off_track_threshold = 10`：连续 10 个周期（~10分钟）触发
- `_nudge_cooldown = 900`：两次提醒间隔至少 15 分钟
- `_consecutive_off_track`：偏离计数器，非偏离状态时归零

#### 2.2.3 一日回顾

`generate_evening_review()` 聚合四个数据源：

```
daily_briefing.json  →  目标完成状态
database.py          →  今日监控记录（效率、注意力分布）
pomodoro.py          →  专注会话记录
work_start_tracker   →  开工时间
```

**评分算法**：

```python
score = 0
score += goal_completion_rate × 40      # 目标完成权重 40%
score += min(productive_ratio × 40, 40) # 生产率权重 40%（上限 40）
score += min(pomo_count × 5, 20)        # 每个番茄 5 分（上限 20）

# 评级
>= 80 → 🏆 出色
>= 60 → 💪 不错
>= 40 → 🌤 还行
<  40 → 🌱 低谷
```

---

### 2.3 番茄钟 + 专注时段（`pomodoro.py`）

#### 2.3.1 标准番茄钟

```
25分钟工作 → 5分钟休息 → 25分钟工作 → 5分钟休息 →
25分钟工作 → 5分钟休息 → 25分钟工作 → 15分钟长休息（每4个一组）
```

**阶段枚举**：`IDLE → WORKING → SHORT_BREAK/LONG_BREAK → IDLE`

#### 2.3.2 专注任务绑定（v3.3 新增）

```python
class PomodoroTimer:
    _focus_task: Optional[str]       # 当前绑定的任务文本
    _focus_task_source: Optional[str] # "goal" / "todo" / "manual"
    _focus_sessions: list            # 今日完成的专注记录

    def start_work(self, focus_task=None, task_source=None):
        # 绑定任务后开始计时
        ...

    # 工作阶段完成时自动记录
    def _on_phase_complete(self):
        if self._focus_task:
            self._focus_sessions.append({
                "task": self._focus_task,
                "source": self._focus_task_source,
                "duration_minutes": self.settings.work_minutes,
                "completed_at": datetime.now().strftime("%H:%M:%S"),
            })
```

**前端任务选择器**：`loadPomoFocusOptions()` 从两个来源拉取：
- `GET /api/briefing` → 未完成的今日目标
- `GET /api/todos` → deadline 为今天 或 优先级为 urgent/high 的待办

#### 2.3.3 与 Nudge 系统的联动

`daily_briefing.check_off_track()` 会查询番茄钟状态：

```python
from pomodoro import get_pomodoro
pomo_status = get_pomodoro().get_status()
if pomo_status["phase"] == "working" and pomo_status["focus_task"]:
    # 生成带有具体任务名和剩余时间的提醒
    remaining_min = pomo_status["remaining_seconds"] // 60
    message = f"你正在专注「{focus_task}」，还剩 {remaining_min} 分钟"
```

---

### 2.4 活动监控（`activity_monitor.py`）

**采集信号**：

| 信号 | 采集方式 | 采样频率 |
|------|----------|----------|
| 键盘事件 | pynput.keyboard.Listener | 实时 |
| 鼠标点击/移动 | pynput.mouse.Listener | 实时 |
| 鼠标位置 | 轮询 | 1 秒 |
| 焦点窗口 | 系统 API | 1 秒 |

**平台适配**：

| 平台 | 焦点窗口 API |
|------|-------------|
| macOS | `NSWorkspace.sharedWorkspace().frontmostApplication()` |
| Windows | `win32gui.GetForegroundWindow()` → `win32process` |
| Linux | `Xlib.display.Display().get_input_focus()` |

**输出**：`ActivityState` 包含 60 秒窗口内的聚合数据：
- `activity_ratio`：活动率（有事件的秒数 / 总秒数）
- `keyboard_events` / `mouse_events`：事件计数
- `window_switches`：窗口切换次数
- `primary_window_app` / `primary_window_title`：主要使用的窗口

---

### 2.5 桌面介入系统（`desktop_overlay.py`）

**介入触发链**：

```
state_fusion 检测到需要介入
       │
       ▼
main.py → _handle_intervention()
       │
       ▼
desktop_overlay.show_intervention(reason)
       │
       ├─→ 更新 PetState（mood=worried, blink_alert=True）
       │
       └─→ 弹出系统对话框（后台线程）
            ├─ macOS: AppleScript display dialog
            ├─ Windows: ctypes MessageBoxW
            └─ Linux: zenity / kdialog
```

**冷却机制**：`_intervention_cooldown = 120`（2分钟，独立于 Nudge 的 15 分钟冷却）

**对话框按钮**：
- 「马上回去」→ mood=happy，标记用户选择回归
- 「继续摸鱼」→ 隐藏消息，5分钟后再提醒

---

### 2.6 TodoList 自然语言解析（`todo_manager.py`）

**解析优先级**：LLM 解析 → 失败时 fallback 到本地规则引擎。

**本地规则引擎能力**：
- **日期解析**：今天、明天、后天、X天后、下周N、M月D号、YYYY-MM-DD
- **时间解析**：21:30、下午3点、晚上8点半
- **优先级**：紧急/ASAP/urgent → urgent，重要 → high，不急 → low
- **标签**：根据关键词自动分类（工作、学习、生活、会议、健康）

**TodoItem 数据结构**：

```python
@dataclass
class TodoItem:
    id: str                    # 8位 UUID
    title: str                 # 任务标题
    deadline: Optional[str]    # "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM"
    created_at: str
    completed: bool
    completed_at: Optional[str]
    priority: str              # low / normal / high / urgent
    tags: List[str]
```

---

### 2.7 每日开工时间（`work_start_tracker.py`）

**逻辑**：每天 6:00 后首次调用 `record_start()` 时记录。同一天不重复记录。

**存储**：`work_start_times.json`

```json
{
  "2026-02-08": {
    "start_time": "2026-02-08T08:32:15",
    "is_workday": true
  }
}
```

**统计**：`get_history(days=30)` 返回历史数据，分别计算工作日/休息日平均开工时间。

---

## 3. Web 服务（`web_server.py`）

### 3.1 技术栈

- **后端**：FastAPI + Uvicorn（异步）
- **前端**：单文件 `index.html`（约 1950 行），原生 JS + Chart.js
- **实时通信**：WebSocket（`/ws`）
- **静态文件**：FastAPI `StaticFiles`

### 3.2 前端架构

**Tab 结构**：

| Tab | ID | 默认 | 功能 |
|-----|----|------|------|
| 📊 仪表盘 | tab-dashboard | | 图表、效率数据、开工时间 |
| ⏰ 签到 | tab-checkin | | 每小时签到、晚间总结 |
| 🍅 番茄钟 | tab-pomodoro | | 专注时段、任务选择、专注日志 |
| 📋 任务 | tab-todo | ✅ | TodoList + 今日目标面板 |
| ⚙️ 设置 | tab-settings | | 外观、报告、签到配置 |

**弹窗 Modal**：

| Modal | 触发 |
|-------|------|
| Briefing Modal | 每日首次打开自动弹出 |
| Evening Review Modal | 签到页「一日回顾」按钮 |
| Report Modal | 「日报」按钮 |

**图表延迟初始化**：仪表盘图表仅在用户首次切换到仪表盘 tab 时初始化（`ensureChartsInit()`），避免影响首屏加载速度。

### 3.3 完整 API 列表

共 40+ 个端点，分布在以下模块：

| 模块 | 端点前缀 | 端点数 |
|------|----------|--------|
| 监控状态 | `/api/status`, `/api/today`, `/api/hourly`, `/api/weekly` | 5 |
| Briefing | `/api/briefing/*` | 8 |
| 番茄钟 | `/api/pomodoro/*` | 6 |
| TodoList | `/api/todos/*` | 5 |
| 签到 | `/api/checkin/*` | 5 |
| 晚间总结 | `/api/summary/*` | 3 |
| 日报 | `/api/report/*` | 3 |
| 开工时间 | `/api/work-start/*` | 2 |
| 休息提醒 | `/api/break/*` | 4 |
| 其他 | `/api/distraction`, `/api/recovery/*`, `/ws` | 3 |

---

## 4. 数据流

### 4.1 一天的完整生命周期

```
06:00+  用户开机
          │
          ├→ work_start_tracker.record_start()     记录开工时间
          ├→ check_and_generate_yesterday_report()  生成昨日日报
          └→ 前端加载 → checkBriefing()
                │
                ▼
08:30   Briefing Modal 弹出
        "今天想做什么？"
          │
          └→ POST /api/briefing/goals
             goals: ["写完 SOP 文档", "review PR #42"]
                │
                ▼
09:00   用户选择任务，开始番茄钟
        POST /api/pomodoro/start {focus_task: "写完 SOP 文档"}
          │
          ▼
09:00-  每 60 秒一次 _monitor_cycle()
17:00     │
          ├→ fuse_state()                            状态融合
          ├→ recovery_reminder.update_user_state()   更新回归状态
          ├→ _handle_intervention()                  如需介入
          └→ check_off_track(fused)  ★              任务感知检查
                │
                ├→ 连续 10 分钟偏离 → 弹出提醒
                │   "🍅 你正在专注「写 SOP 文档」，还剩 12 分钟"
                │
                └→ 15 分钟冷却 → 再次检测...

10:00   每小时签到弹窗
        "你现在在做什么？"

20:00   用户打开「一日回顾」
        GET /api/briefing/evening-review
          │
          └→ 聚合: 目标完成 + 效率数据 + 番茄钟 + 开工时间
             生成评分和反思
```

### 4.2 持久化文件

| 文件 | 内容 | 写入时机 |
|------|------|----------|
| `data/work_logs.json` | 每分钟监控记录 | 每个监控周期 |
| `data/daily_briefing.json` | 每日目标 + 回顾 | Briefing 提交 / 回顾生成 |
| `data/todos.json` | 待办事项 | 增删改查 |
| `data/work_start_times.json` | 开工时间 | 每日首次启动 |
| `data/pomodoro_settings.json` | 番茄钟配置 | 设置变更 |
| `data/break_settings.json` | 休息提醒配置 | 设置变更 |
| `data/reports/daily_report_YYYY-MM-DD.json` | 日报 | 每日生成 |
| `data/checkins/YYYY-MM-DD.json` | 签到记录 | 每次签到 |
| `data/evening_summaries/YYYY-MM-DD.json` | 晚间总结 | 每日生成 |

---

## 5. 启动流程

```
run.py → AppManager
  │
  ├→ TrayIcon.start()                    系统托盘图标
  │
  ├→ run_server()                        FastAPI + Uvicorn（后台线程）
  │     └→ app (web_server.py)
  │
  ├→ AttentionAgent.start()              监控主循环（后台线程）
  │     ├→ start_activity_monitoring()   键鼠监听
  │     ├→ record_work_start()           记录开工时间
  │     └→ _main_loop()                  60秒循环
  │
  ├→ start_break_reminder()              休息提醒
  │
  ├→ start_desktop_overlay()             桌面悬浮窗
  │
  ├→ HourlyCheckin.start()               每小时签到
  │
  └→ webbrowser.open(":5000")            打开浏览器
```

---

## 6. 配置参考

### 6.1 `config.py` 主要配置项

```python
CHECK_INTERVAL = 60              # 截图分析间隔（秒）

ACTIVITY_MONITOR = {
    "enabled": True,
    "sample_interval": 1.0,      # 采样间隔
    "history_size": 120,         # 历史快照数
    "aggregation_window": 60,    # 聚合窗口（秒）
}

STATE_FUSION = {
    "idle_threshold": 120,       # 空闲判定阈值（秒）
    "low_activity_threshold": 0.1,
    "high_switch_threshold": 10, # 窗口切换阈值
}

INTERVENTION = {
    "style": "encouraging",      # 提醒风格
}
```

### 6.2 环境变量（`.env`）

```
QWEN_API_BASE=https://api-inference.modelscope.cn/v1
QWEN_API_KEY=your_key_here
MODEL_NAME=Qwen/Qwen2.5-VL-72B-Instruct
```

### 6.3 Nudge 参数（`daily_briefing.py`）

```python
_nudge_cooldown = 900        # 两次提醒间隔（秒），默认 15 分钟
_off_track_threshold = 10    # 连续偏离周期数，默认 10（约 10 分钟）
```

---

## 7. 平台差异

| 功能 | macOS | Windows | Linux |
|------|-------|---------|-------|
| 截屏 | mss | mss | mss |
| 键鼠监听 | pynput | pynput | pynput |
| 焦点窗口 | NSWorkspace + Quartz | win32gui | Xlib |
| 介入弹窗 | AppleScript dialog | MessageBoxW | zenity / kdialog |
| 全屏遮罩 | PyObjC NSWindow | tkinter Toplevel | tkinter Toplevel |
| 系统托盘 | pystray | pystray | pystray |

**macOS 特殊注意**：PyObjC 的窗口操作必须在主线程，因此全屏遮罩通过独立子进程（`break_overlay_process.py`）实现，父子通过 stdin/stdout 通信。

---

## 8. 已知限制

1. **VL 模型延迟**：每次截屏分析调用云端 API，延迟 2-5 秒
2. **隐私**：截图上传到云端 API 进行分析（不存储，但传输过程中存在风险）
3. **浏览器内容**：Chrome 标签页标题获取依赖系统 API，部分情况下只能拿到 "Google Chrome"
4. **电池消耗**：持续截屏 + 键鼠监听 + API 调用，笔记本电池消耗增加约 10-15%
5. **番茄钟专注记录**：仅在内存中维护，重启后清空（每日数据通过一日回顾持久化）
6. **并发**：所有模块通过单例管理，不支持多用户同时使用

---

## 9. 扩展方向

- [ ] **Chrome 标签页标题获取**：通过 AppleScript 精确获取 Chrome 当前标签 URL + 标题
- [ ] **周报/周趋势洞察**：跨天数据聚合，识别行为模式（如"周三下午最容易分心"）
- [ ] **自适应阈值**：根据用户历史数据自动调整偏离阈值和冷却时间
- [ ] **番茄钟专注记录持久化**：写入文件，支持跨天统计
- [ ] **数据导出**：CSV / PDF 导出支持
