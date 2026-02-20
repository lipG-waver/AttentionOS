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
    """全局异常处理器 — 捕获所有未处理异常，返回友好 JSON"""
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
    """404 处理器"""
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
    """422 验证错误处理器"""
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": "validation_error",
            "detail": str(exc),
        },
    )


def _safe_route(func):
    """路由装饰器 — 为每个路由添加 try/except"""
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

def _safe_get_recovery_state() -> Optional[Dict]:
    """安全获取恢复提醒状态"""
    try:
        from attention.features.recovery_reminder import get_recovery_reminder
        reminder = get_recovery_reminder()
        return reminder.get_state()
    except Exception:
        return None


def _safe_get_pomodoro_status() -> Optional[Dict]:
    """安全获取番茄钟状态"""
    try:
        from attention.features.pomodoro import get_pomodoro
        return get_pomodoro().get_status()
    except Exception:
        return None


def _parse_bool(value: str) -> bool:
    """解析字符串为布尔值"""
    return value.lower() in ("true", "1", "yes")


# ==================== 核心API路由 ====================

@app.get("/", response_class=HTMLResponse)
async def root():
    """返回主页面"""
    html_path = Config.BASE_DIR / "static" / "index.html"
    if html_path.exists():
        return FileResponse(html_path)
    return HTMLResponse("<h1>注意力管理Agent</h1><p>静态文件未找到</p>")


@app.get("/api/status")
@_safe_route
async def get_current_status():
    """获取当前实时状态（包含 recovery 和 pomodoro）"""
    db = get_database()
    monitor = get_activity_monitor()

    # 最新记录
    records = db.get_records(limit=1)
    latest = records[-1] if records else None

    # 活动状态
    activity = None
    idle_duration = 0
    if monitor._running:
        activity_state = monitor.get_current_state(60)
        idle_duration = monitor.get_idle_duration()
        activity = activity_state.to_dict()

    # 今日统计
    today_records = db.get_today_records()
    stats = db.get_statistics(today_records)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "latest_record": latest,
        "activity": activity,
        "idle_duration": idle_duration,
        "today_stats": stats,
        "monitor_running": monitor._running,
        "recovery": _safe_get_recovery_state(),
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
    # 支持通过 body 或 query 传递专注任务
    focus_task = params.get("focus_task")
    task_source = params.get("task_source")
    if not focus_task:
        try:
            body = await request.json()
            focus_task = body.get("focus_task")
            task_source = body.get("task_source")
        except:
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
async def get_todos():
    from attention.features.todo_manager import get_todo_manager
    mgr = get_todo_manager()
    return {"todos": mgr.get_all(), "stats": mgr.get_stats()}


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


@app.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: str):
    from attention.features.todo_manager import get_todo_manager
    return {"success": get_todo_manager().delete(todo_id)}


@app.post("/api/todos/smart-add")
@_safe_route
async def smart_add_todo(request: Request):
    """智能添加：接受自然语言输入，LLM 解析后创建任务"""
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
    """仅解析自然语言，不创建任务（预览用）"""
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


# ==================== 每日 Briefing API ====================

@app.get("/api/briefing")
@_safe_route
async def get_briefing():
    """获取今日 briefing 数据（包含 deadline 任务、目标等）"""
    from attention.features.daily_briefing import get_daily_briefing
    return get_daily_briefing().get_briefing_data()


@app.post("/api/briefing/goals")
@_safe_route
async def set_briefing_goals(request: Request):
    """提交今日目标，完成 briefing"""
    from attention.features.daily_briefing import get_daily_briefing
    try:
        body = await request.json()
    except Exception:
        body = {}
    goals = body.get("goals", [])
    if not goals or not any(g.strip() for g in goals):
        return {"success": False, "error": "请至少输入一个今日目标"}
    return {"success": True, **get_daily_briefing().set_goals(goals)}


@app.post("/api/briefing/dismiss")
@_safe_route
async def dismiss_briefing():
    """跳过今日 briefing"""
    from attention.features.daily_briefing import get_daily_briefing
    return {"success": True, **get_daily_briefing().dismiss_briefing()}


@app.post("/api/briefing/goals/add")
@_safe_route
async def add_briefing_goal(request: Request):
    """追加一个目标"""
    from attention.features.daily_briefing import get_daily_briefing
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = body.get("text", "").strip()
    if not text:
        return {"success": False, "error": "目标不能为空"}
    return {"success": True, **get_daily_briefing().add_goal(text)}


@app.post("/api/briefing/goals/{index}/toggle")
@_safe_route
async def toggle_briefing_goal(index: int):
    """切换目标完成状态"""
    from attention.features.daily_briefing import get_daily_briefing
    return {"success": True, **get_daily_briefing().toggle_goal(index)}


@app.post("/api/briefing/goals/{index}/remove")
@_safe_route
async def remove_briefing_goal(index: int):
    """删除一个目标"""
    from attention.features.daily_briefing import get_daily_briefing
    return {"success": True, **get_daily_briefing().remove_goal(index)}


@app.get("/api/briefing/nudge-status")
@_safe_route
async def get_nudge_status():
    """获取任务感知提醒状态"""
    from attention.features.daily_briefing import get_daily_briefing
    return get_daily_briefing().get_nudge_summary()


@app.get("/api/briefing/evening-review")
@_safe_route
async def get_evening_review():
    """生成并获取一日回顾（对照早间目标 vs 实际行为）"""
    from attention.features.daily_briefing import get_daily_briefing
    return get_daily_briefing().generate_evening_review()


# ==================== 语音识别 API（SenseVoice） ====================

@app.post("/api/speech/transcribe")
async def speech_transcribe(request: Request):
    """
    语音识别端点 — 基于 ModelScope SenseVoice 模型。
    接收音频文件，返回识别文本 + 情感标签。

    Request: multipart/form-data, field name = "audio"
    Response: {"text": "...", "emotion": "neutral", "language": "zh", "success": true}
    """
    try:
        form = await request.form()
        audio_file = form.get("audio")
        if not audio_file:
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "请上传音频文件（字段名: audio）"}
            )

        audio_bytes = await audio_file.read()
        filename = getattr(audio_file, "filename", "audio.wav")
        suffix = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".wav"

        from attention.core.speech_recognition import get_speech_recognizer
        recognizer = get_speech_recognizer()

        if not recognizer.is_available:
            return JSONResponse(
                status_code=503,
                content={
                    "success": False,
                    "error": "SenseVoice 模型未加载，请安装: pip install funasr modelscope torch torchaudio",
                }
            )

        result = recognizer.transcribe_bytes(audio_bytes, suffix=suffix)
        return result

    except Exception as e:
        logger.error(f"语音识别 API 错误: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


@app.get("/api/speech/status")
@_safe_route
async def speech_status():
    """检查 SenseVoice 语音识别是否可用"""
    from attention.core.speech_recognition import get_speech_recognizer
    recognizer = get_speech_recognizer()
    return {
        "available": recognizer.is_available,
        "model": "iic/SenseVoiceSmall",
        "features": ["transcription", "emotion_detection", "language_detection"],
    }


# ==================== 周数据洞察 API ====================

@app.get("/api/weekly-insight")
@_safe_route
async def get_weekly_insight():
    """生成过去 7 天的效率洞察"""
    from attention.features.weekly_insight import generate_weekly_insight
    return generate_weekly_insight()


# ==================== 每日开工时间API ====================

@app.get("/api/work-start/today")
@_safe_route
async def get_work_start_today():
    """获取今天的开工时间"""
    from attention.features.work_start_tracker import get_work_start_tracker
    tracker = get_work_start_tracker()
    return tracker.get_today()

@app.get("/api/work-start/history")
@_safe_route
async def get_work_start_history():
    """获取历史开工时间（最近30天）"""
    from attention.features.work_start_tracker import get_work_start_tracker
    tracker = get_work_start_tracker()
    return {"history": tracker.get_history(days=30)}


# ==================== 每日报告API ====================

@app.get("/api/report/yesterday")
@_safe_route
async def get_yesterday_report():
    from attention.features.daily_report import check_and_generate_yesterday_report
    report = check_and_generate_yesterday_report()
    return report if report else {"has_data": False, "message": "暂无昨日报告"}


@app.get("/api/report/latest")
async def get_latest_report():
    from attention.features.daily_report import get_latest_report
    report = get_latest_report()
    return report if report else {"has_data": False, "message": "暂无报告"}


@app.post("/api/report/generate")
async def generate_report():
    from attention.features.daily_report import generate_daily_report
    report = generate_daily_report(datetime.now())
    return report if report else {"has_data": False, "message": "没有足够数据"}


# ==================== 对话悬浮窗 API（替代原桌面悬浮窗）====================

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
    for int_key in ("interval_minutes", "start_hour", "end_hour", "evening_summary_hour"):
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


# ==================== 晚间总结API ====================

@app.get("/api/summary/latest")
async def get_latest_evening_summary():
    from attention.features.hourly_checkin import get_latest_summary
    summary = get_latest_summary()
    return summary if summary else {"message": "暂无晚间总结"}


@app.get("/api/summary/{date_str}")
async def get_evening_summary(date_str: str):
    from attention.features.hourly_checkin import get_summary_by_date
    summary = get_summary_by_date(date_str)
    return summary if summary else {"message": f"{date_str} 暂无晚间总结"}


@app.post("/api/summary/generate")
async def generate_summary_now(request: Request):
    from attention.features.hourly_checkin import generate_evening_summary
    today = datetime.now().strftime("%Y-%m-%d")
    # 支持传入参数控制是否使用 LLM
    try:
        body = await request.json()
    except Exception:
        body = {}
    use_llm = body.get("use_llm", True)
    summary = generate_evening_summary(today, use_llm=use_llm)
    if summary:
        return summary.to_dict()
    return {"message": "今日暂无签到数据，无法生成总结"}


# ==================== WebSocket ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket实时推送"""
    await manager.connect(websocket)
    try:
        while True:
            try:
                status = await get_current_status()
                # 如果 _safe_route 包装返回了 JSONResponse，解析它
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
    """发送用户消息，返回 AI 回复"""
    try:
        body = await request.json()
        text = body.get("text", "").strip()
        if not text:
            return JSONResponse(status_code=400,
                                content={"error": "消息不能为空"})

        from attention.core.dialogue_agent import get_dialogue_agent
        agent = get_dialogue_agent()
        response = agent.user_message(text)

        # 同步到悬浮窗显示
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
        return JSONResponse(status_code=500,
                            content={"error": str(e)})


@app.get("/api/chat/history")
async def chat_history():
    """获取对话历史"""
    try:
        from attention.core.dialogue_agent import get_dialogue_agent
        agent = get_dialogue_agent()
        return {"success": True, "messages": agent.get_history()}
    except Exception as e:
        return JSONResponse(status_code=500,
                            content={"error": str(e)})


@app.post("/api/chat/export")
async def chat_export():
    """导出今日对话为 Markdown"""
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
        return JSONResponse(status_code=500,
                            content={"error": str(e)})


# ==================== v5.2: 目标管理 API ====================

@app.get("/api/goals")
async def get_goals(include_archived: bool = False):
    """获取所有目标"""
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    return {
        "goals": mgr.get_all(include_archived=include_archived),
        "stats": mgr.get_stats(),
    }


@app.post("/api/goals")
async def add_goal(request: Request):
    """新增目标"""
    data = await request.json()
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    goal = mgr.add_goal(
        title=data.get("title", ""),
        description=data.get("description", ""),
        priority=data.get("priority", "normal"),
        tags=data.get("tags", []),
        app_keywords=data.get("app_keywords", []),
    )
    return {"success": True, "goal": goal.to_dict()}


@app.put("/api/goals/{goal_id}")
async def update_goal(goal_id: str, request: Request):
    """更新目标"""
    data = await request.json()
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    goal = mgr.update_goal(goal_id, **data)
    if goal:
        return {"success": True, "goal": goal.to_dict()}
    return JSONResponse(status_code=404, content={"error": "目标不存在"})


@app.delete("/api/goals/{goal_id}")
async def delete_goal(goal_id: str):
    """删除目标"""
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    if mgr.delete_goal(goal_id):
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "目标不存在"})


@app.post("/api/goals/{goal_id}/subtasks")
async def add_subtask(goal_id: str, request: Request):
    """为目标添加子任务"""
    data = await request.json()
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    st = mgr.add_subtask(
        goal_id=goal_id,
        title=data.get("title", ""),
        deadline=data.get("deadline"),
        estimated_minutes=data.get("estimated_minutes", 0),
        app_keywords=data.get("app_keywords", []),
    )
    if st:
        return {"success": True, "subtask": st.to_dict()}
    return JSONResponse(status_code=404, content={"error": "目标不存在"})


@app.post("/api/goals/{goal_id}/subtasks/{subtask_id}/toggle")
async def toggle_subtask(goal_id: str, subtask_id: str):
    """切换子任务完成状态"""
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    st = mgr.toggle_subtask(goal_id, subtask_id)
    if st:
        return {"success": True, "subtask": st.to_dict()}
    return JSONResponse(status_code=404, content={"error": "子任务不存在"})


@app.delete("/api/goals/{goal_id}/subtasks/{subtask_id}")
async def delete_subtask(goal_id: str, subtask_id: str):
    """删除子任务"""
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    if mgr.delete_subtask(goal_id, subtask_id):
        return {"success": True}
    return JSONResponse(status_code=404, content={"error": "子任务不存在"})


@app.get("/api/goals/deadlines")
async def get_deadlines(hours: int = 72):
    """获取即将到期的 deadline"""
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    return {"deadlines": mgr.get_upcoming_deadlines(hours=hours)}


@app.get("/api/goals/recommendation")
async def get_recommendation():
    """获取当前推荐任务"""
    from attention.features.goal_manager import get_goal_manager
    mgr = get_goal_manager()
    return mgr.what_should_i_do_now()


# ==================== v5.2: 主动规划 API ====================

@app.get("/api/planner/status")
async def get_planner_status():
    """获取主动规划引擎状态"""
    from attention.features.active_planner import get_active_planner
    planner = get_active_planner()
    return planner.get_status()


@app.post("/api/planner/rest")
async def declare_rest(request: Request):
    """声明合法休息"""
    data = await request.json()
    from attention.features.active_planner import get_active_planner
    planner = get_active_planner()
    result = planner.declare_rest(
        minutes=data.get("minutes", 15),
        reason=data.get("reason", ""),
    )
    return {"success": True, "rest": result}


@app.post("/api/planner/rest/end")
async def end_rest():
    """结束休息"""
    from attention.features.active_planner import get_active_planner
    planner = get_active_planner()
    result = planner.end_rest()
    return {"success": True, "rest": result}


@app.post("/api/planner/override")
async def override_plan(request: Request):
    """临时变更计划"""
    data = await request.json()
    from attention.features.active_planner import get_active_planner
    planner = get_active_planner()
    planner.override_plan(
        task_description=data.get("task", ""),
        duration_minutes=data.get("duration_minutes", 60),
    )
    return {"success": True, "plan": planner.get_active_plan()}


@app.get("/api/planner/plan")
async def get_current_plan():
    """获取当前活跃计划"""
    from attention.features.active_planner import get_active_planner
    planner = get_active_planner()
    return planner.get_active_plan()


# ==================== API 设置管理 ====================

@app.get("/api/settings/providers")
@_safe_route
async def get_providers():
    """获取所有 LLM 提供商配置"""
    from attention.core.api_settings import get_api_settings
    mgr = get_api_settings()
    return {"providers": mgr.get_all_configs()}


@app.post("/api/settings/providers/{provider}/key")
@_safe_route
async def set_provider_key(provider: str, request: Request):
    """设置指定提供商的 API key"""
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_key = body.get("api_key", "").strip()
    if not api_key:
        return {"success": False, "error": "API key 不能为空"}
    mgr = get_api_settings()
    ok = mgr.set_api_key(provider, api_key)
    return {"success": ok}


@app.post("/api/settings/providers/{provider}/test")
@_safe_route
async def test_provider_key(provider: str, request: Request):
    """测试指定提供商的 API key 连通性"""
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}
    api_key = body.get("api_key", "").strip() or None
    mgr = get_api_settings()
    result = mgr.test_api_key(provider, api_key)
    return result


@app.post("/api/settings/providers/active")
@_safe_route
async def set_active_provider(request: Request):
    """设置当前激活的 LLM 提供商"""
    from attention.core.api_settings import get_api_settings
    try:
        body = await request.json()
    except Exception:
        body = {}
    provider = body.get("provider", "").strip()
    if not provider:
        return {"success": False, "error": "请指定提供商"}
    mgr = get_api_settings()
    ok = mgr.set_active_provider(provider)
    if not ok:
        return {"success": False, "error": "该提供商未配置 API key"}
    return {"success": True, "active_provider": provider}


# ==================== 随手记 API ====================

@app.post("/api/memo/save")
@_safe_route
async def save_memo(request: Request):
    """保存随手记内容到长期记忆（Markdown 格式）"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    content = body.get("content", "").strip()
    if not content:
        return {"success": False, "error": "内容不能为空"}

    # 保存为 Markdown 文件到 data/memos/
    memo_dir = Config.DATA_DIR / "memos"
    memo_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"memo_{timestamp}.md"
    filepath = memo_dir / filename

    # 构建 Markdown 内容
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
    """获取所有随手记列表"""
    memo_dir = Config.DATA_DIR / "memos"
    if not memo_dir.exists():
        return {"memos": []}

    memos = []
    for f in sorted(memo_dir.glob("memo_*.md"), reverse=True):
        try:
            text = f.read_text(encoding="utf-8")
            # 提取预览（跳过标题行）
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

    return {"memos": memos[:50]}  # 最多返回 50 条


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
