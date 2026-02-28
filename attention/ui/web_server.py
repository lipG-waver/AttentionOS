"""
Web服务模块
FastAPI后端，提供数据可视化API
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from attention.config import Config
from attention.core.database import get_database
from attention.core.activity_monitor import get_activity_monitor

logger = logging.getLogger(__name__)

# FastAPI应用
app = FastAPI(title="注意力管理Agent", version="2.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 统一错误处理 ====================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"API错误 [{request.method} {request.url.path}]: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "detail": f"服务器内部错误: {type(exc).__name__}",
            "path": str(request.url.path),
        },
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "error": "not_found",
            "detail": f"接口不存在: {request.url.path}",
        },
    )


@app.exception_handler(422)
async def validation_error_handler(request: Request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "validation_error",
            "detail": str(exc),
        },
    )


def _safe_route(func):
    import functools

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"路由 {func.__name__} 错误: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": str(e),
                    "detail": f"{type(e).__name__}: {e}",
                },
            )

    return wrapper


# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


# ==================== 辅助函数 ====================

def _safe_get_pomodoro_status() -> Optional[Dict]:
    try:
        from attention.features.pomodoro import get_pomodoro
        return get_pomodoro().get_status()
    except Exception:
        return None


def _parse_bool(value: str) -> bool:
    return value.lower() in ("true", "1", "yes")


# ==================== 核心API路由 ====================

@app.get("/", response_class=HTMLResponse)
async def root():
    html_path = Config.BASE_DIR / "static" / "index.html"
    if html_path.exists():
        return FileResponse(html_path)
    return HTMLResponse("<h1>注意力管理Agent</h1><p>静态文件未找到</p>")


@app.get("/api/status")
@_safe_route
async def get_current_status():
    """获取当前实时状态"""
    db = get_database()
    monitor = get_activity_monitor()

    records = db.get_records(limit=1)
    latest = records[-1] if records else None

    activity = None
    idle_duration = 0
    if monitor._running:
        activity_state = monitor.get_current_state(60)
        idle_duration = monitor.get_idle_duration()
        activity = activity_state.to_dict()

    today_records = db.get_today_records()
    stats = db.get_statistics(today_records)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_record": latest,
        "activity": activity,
        "idle_duration": idle_duration,
        "today_stats": stats,
        "monitor_running": monitor._running,
        "pomodoro": _safe_get_pomodoro_status(),
    }


@app.get("/api/today")
@_safe_route
async def get_today_data():
    """获取今日详细数据"""
    db = get_database()
    records = db.get_today_records()

    timeline = []
    for r in records:
        fused = r.get("fused_state", {})
        timeline.append({
            "time": r["timestamp"],
            "work_status": r.get("analysis", {}).get("work_status", "未知"),
            "engagement": fused.get("user_engagement", "未知"),
            "attention": fused.get("attention_level", "未知"),
            "is_productive": fused.get("is_productive", False),
            "is_distracted": fused.get("is_distracted", False),
            "app": fused.get("active_window_app", ""),
            "activity_ratio": fused.get("activity_ratio", 0),
        })

    stats = db.get_statistics(records)

    app_usage: Dict[str, int] = {}
    for r in records:
        fused = r.get("fused_state", {})
        app_name = fused.get("active_window_app", "未知")
        if app_name:
            app_usage[app_name] = app_usage.get(app_name, 0) + 1

    app_usage_sorted = sorted(app_usage.items(), key=lambda x: -x[1])[:10]

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timeline": timeline,
        "statistics": stats,
        "app_usage": [{"app": k, "minutes": v} for k, v in app_usage_sorted],
        "total_records": len(records),
    }


@app.get("/api/hourly")
@_safe_route
async def get_hourly_pattern():
    db = get_database()
    pattern = db.get_hourly_pattern(days=7)
    hourly_data = []
    for hour in range(24):
        data = pattern.get(hour, {})
        hourly_data.append({
            "hour": hour,
            "productive_ratio": data.get("productive_ratio", 0),
            "distracted_ratio": data.get("distracted_ratio", 0),
            "sample_count": data.get("sample_count", 0),
        })
    return {"hourly_pattern": hourly_data}


@app.get("/api/weekly")
@_safe_route
async def get_weekly_trend():
    db = get_database()
    weekly_data = []
    for i in range(7):
        date = datetime.now() - timedelta(days=6 - i)
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        records = db.get_records(start_time=start, end_time=end)
        stats = db.get_statistics(records)
        weekly_data.append({
            "date": start.strftime("%m-%d"),
            "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][start.weekday()],
            "total_records": stats.get("total_records", 0),
            "productive_ratio": stats.get("productive_ratio", 0),
            "distracted_ratio": stats.get("distracted_ratio", 0),
        })
    return {"weekly_trend": weekly_data}


@app.get("/api/distraction")
async def get_distraction_info():
    db = get_database()
    return {
        "current_streak": db.get_recent_distraction_streak(),
        "entertainment_duration": db.get_recent_entertainment_duration(),
    }


# ==================== 番茄钟API ====================

@app.get("/api/pomodoro/status")
@_safe_route
async def pomodoro_get_status():
    from attention.features.pomodoro import get_pomodoro
    return get_pomodoro().get_status()


@app.post("/api/pomodoro/start")
@_safe_route
async def pomodoro_start(request: Request):
    from attention.features.pomodoro import get_pomodoro
    p = get_pomodoro()
    params = dict(request.query_params)
    focus_task = params.get("focus_task")
    task_source = params.get("task_source")
    if not focus_task:
        try:
            body = await request.json()
            focus_task = body.get("focus_task")
            task_source = body.get("task_source")
        except Exception:
            pass
    p.start_work(focus_task=focus_task, task_source=task_source)
    return {"success": True, "status": p.get_status()}


@app.post("/api/pomodoro/pause")
async def pomodoro_pause():
    from attention.features.pomodoro import get_pomodoro
    p = get_pomodoro()
    p.pause()
    return {"success": True, "status": p.get_status()}


@app.post("/api/pomodoro/resume")
async def pomodoro_resume():
    from attention.features.pomodoro import get_pomodoro
    p = get_pomodoro()
    p.resume()
    return {"success": True, "status": p.get_status()}


@app.post("/api/pomodoro/stop")
async def pomodoro_stop():
    from attention.features.pomodoro import get_pomodoro
    p = get_pomodoro()
    p.stop()
    return {"success": True, "status": p.get_status()}


@app.post("/api/pomodoro/skip-break")
async def pomodoro_skip_break():
    from attention.features.pomodoro import get_pomodoro
    p = get_pomodoro()
    p.skip_break()
    return {"success": True, "status": p.get_status()}


@app.post("/api/pomodoro/settings")
async def pomodoro_update_settings(request: Request):
    from attention.features.pomodoro import get_pomodoro
    p = get_pomodoro()
    params = dict(request.query_params)
    kwargs = {}
    for int_key in ("work_minutes", "short_break_minutes", "long_break_minutes"):
        if int_key in params:
            kwargs[int_key] = int(params[int_key])
    if "force_break" in params:
        kwargs["force_break"] = _parse_bool(params["force_break"])
    if kwargs:
        p.update_settings(**kwargs)
    return {"success": True, "settings": p.settings.to_dict()}


# ==================== TodoList API ====================

@app.get("/api/todos")
@_safe_route
async def get_todos(request: Request):
    """
    获取待办事项列表，支持搜索和过滤。

    Query params:
      q:                关键词搜索（标题/标签）
      due:              due=today | due=overdue | due=upcoming
      days:             upcoming 时的天数窗口（默认7）
      tag:              按标签过滤（精确匹配）
      priority:         按优先级过滤（urgent/high/normal/low）
      include_completed: 是否包含已完成（默认 true）
    """
    from attention.features.todo_manager import get_todo_manager
    mgr = get_todo_manager()
    params = dict(request.query_params)

    q = params.get("q", "").strip()
    due = params.get("due", "").strip().lower()
    days = int(params.get("days", 7))
    tag = params.get("tag", "").strip()
    priority = params.get("priority", "").strip()
    include_completed = params.get("include_completed", "true").lower() != "false"

    if due == "today":
        todos = mgr.get_due_today()
    elif due == "overdue":
        todos = mgr.get_overdue()
    elif due == "upcoming":
        todos = mgr.get_upcoming(days=days)
    elif q:
        todos = mgr.search(q, include_completed=include_completed)
    else:
        todos = mgr.get_all(include_completed=include_completed)

    # 在结果中二次过滤 tag / priority
    if tag:
        todos = [t for t in todos if tag in (t.get("tags") or [])]
    if priority:
        todos = [t for t in todos if t.get("priority") == priority]

    return {"todos": todos, "stats": mgr.get_stats()}


@app.post("/api/todos")
@_safe_route
async def add_todo(request: Request):
    from attention.features.todo_manager import get_todo_manager
    mgr = get_todo_manager()
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = body.get("title", "").strip()
    if not title:
        return {"success": False, "error": "标题不能为空"}
    todo = mgr.add(
        title=title,
        deadline=body.get("deadline") or None,
        priority=body.get("priority", "normal"),
        tags=body.get("tags") or [],
    )
    return {"success": True, "todo": todo.to_dict()}


@app.post("/api/todos/{todo_id}/toggle")
async def toggle_todo(todo_id: str):
    from attention.features.todo_manager import get_todo_manager
    todo = get_todo_manager().toggle_complete(todo_id)
    if todo:
        return {"success": True, "todo": todo.to_dict()}
    return {"success": False, "error": "未找到该待办事项"}


@app.delete("/api/todos/completed")
async def clear_completed_todos():
    """清空所有已完成的待办事项"""
    from attention.features.todo_manager import get_todo_manager
    deleted = get_todo_manager().clear_completed()
    return {"success": True, "deleted": deleted}


@app.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: str):
    from attention.features.todo_manager import get_todo_manager
    return {"success": get_todo_manager().delete(todo_id)}


@app.get("/api/todos/search")
@_safe_route
async def search_todos(request: Request):
    """
    按关键词搜索待办事项（GET /api/todos?q= 的语义别名）。

    Query params:
      q:                 搜索关键词（必填）
      include_completed: 是否包含已完成（默认 false）
    """
    from attention.features.todo_manager import get_todo_manager
    params = dict(request.query_params)
    q = params.get("q", "").strip()
    if not q:
        return {"success": False, "error": "请提供搜索关键词 q"}
    include_completed = params.get("include_completed", "false").lower() != "false"
    mgr = get_todo_manager()
    todos = mgr.search(q, include_completed=include_completed)
    return {"success": True, "todos": todos, "count": len(todos), "keyword": q}


@app.post("/api/todos/smart-add")
@_safe_route
async def smart_add_todo(request: Request):
    from attention.features.todo_manager import get_todo_manager
    mgr = get_todo_manager()
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = body.get("text", "").strip()
    if not text:
        return {"success": False, "error": "输入不能为空"}
    use_llm = body.get("use_llm", True)
    result = mgr.smart_add(text, use_llm=use_llm)
    return {"success": True, **result}


@app.post("/api/todos/parse")
@_safe_route
async def parse_todo_text(request: Request):
    from attention.features.todo_manager import parse_natural_language_todo
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = body.get("text", "").strip()
    if not text:
        return {"success": False, "error": "输入不能为空"}
    use_llm = body.get("use_llm", True)
    parsed = parse_natural_language_todo(text, use_llm=use_llm)
    return {"success": True, "parsed": parsed}


@app.post("/api/todos/bulk-add")
@_safe_route
async def bulk_add_todos(request: Request):
    """
    批量添加多条待办（相同标题，不同日期）。

    Body:
      title: str          — 任务标题
      dates: List[str]    — YYYY-MM-DD 格式的日期列表
      priority: str       — 优先级（可选，默认 normal）
      tags: List[str]     — 标签（可选）

    或使用 recurrence 模式自动生成日期：
      recurrence: "monthly" | "weekly"
      day_of_month: int   — 每月第几日（monthly 时必填）
      day_of_week: int    — 星期几 0=周一（weekly 时必填）
      start_date: str     — 开始日期 YYYY-MM-DD（可选，默认今天）
      end_date: str       — 结束日期 YYYY-MM-DD（必填）
    """
    from datetime import datetime
    from attention.features.todo_manager import (
        get_todo_manager, generate_monthly_dates, generate_weekly_dates
    )
    try:
        body = await request.json()
    except Exception:
        body = {}

    title = body.get("title", "").strip()
    if not title:
        return {"success": False, "error": "标题不能为空"}

    priority = body.get("priority", "normal")
    tags = body.get("tags") or []

    dates = body.get("dates")
    recurrence = body.get("recurrence")

    if not dates and recurrence:
        end_date_str = body.get("end_date", "")
        start_date_str = body.get("start_date", datetime.now().strftime("%Y-%m-%d"))
        try:
            start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        except ValueError:
            return {"success": False, "error": "日期格式应为 YYYY-MM-DD"}

        if recurrence == "monthly":
            day_of_month = body.get("day_of_month")
            if not day_of_month:
                return {"success": False, "error": "monthly 模式需要 day_of_month"}
            dates = generate_monthly_dates(int(day_of_month), start_dt, end_dt)
        elif recurrence == "weekly":
            day_of_week = body.get("day_of_week")
            if day_of_week is None:
                return {"success": False, "error": "weekly 模式需要 day_of_week (0=周一)"}
            dates = generate_weekly_dates(int(day_of_week), start_dt, end_dt)
        else:
            return {"success": False, "error": f"不支持的 recurrence 类型: {recurrence}"}

    if not dates:
        return {"success": False, "error": "日期列表不能为空，请提供 dates 或 recurrence 参数"}

    mgr = get_todo_manager()
    todos = mgr.bulk_add(title, dates, priority=priority, tags=tags)
    return {
        "success": True,
        "count": len(todos),
        "todos": [t.to_dict() for t in todos],
    }


# ==================== 对话悬浮窗 API ====================

@app.get("/api/overlay/status")
@_safe_route
async def get_overlay_status():
    try:
        from attention.ui.chat_overlay import get_chat_overlay
        overlay = get_chat_overlay()
        agent = overlay.get_agent()
        ctx = agent.get_context()
        return {
            "is_focus_mode": ctx.is_focus_mode,
            "focus_task": ctx.focus_task,
            "attention_level": ctx.attention_level,
            "is_distracted": ctx.is_distracted,
            "ready": overlay.is_ready(),
        }
    except Exception as e:
        return {"error": str(e), "ready": False}


@app.post("/api/overlay/intervention")
async def trigger_intervention(request: Request):
    params = dict(request.query_params)
    reason = params.get("reason", "手动触发测试")
    try:
        from attention.ui.chat_overlay import get_chat_overlay
        get_chat_overlay().show_nudge(reason)
        return {"success": True, "reason": reason}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/overlay/break")
async def trigger_break_overlay(request: Request):
    try:
        from attention.ui.chat_overlay import get_chat_overlay
        get_chat_overlay().show_break_reminder()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/overlay/skip-break")
async def skip_break_overlay():
    try:
        from attention.features.pomodoro import get_pomodoro
        get_pomodoro().skip_break()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== 休息提醒设置API ====================

@app.get("/api/break/settings")
@_safe_route
async def get_break_settings():
    from attention.features.break_reminder import get_break_reminder
    reminder = get_break_reminder()
    return {"settings": reminder.settings.to_dict(), "status": reminder.get_status()}


@app.post("/api/break/settings")
async def update_break_settings(request: Request):
    params = dict(request.query_params)
    from attention.features.break_reminder import get_break_reminder
    reminder = get_break_reminder()
    kwargs = {}
    for int_key in ("interval_minutes", "break_duration_minutes",
                     "rest_end_reminder_minutes"):
        if int_key in params:
            kwargs[int_key] = int(params[int_key])
    for bool_key in ("enabled", "sound_enabled",
                      "rest_end_reminder_enabled", "rest_end_sound_enabled",
                      "rest_end_chat_enabled"):
        if bool_key in params:
            kwargs[bool_key] = _parse_bool(params[bool_key])
    if kwargs:
        reminder.update_settings(**kwargs)
    return {"success": True, "settings": reminder.settings.to_dict()}


# ==================== 每小时签到API ====================

@app.get("/api/checkin/status")
@_safe_route
async def get_checkin_status():
    from attention.features.hourly_checkin import get_hourly_checkin
    return get_hourly_checkin().get_status()


@app.get("/api/checkin/today")
@_safe_route
async def get_checkin_today():
    from attention.features.hourly_checkin import get_hourly_checkin
    entries = get_hourly_checkin().get_today_entries()
    return {"date": datetime.now().strftime("%Y-%m-%d"), "entries": entries}


@app.get("/api/checkin/entries/{date_str}")
async def get_checkin_by_date(date_str: str):
    from attention.features.hourly_checkin import load_entries_by_date
    entries = load_entries_by_date(date_str)
    return {"date": date_str, "entries": [e.to_dict() for e in entries]}


@app.post("/api/checkin/add")
async def add_checkin(request: Request):
    params = dict(request.query_params)
    doing = params.get("doing", "")
    feeling = params.get("feeling", "normal")
    from attention.features.hourly_checkin import get_hourly_checkin
    entry = get_hourly_checkin().add_entry_from_web(doing, feeling)
    return {"success": True, "entry": entry.to_dict()}


@app.post("/api/checkin/trigger")
async def trigger_checkin():
    from attention.features.hourly_checkin import get_hourly_checkin
    get_hourly_checkin().trigger_now()
    return {"success": True}


@app.post("/api/checkin/settings")
async def update_checkin_settings(request: Request):
    params = dict(request.query_params)
    from attention.features.hourly_checkin import get_hourly_checkin
    checkin = get_hourly_checkin()
    kwargs = {}
    for int_key in ("interval_minutes", "start_hour", "end_hour"):
        if int_key in params:
            kwargs[int_key] = int(params[int_key])
    for bool_key in ("enabled", "sound_enabled"):
        if bool_key in params:
            kwargs[bool_key] = _parse_bool(params[bool_key])
    if kwargs:
        checkin.update_settings(**kwargs)
    return {"success": True, "settings": checkin.settings.to_dict()}


@app.post("/api/checkin/toggle")
async def toggle_checkin(request: Request):
    params = dict(request.query_params)
    enabled = _parse_bool(params.get("enabled", "true"))
    from attention.features.hourly_checkin import get_hourly_checkin
    checkin = get_hourly_checkin()
    if enabled:
        checkin.settings.enabled = True
        checkin.start()
    else:
        checkin.settings.enabled = False
        checkin.stop()
    checkin.save_settings()
    return {"success": True, "enabled": enabled}


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                status = await get_current_status()
                if hasattr(status, 'body'):
                    import json as _json
                    status = _json.loads(status.body)
            except Exception:
                status = {"error": "获取状态失败", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

            try:
                from attention.ui.chat_overlay import get_chat_overlay
                overlay = get_chat_overlay()
                agent = overlay.get_agent()
                ctx = agent.get_context()
                status["overlay"] = {
                    "is_focus_mode": ctx.is_focus_mode,
                    "attention_level": ctx.attention_level,
                    "ready": overlay.is_ready(),
                }
            except Exception:
                status["overlay"] = {"ready": False}

            try:
                await websocket.send_json(status)
            except Exception:
                break

            await asyncio.sleep(5)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        manager.disconnect(websocket)


# ==================== 对话 API ====================

@app.post("/api/chat/send")
async def chat_send(request: Request):
    try:
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse(status_code=400, content={"error": "消息不能为空"})

        from attention.core.dialogue_agent import get_dialogue_agent
        agent = get_dialogue_agent()
        response = agent.user_message(text)

        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            overlay._send_ai_message(response)
        except Exception:
            pass

        return {
            "success": True,
            "response": response,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/chat/history")
async def chat_history():
    try:
        from attention.core.dialogue_agent import get_dialogue_agent
        agent = get_dialogue_agent()
        return {"success": True, "messages": agent.get_history()}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/chat/export")
async def chat_export():
    try:
        from attention.core.dialogue_agent import get_dialogue_agent
        from attention.features.chat_logger import save_chat_log
        agent = get_dialogue_agent()
        messages = agent.get_history_for_export()
        filepath = save_chat_log(messages)
        return {
            "success": True,
            "path": str(filepath),
            "message": f"已导出到 {filepath.name}",
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ==================== 对话日志查阅 API ====================

@app.get("/api/chatlog/list")
@_safe_route
async def list_chat_logs():
    chat_log_dir = Config.DATA_DIR / "chat_logs"
    if not chat_log_dir.exists():
        return {"dates": []}

    dates = []
    for f in sorted(chat_log_dir.glob("chat_log_*.md"), reverse=True):
        name = f.stem
        if name.startswith("chat_log_"):
            date_str = name[len("chat_log_"):]
            dates.append(date_str)

    return {"dates": dates}


@app.get("/api/chatlog/read/{date_str}")
@_safe_route
async def read_chat_log(date_str: str):
    chat_log_dir = Config.DATA_DIR / "chat_logs"
    filepath = chat_log_dir / f"chat_log_{date_str}.md"

    if not filepath.exists():
        return {"success": False, "error": f"{date_str} 暂无对话记录"}

    try:
        content = filepath.read_text(encoding="utf-8")
        return {"success": True, "date": date_str, "content": content}
    except Exception as e:
        return {"success": False, "error": f"读取失败: {e}"}


# ==================== API 设置管理 ====================

@app.get("/api/settings/providers")
@_safe_route
async def get_providers():
    from attention.core.api_settings import get_api_settings
    mgr = get_api_settings()
    return {"providers": mgr.get_all_configs()}


@app.post("/api/settings/providers/{provider}/key")
@_safe_route
async def set_provider_key(provider: str, request: Request):
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_key = (body.get("api_key") or "").strip()
    if not api_key:
        return {"success": False, "error": "API key 不能为空"}
    mgr = get_api_settings()
    ok = mgr.set_api_key(provider, api_key)
    if not ok:
        return {"success": False, "error": "提供商不存在或保存失败"}
    return {"success": True, "message": f"{provider} 的 API key 已保存"}


@app.post("/api/settings/providers/{provider}/config")
@_safe_route
async def update_provider_config(provider: str, request: Request):
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}

    text_model = (body.get("text_model") or "").strip()
    vision_model = (body.get("vision_model") or "").strip()
    api_base = (body.get("api_base") or "").strip()

    updates = {}
    if text_model:
        updates["text_model"] = text_model
    if "vision_model" in body:
        updates["vision_model"] = vision_model
    if api_base:
        updates["api_base"] = api_base

    if not updates:
        return {"success": False, "error": "没有可更新的配置"}

    mgr = get_api_settings()
    ok = mgr.update_provider_config(provider, **updates)
    if not ok:
        return {"success": False, "error": "提供商不存在或更新失败"}
    return {"success": True, "message": f"{provider} 配置已更新"}


@app.post("/api/settings/providers/{provider}/test")
@_safe_route
async def test_provider_key(provider: str, request: Request):
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_key = (body.get("api_key") or "").strip() or None
    mgr = get_api_settings()
    result = mgr.test_api_key(provider, api_key)
    return result


@app.post("/api/settings/providers/active")
@_safe_route
async def set_active_provider(request: Request):
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}
    provider = (body.get("provider") or "").strip()
    if not provider:
        return {"success": False, "error": "请指定提供商"}
    mgr = get_api_settings()
    ok = mgr.set_active_provider(provider)
    if not ok:
        return {"success": False, "error": "该提供商未配置 API key"}
    return {"success": True, "active_provider": provider}


# ==================== 开机自启 API ====================

@app.get("/api/settings/autostart")
@_safe_route
async def get_autostart_status():
    import platform
    from attention.core.autostart_manager import AutoStartManager
    from attention.core.app_settings import get_app_settings

    mgr = AutoStartManager()
    os_enabled = mgr.is_enabled()
    user_pref = get_app_settings().auto_start_enabled

    return {
        "enabled": os_enabled,
        "user_preference": user_pref,
        "platform": platform.system(),
    }


@app.post("/api/settings/autostart")
@_safe_route
async def set_autostart(request: Request):
    from attention.core.autostart_manager import AutoStartManager
    from attention.core.app_settings import get_app_settings

    try:
        body = await request.json()
    except Exception:
        body = {}

    enabled = bool(body.get("enabled", False))
    mgr = AutoStartManager()

    if enabled:
        success = mgr.enable()
    else:
        success = mgr.disable()

    settings = get_app_settings()
    settings.auto_start_enabled = enabled

    return {
        "success": success,
        "enabled": enabled,
        "message": f"开机自启已{'启用' if enabled else '禁用'}" if success else "操作失败，请检查权限",
    }


# ==================== 主题设置 API ====================

@app.get("/api/settings/theme")
@_safe_route
async def get_theme():
    from attention.core.app_settings import get_app_settings
    return {"theme": get_app_settings().theme}


@app.post("/api/settings/theme")
@_safe_route
async def set_app_theme(request: Request):
    from attention.core.app_settings import get_app_settings
    params = dict(request.query_params)
    theme = params.get("theme", "").strip()
    if theme not in ("dark", "light"):
        return {"success": False, "error": "无效的主题值，仅支持 dark 或 light"}
    get_app_settings().theme = theme
    try:
        from attention.ui.chat_overlay import get_chat_overlay
        overlay = get_chat_overlay()
        if overlay.is_ready():
            overlay._send({"cmd": "set_theme", "theme": theme})
    except Exception:
        pass
    return {"success": True, "theme": theme}


# ==================== 随手记 API ====================

@app.post("/api/memo/save")
@_safe_route
async def save_memo(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = (body.get("content") or "").strip()
    if not content:
        return {"success": False, "error": "内容不能为空"}

    memo_dir = Config.DATA_DIR / "memos"
    memo_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"memo_{timestamp}.md"
    filepath = memo_dir / filename

    md_content = f"# 随手记 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{content}\n"
    filepath.write_text(md_content, encoding="utf-8")

    return {
        "success": True,
        "path": str(filepath),
        "filename": filename,
        "message": f"已保存到 {filename}",
    }


@app.get("/api/memo/list")
@_safe_route
async def list_memos():
    memo_dir = Config.DATA_DIR / "memos"
    if not memo_dir.exists():
        return {"memos": []}

    memos = []
    for f in sorted(memo_dir.glob("memo_*.md"), reverse=True):
        try:
            text = f.read_text(encoding="utf-8")
            lines = text.strip().split("\n")
            preview = ""
            for line in lines:
                if not line.startswith("#") and line.strip():
                    preview = line.strip()[:100]
                    break
            memos.append({
                "filename": f.name,
                "preview": preview,
                "created": f.stat().st_mtime,
            })
        except Exception:
            pass

    return {"memos": memos[:50]}


# ==================== 静态文件 ====================

static_dir = Config.BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


# ==================== 启动函数 ====================

def run_server(host: str = "127.0.0.1", port: int = 5000):
    uvicorn.run(app, host=host, port=port, log_level="warning")


async def run_server_async(host: str = "127.0.0.1", port: int = 5000):
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("启动Web服务器: http://127.0.0.1:5000")
    run_server()
