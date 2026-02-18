"""
每日 Briefing 与任务感知提醒模块

核心功能：
1. 每日开工 Briefing：用户打开电脑时弹出，展示今日 deadline 任务 + 让用户声明今日主要目标
2. 今日焦点：持久化保存用户声明的今日任务目标（独立于 TodoList）
3. 任务感知提醒：检测用户行为是否偏离声明的目标，在合适时机主动提醒
"""
import json
import logging
import threading
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from attention.config import Config

logger = logging.getLogger(__name__)

BRIEFING_FILE = Config.DATA_DIR / "daily_briefing.json"


# ============================================================
# 数据持久化
# ============================================================

def _load_all() -> Dict[str, Any]:
    try:
        if BRIEFING_FILE.exists():
            with open(BRIEFING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _save_all(data: Dict[str, Any]):
    Config.ensure_dirs()
    with open(BRIEFING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# DailyBriefing
# ============================================================

class DailyBriefing:
    """
    每日 Briefing 管理器。
    
    数据结构 (per date):
    {
        "2026-02-08": {
            "briefed": true,                   # 今日是否已完成 briefing
            "briefed_at": "08:32:15",          # briefing 完成时间
            "goals": [                          # 用户声明的今日目标
                {"text": "写完 SOP 文档", "done": false},
                {"text": "review PR #42", "done": true}
            ],
            "dismissed": false                  # 用户是否跳过了 briefing
        }
    }
    """

    def __init__(self):
        self._data = _load_all()
        self._lock = threading.Lock()
        # 提醒状态
        self._last_nudge_time: Optional[datetime] = None
        self._nudge_cooldown = 900  # 15分钟冷却
        self._consecutive_off_track = 0  # 连续偏离计数
        self._off_track_threshold = 10  # 连续偏离多少个周期触发提醒（10×60s=10分钟）

    def _today_key(self) -> str:
        return date.today().strftime("%Y-%m-%d")

    def _get_today(self) -> Dict[str, Any]:
        return self._data.get(self._today_key(), {})

    def _set_today(self, entry: Dict[str, Any]):
        self._data[self._today_key()] = entry
        _save_all(self._data)

    # ---- 公开 API ----

    def needs_briefing(self) -> bool:
        """今日是否还需要 briefing（未完成且未跳过）"""
        today = self._get_today()
        return not today.get("briefed", False) and not today.get("dismissed", False)

    def get_briefing_data(self) -> Dict[str, Any]:
        """
        获取 briefing 所需的全部数据：
        - 今日 deadline 任务（从 TodoList 拉取）
        - 已逾期任务
        - 今日已声明的目标（如果有）
        - briefing 状态
        """
        today_key = self._today_key()
        today_entry = self._get_today()

        # 从 TodoManager 拉取今日相关任务
        due_today = []
        overdue = []
        upcoming = []
        try:
            from attention.features.todo_manager import get_todo_manager
            mgr = get_todo_manager()
            all_todos = mgr.get_all(include_completed=False)
            now = datetime.now()
            for t in all_todos:
                if t.get("completed"):
                    continue
                dl = t.get("deadline")
                if not dl:
                    continue
                dl_date = dl.split(" ")[0]
                if dl_date == today_key:
                    due_today.append(t)
                elif t.get("is_overdue"):
                    overdue.append(t)
                elif t.get("days_until_deadline") is not None and 0 < t["days_until_deadline"] <= 3:
                    upcoming.append(t)
        except Exception as e:
            logger.warning(f"获取 todo 数据失败: {e}")

        return {
            "date": today_key,
            "needs_briefing": self.needs_briefing(),
            "briefed": today_entry.get("briefed", False),
            "briefed_at": today_entry.get("briefed_at"),
            "dismissed": today_entry.get("dismissed", False),
            "goals": today_entry.get("goals", []),
            "due_today": due_today,
            "overdue": overdue,
            "upcoming": upcoming,
        }

    def set_goals(self, goals: List[str]) -> Dict[str, Any]:
        """
        用户提交今日目标，完成 briefing。
        
        Args:
            goals: 目标文本列表，如 ["写完 SOP 文档", "review PR #42"]
        """
        with self._lock:
            today = self._get_today()
            today["briefed"] = True
            today["briefed_at"] = datetime.now().strftime("%H:%M:%S")
            today["goals"] = [{"text": g.strip(), "done": False} for g in goals if g.strip()]
            today["dismissed"] = False
            self._set_today(today)
        logger.info(f"今日 briefing 完成，设定 {len(goals)} 个目标")
        return self.get_briefing_data()

    def dismiss_briefing(self) -> Dict[str, Any]:
        """用户跳过今日 briefing"""
        with self._lock:
            today = self._get_today()
            today["dismissed"] = True
            today["briefed"] = False
            self._set_today(today)
        logger.info("用户跳过今日 briefing")
        return self.get_briefing_data()

    def toggle_goal(self, index: int) -> Dict[str, Any]:
        """切换目标完成状态"""
        with self._lock:
            today = self._get_today()
            goals = today.get("goals", [])
            if 0 <= index < len(goals):
                goals[index]["done"] = not goals[index]["done"]
                today["goals"] = goals
                self._set_today(today)
        return self.get_briefing_data()

    def add_goal(self, text: str) -> Dict[str, Any]:
        """追加一个目标"""
        with self._lock:
            today = self._get_today()
            goals = today.get("goals", [])
            goals.append({"text": text.strip(), "done": False})
            today["goals"] = goals
            if not today.get("briefed"):
                today["briefed"] = True
                today["briefed_at"] = datetime.now().strftime("%H:%M:%S")
            self._set_today(today)
        return self.get_briefing_data()

    def remove_goal(self, index: int) -> Dict[str, Any]:
        """删除一个目标"""
        with self._lock:
            today = self._get_today()
            goals = today.get("goals", [])
            if 0 <= index < len(goals):
                goals.pop(index)
                today["goals"] = goals
                self._set_today(today)
        return self.get_briefing_data()

    # ---- 任务感知提醒 ----

    def check_off_track(self, fused_state: Dict[str, Any]) -> Optional[str]:
        """
        检查用户当前行为是否偏离了今日目标。
        
        逻辑：
        1. 如果用户没有设定目标 → 不提醒
        2. 如果所有目标都已完成 → 不提醒
        3. 如果用户处于分心/娱乐状态且有未完成目标 → 累计偏离计数
        4. 偏离持续超过阈值 → 生成提醒消息（带冷却）
        
        Args:
            fused_state: 融合状态 dict
            
        Returns:
            提醒消息字符串，或 None（不需要提醒）
        """
        today = self._get_today()
        goals = today.get("goals", [])
        
        # 没有目标或全部完成 → 不提醒
        pending_goals = [g for g in goals if not g.get("done")]
        if not pending_goals:
            self._consecutive_off_track = 0
            return None

        # 判断是否偏离
        is_distracted = fused_state.get("is_distracted", False)
        is_productive = fused_state.get("is_productive", False)
        engagement = fused_state.get("user_engagement", "")
        app_category = fused_state.get("app_category", "")

        off_track = False
        if is_distracted:
            off_track = True
        elif app_category == "entertainment":
            off_track = True
        elif engagement in ["被动消费", "分心离开"]:
            off_track = True

        if off_track:
            self._consecutive_off_track += 1
        else:
            self._consecutive_off_track = 0
            return None

        # 未到阈值 → 不提醒
        if self._consecutive_off_track < self._off_track_threshold:
            return None

        # 冷却检查
        now = datetime.now()
        if self._last_nudge_time:
            elapsed = (now - self._last_nudge_time).total_seconds()
            if elapsed < self._nudge_cooldown:
                return None

        # 生成提醒
        self._last_nudge_time = now
        self._consecutive_off_track = 0

        # 构造友好消息
        goal_text = pending_goals[0]["text"]
        remaining = len(pending_goals)
        
        # 检查是否有今日 deadline
        has_deadline = False
        try:
            from attention.features.todo_manager import get_todo_manager
            stats = get_todo_manager().get_stats()
            has_deadline = stats.get("due_today", 0) > 0 or stats.get("overdue", 0) > 0
        except:
            pass

        # 检查是否有正在进行的番茄钟专注任务
        active_focus = None
        try:
            from attention.features.pomodoro import get_pomodoro
            pomo_status = get_pomodoro().get_status()
            if pomo_status.get("phase") == "working" and pomo_status.get("focus_task"):
                active_focus = pomo_status["focus_task"]
                remaining_min = pomo_status.get("remaining_seconds", 0) // 60
        except:
            pass

        if has_deadline:
            fallback_messages = [
                f"📋 你今天有任务要交付哦！当前目标「{goal_text}」还没完成，要不要切回来？",
                f"⏰ 提醒：今天有 deadline 任务。先搞定「{goal_text}」？",
                f"🎯 你今早定的目标「{goal_text}」还在等你，今天还有任务要交付！",
            ]
        elif active_focus:
            # 番茄钟专注中但跑偏了 → 最紧迫的提醒
            fallback_messages = [
                f"🍅 你正在专注「{active_focus}」，还剩 {remaining_min} 分钟，坚持住！",
                f"🎯 番茄钟进行中 →「{active_focus}」。先完成这个再休息~",
                f"💪 再坚持 {remaining_min} 分钟！「{active_focus}」马上就完成了。",
            ]
        else:
            fallback_messages = [
                f"🎯 你今早定的目标「{goal_text}」还没完成，要不要回来继续？" + (f"（还有 {remaining-1} 个目标待完成）" if remaining > 1 else ""),
                f"💡 注意到你离开了一会儿。「{goal_text}」还在等你哦~",
                f"📌 小提醒：你今天想做的「{goal_text}」还没搞定，继续冲！",
            ]

        # 尝试用 LLM 动态生成个性化提醒（Coach Agent）
        try:
            nudge_context = {
                "pending_goals": [g["text"] for g in pending_goals],
                "off_track_minutes": self._consecutive_off_track,
                "current_app": fused_state.get("focused_app", "未知"),
                "current_activity": fused_state.get("user_engagement", "未知"),
                "focus_task": active_focus,
                "remaining_min": remaining_min,
                "has_deadline": has_deadline,
            }
            smart_msg = self._generate_smart_nudge(nudge_context)
            if smart_msg:
                return smart_msg
        except Exception as e:
            logger.debug(f"LLM 动态提醒生成失败，使用模板: {e}")

        import random
        return random.choice(fallback_messages)

    def _generate_smart_nudge(self, context: dict) -> Optional[str]:
        """
        用 Coach Agent（LLM）根据上下文生成个性化提醒消息。

        Args:
            context: 包含目标、偏离时长、当前应用等上下文信息

        Returns:
            个性化提醒文本，或 None（生成失败时 fallback 到模板）
        """
        import json as _json
        prompt = f"""用户今早设定的目标是：
{_json.dumps(context['pending_goals'], ensure_ascii=False)}

当前状况：
- 用户已连续 {context['off_track_minutes']} 分钟在做非目标相关的事
- 当前应用：{context['current_app']}
- 当前活动：{context['current_activity']}
{"- 番茄钟进行中：" + context['focus_task'] + "，剩余 " + str(context['remaining_min']) + " 分钟" if context.get('focus_task') else ""}
{"- 今日有 deadline 任务！" if context.get('has_deadline') else ""}

请用一句简短、友好、有共情力的中文提醒用户回到目标。不要说教，像朋友一样。
不超过 40 字。直接输出提醒文本，不要任何前缀。"""

        try:
            from attention.core.agents import call_agent
            msg = call_agent(
                "coach",
                prompt,
                max_tokens=80,
                temperature=0.8,
                timeout=5,
            )
            msg = msg.strip().strip('"').strip("'")
            if msg and len(msg) <= 60:
                return msg
            return None
        except Exception as e:
            logger.debug(f"Coach Agent 提醒生成失败: {e}")
            return None

    def get_nudge_summary(self) -> Dict[str, Any]:
        """获取提醒相关的状态摘要（供前端和调试使用）"""
        today = self._get_today()
        goals = today.get("goals", [])
        pending = [g for g in goals if not g.get("done")]
        return {
            "has_goals": len(goals) > 0,
            "pending_goals": len(pending),
            "total_goals": len(goals),
            "consecutive_off_track": self._consecutive_off_track,
            "off_track_threshold": self._off_track_threshold,
            "nudge_cooldown": self._nudge_cooldown,
            "last_nudge_time": self._last_nudge_time.strftime("%H:%M:%S") if self._last_nudge_time else None,
        }

    def generate_evening_review(self) -> Dict[str, Any]:
        """
        生成一日回顾，对照早间目标与实际行为。
        
        Returns:
            包含目标完成情况、效率数据、专注会话、反思提示的完整回顾
        """
        today_key = self._today_key()
        today_entry = self._get_today()
        goals = today_entry.get("goals", [])

        # 1. 目标完成统计
        total_goals = len(goals)
        completed_goals = sum(1 for g in goals if g.get("done"))
        goal_completion_rate = completed_goals / total_goals if total_goals else 0

        # 2. 从监控数据拉取今日效率
        productivity_data = {}
        try:
            from attention.core.database import get_database
            db = get_database()
            records = db.get_today_records()
            stats = db.get_statistics(records)
            total_records = len(records)

            # 计算活跃时间段
            first_record = records[0]["timestamp"] if records else None
            last_record = records[-1]["timestamp"] if records else None

            productivity_data = {
                "total_records": total_records,
                "productive_ratio": stats.get("productive_ratio", 0),
                "distracted_ratio": stats.get("distracted_ratio", 0),
                "first_record": first_record,
                "last_record": last_record,
                "attention_distribution": stats.get("attention_distribution", {}),
            }
        except Exception as e:
            logger.warning(f"获取效率数据失败: {e}")

        # 3. 从番茄钟拉取专注会话
        focus_sessions = []
        pomo_stats = {}
        try:
            from attention.features.pomodoro import get_pomodoro
            pomo = get_pomodoro()
            status = pomo.get_status()
            focus_sessions = status.get("focus_sessions", [])
            pomo_stats = {
                "completed_cycles": status.get("completed_cycles", 0),
                "total_work_minutes": status.get("total_work_minutes", 0),
                "total_break_minutes": status.get("total_break_minutes", 0),
                "skipped_breaks": status.get("skipped_breaks", 0),
            }
        except Exception as e:
            logger.warning(f"获取番茄钟数据失败: {e}")

        # 4. 从开工时间拉取
        work_start = None
        try:
            from attention.features.work_start_tracker import get_work_start_tracker
            ws = get_work_start_tracker().get_today()
            if ws.get("recorded"):
                work_start = ws.get("start_time")
        except:
            pass

        # 5. 生成反思提示
        reflection = self._generate_reflection(
            goals, goal_completion_rate, productivity_data, focus_sessions, pomo_stats
        )

        review = {
            "date": today_key,
            "briefed": today_entry.get("briefed", False),
            "briefed_at": today_entry.get("briefed_at"),
            "work_start": work_start,

            # 目标对照
            "goals": goals,
            "total_goals": total_goals,
            "completed_goals": completed_goals,
            "goal_completion_rate": round(goal_completion_rate, 2),

            # 效率数据
            "productivity": productivity_data,

            # 专注会话
            "focus_sessions": focus_sessions,
            "pomodoro_stats": pomo_stats,

            # 反思
            "reflection": reflection,
        }

        # 持久化到 briefing 数据
        with self._lock:
            today = self._get_today()
            today["evening_review"] = review
            self._set_today(today)

        return review

    def _generate_reflection(
        self, goals, goal_rate, prod_data, focus_sessions, pomo_stats
    ) -> Dict[str, Any]:
        """
        生成反思提示。优先使用 Reviewer Agent（LLM）动态生成，
        失败时 fallback 到规则模板。
        """
        total_goals = len(goals)
        completed = sum(1 for g in goals if g.get("done"))
        prod_ratio = prod_data.get("productive_ratio", 0)
        dist_ratio = prod_data.get("distracted_ratio", 0)
        pomo_count = pomo_stats.get("completed_cycles", 0)
        focus_minutes = pomo_stats.get("total_work_minutes", 0)

        # 综合评分（规则计算，保持确定性）
        score = 0
        if total_goals > 0:
            score += goal_rate * 40
        else:
            score += 20
        score += min(prod_ratio * 40, 40)
        score += min(pomo_count * 5, 20)
        score = round(score)

        # 尝试用 Reviewer Agent 生成个性化反思
        try:
            smart = self._generate_smart_reflection({
                "goals": goals,
                "goal_completion_rate": goal_rate,
                "productive_ratio": prod_ratio,
                "distracted_ratio": dist_ratio,
                "pomo_count": pomo_count,
                "focus_minutes": focus_minutes,
                "work_start": prod_data.get("first_record"),
            })
            if smart:
                smart["score"] = score
                return smart
        except Exception as e:
            logger.debug(f"Reviewer Agent 反思生成失败，使用模板: {e}")

        # fallback: 规则模板
        return self._generate_reflection_template(
            goals, goal_rate, prod_data, focus_sessions, pomo_stats, score
        )

    def _generate_smart_reflection(self, review_data: dict) -> Optional[Dict[str, Any]]:
        """用 Reviewer Agent（LLM）生成个性化反思"""
        import json as _json
        prompt = f"""请根据用户今天的数据生成简短反思。

今日数据：
- 目标：{_json.dumps(review_data['goals'], ensure_ascii=False)}
- 目标完成率：{review_data['goal_completion_rate']:.0%}
- 生产率：{review_data['productive_ratio']:.0%}
- 分心率：{review_data['distracted_ratio']:.0%}
- 番茄钟：完成 {review_data['pomo_count']} 个，共 {review_data['focus_minutes']} 分钟
- 开工时间：{review_data.get('work_start', '未记录')}

请输出 JSON 格式：
{{
  "overall_emoji": "一个表达今日状态的 emoji",
  "overall_message": "一句总评",
  "highlights": ["亮点1", "亮点2"],
  "areas_to_improve": ["改进建议1"],
  "encouragement": "一句鼓励的话"
}}
只输出 JSON。"""

        from attention.core.agents import call_agent_json
        result = call_agent_json(
            "reviewer",
            prompt,
            max_tokens=400,
            temperature=0.7,
            timeout=10,
        )
        # 校验关键字段
        if "overall_emoji" in result and "highlights" in result:
            result.setdefault("areas_to_improve", [])
            result.setdefault("overall_message", result.get("encouragement", ""))
            return result
        return None

    def _generate_reflection_template(
        self, goals, goal_rate, prod_data, focus_sessions, pomo_stats, score
    ) -> Dict[str, Any]:
        """规则模板版本的反思生成（作为 LLM 失败时的 fallback）"""
        highlights = []
        areas_to_improve = []

        total_goals = len(goals)
        completed = sum(1 for g in goals if g.get("done"))
        prod_ratio = prod_data.get("productive_ratio", 0)
        dist_ratio = prod_data.get("distracted_ratio", 0)
        pomo_count = pomo_stats.get("completed_cycles", 0)
        focus_minutes = pomo_stats.get("total_work_minutes", 0)

        if total_goals > 0:
            if goal_rate >= 1.0:
                highlights.append(f"🎯 所有 {total_goals} 个目标全部完成！")
            elif goal_rate >= 0.5:
                highlights.append(f"🎯 完成了 {completed}/{total_goals} 个目标")
            else:
                pending = [g["text"] for g in goals if not g.get("done")]
                areas_to_improve.append(f"目标「{pending[0]}」等 {len(pending)} 项未完成")

        if prod_ratio >= 0.7:
            highlights.append(f"📈 生产率 {prod_ratio:.0%}，非常高效")
        elif prod_ratio >= 0.5:
            highlights.append(f"📊 生产率 {prod_ratio:.0%}，表现稳定")
        elif prod_data.get("total_records", 0) > 5:
            areas_to_improve.append(f"生产率 {prod_ratio:.0%}，低于预期")

        if dist_ratio > 0.3 and prod_data.get("total_records", 0) > 5:
            areas_to_improve.append(f"分心率 {dist_ratio:.0%}，偏高")

        if pomo_count > 0:
            highlights.append(f"🍅 完成 {pomo_count} 个番茄钟，专注 {focus_minutes} 分钟")
            if focus_sessions:
                task_names = list(set(s["task"] for s in focus_sessions if s.get("task")))
                if task_names:
                    highlights.append(f"💡 专注任务: {', '.join(task_names[:3])}")

        if score >= 80:
            overall_emoji, overall_message = "🏆", "出色的一天！目标清晰、执行到位。"
        elif score >= 60:
            overall_emoji, overall_message = "💪", "不错的一天，继续保持这个节奏。"
        elif score >= 40:
            overall_emoji, overall_message = "🌤", "还行，但有提升空间。明天试试更早进入专注状态。"
        else:
            overall_emoji, overall_message = "🌱", "每个人都有低效的日子。明天从设定一个小目标开始。"

        return {
            "overall_emoji": overall_emoji,
            "overall_message": overall_message,
            "highlights": highlights,
            "areas_to_improve": areas_to_improve,
            "score": score,
        }


# ============================================================
# 单例
# ============================================================

_briefing: Optional[DailyBriefing] = None


def get_daily_briefing() -> DailyBriefing:
    global _briefing
    if _briefing is None:
        _briefing = DailyBriefing()
    return _briefing
