# Attention OS Changelog

## v5.2 — 主动引导模式 (2026-02-17)

### 核心理念转变
从"被动监控 → 分心提醒"升级为"主动引导 → 此刻该做什么"。
系统不再只是告诉你"你分心了"，而是主动告诉你"你现在应该做X"，
在检测到偏离时友好确认你的意图。

### 新增模块

#### 目标与 Deadline 注册中心 (`goal_manager.py`)
- 两层目标体系：长期目标 (Goal) + 子任务 (SubTask)
- 每个子任务可设独立 deadline 和应用关键词
- `what_should_i_do_now()`: 根据时间、紧迫度、优先级推荐当前最该做的事
- `match_screen_to_plan()`: 比对当前屏幕活动与推荐计划
- 紧迫度评分系统 (0-100)，基于 deadline 倒计时自动计算
- 完整 CRUD API

#### 主动规划引擎 (`active_planner.py`)
- 每个监控周期比较"当前屏幕" vs "推荐计划"
- 匹配时静默，不匹配时发起确认意图的对话
- **合法休息模式 (Sanctioned Rest)**：用户声明休息后系统静默
- **计划变更 (Plan Override)**：用户临时切换任务
- 连续偏离容忍 + 对话冷却机制

### 新增 Agent 角色
- `planner` Agent：生成友好的计划确认对话

### 对话系统升级
- 新增命令：`/plan` `/rest` `/back` `/switch` `/deadlines`
- 自然语言休息检测（"我想摆烂"自动触发合法休息）
- `proactive_plan_check()`: 主动计划确认对话

### 主循环升级
- 启动时主动推送推荐计划
- 监控周期新增"主动规划检查"（优先级高于旧分心提醒）

### Web API 新增 (15+ endpoints)
- Goals CRUD: `/api/goals`, `/api/goals/{id}/subtasks`
- Planner: `/api/planner/status`, `/api/planner/rest`, `/api/planner/plan`

### 配置新增
- `Config.ACTIVE_PLANNER`: off_plan_threshold, nudge_cooldown, max_rest_minutes 等

---

## v5.1 — 对话式交互 + Agent 架构 (2026-02-12)
- 统一对话悬浮窗
- Multi-Agent 架构
- 每日 Briefing + 晚间回顾
- 番茄钟专注模式 + 每小时签到
- 活动监控 + 状态融合
