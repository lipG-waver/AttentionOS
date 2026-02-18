"""
目标与 Deadline 注册中心 — Attention OS v5.2 核心新增

两层目标体系：
  1. 长期目标 (Goal): 战略级，如"完成毕业论文"、"Attention OS 上线"
     - 每个 Goal 下可挂载子任务 (SubTask)
     - 子任务可设独立 deadline
  2. 短期 Deadline (Deadline): 独立的时间节点任务，如"周三前交 SOP"

核心能力：
  - what_should_i_do_now(): 根据时间、紧迫度、优先级推荐当前最该做的事
  - get_upcoming_deadlines(): 返回即将到期的 deadline 列表
  - 与 TodoManager 联动但不替代（Todo 是日常杂务，Goal 是战略级）
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass, field, asdict

from attention.config import Config

logger = logging.getLogger(__name__)

GOALS_FILE = Config.DATA_DIR / "goals.json"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SubTask:
    """子任务"""
    id: str = ""
    title: str = ""
    deadline: Optional[str] = None  # YYYY-MM-DD 或 YYYY-MM-DD HH:MM
    completed: bool = False
    completed_at: Optional[str] = None
    estimated_minutes: int = 0  # 预估所需时间（分钟）
    app_keywords: List[str] = field(default_factory=list)  # 关联应用关键词，用于屏幕匹配

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_overdue"] = self._is_overdue()
        d["urgency_score"] = self._urgency_score()
        return d

    def _deadline_dt(self) -> Optional[datetime]:
        if not self.deadline:
            return None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(self.deadline, fmt)
                if " " not in self.deadline:
                    dt = dt.replace(hour=23, minute=59, second=59)
                return dt
            except ValueError:
                continue
        return None

    def _is_overdue(self) -> bool:
        if self.completed:
            return False
        dt = self._deadline_dt()
        return dt is not None and datetime.now() > dt

    def _urgency_score(self) -> float:
        """紧迫度评分 (0-100)，越高越紧急"""
        if self.completed:
            return 0
        dt = self._deadline_dt()
        if dt is None:
            return 10  # 无 deadline 的任务默认低优先
        now = datetime.now()
        if now > dt:
            return 100  # 已逾期
        hours_left = (dt - now).total_seconds() / 3600
        if hours_left <= 2:
            return 95
        elif hours_left <= 6:
            return 85
        elif hours_left <= 24:
            return 70
        elif hours_left <= 72:
            return 50
        elif hours_left <= 168:  # 一周
            return 30
        return 15

    @classmethod
    def from_dict(cls, data: dict) -> "SubTask":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class Goal:
    """长期目标"""
    id: str = ""
    title: str = ""
    description: str = ""
    priority: str = "normal"  # urgent / high / normal / low
    subtasks: List[SubTask] = field(default_factory=list)
    created_at: str = ""
    archived: bool = False
    tags: List[str] = field(default_factory=list)
    # 关联的应用关键词（整个目标层面），用于屏幕-计划匹配
    app_keywords: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["subtasks"] = [s.to_dict() if isinstance(s, SubTask) else s for s in self.subtasks]
        d["progress"] = self.progress()
        d["max_urgency"] = self.max_urgency()
        return d

    def progress(self) -> float:
        if not self.subtasks:
            return 0.0
        done = sum(1 for s in self.subtasks if s.completed)
        return done / len(self.subtasks)

    def max_urgency(self) -> float:
        """所有子任务中最高的紧迫度"""
        if not self.subtasks:
            return 0
        return max(s._urgency_score() for s in self.subtasks if not s.completed) if any(not s.completed for s in self.subtasks) else 0

    def pending_subtasks(self) -> List[SubTask]:
        return [s for s in self.subtasks if not s.completed]

    @classmethod
    def from_dict(cls, data: dict) -> "Goal":
        subtasks_raw = data.pop("subtasks", [])
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        goal = cls(**valid)
        goal.subtasks = [
            SubTask.from_dict(s) if isinstance(s, dict) else s
            for s in subtasks_raw
        ]
        return goal


# ============================================================
# GoalManager
# ============================================================

class GoalManager:
    """目标管理器 — 长期目标 + 短期 Deadline 注册中心"""

    def __init__(self):
        self._goals: List[Goal] = []
        self._load()

    # ---- 持久化 ----

    def _load(self):
        Config.ensure_dirs()
        if GOALS_FILE.exists():
            try:
                with open(GOALS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._goals = [Goal.from_dict(g) for g in data]
                logger.info(f"加载了 {len(self._goals)} 个目标")
            except Exception as e:
                logger.error(f"加载目标失败: {e}")
                self._goals = []

    def _save(self):
        Config.ensure_dirs()
        try:
            with open(GOALS_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    [g.to_dict() for g in self._goals],
                    f, ensure_ascii=False, indent=2
                )
        except Exception as e:
            logger.error(f"保存目标失败: {e}")

    # ---- Goal CRUD ----

    def add_goal(
        self, title: str, description: str = "",
        priority: str = "normal", tags: Optional[List[str]] = None,
        app_keywords: Optional[List[str]] = None,
    ) -> Goal:
        goal = Goal(
            title=title.strip(),
            description=description.strip(),
            priority=priority,
            tags=tags or [],
            app_keywords=app_keywords or [],
        )
        self._goals.append(goal)
        self._save()
        logger.info(f"新增目标: {goal.title}")
        return goal

    def update_goal(self, goal_id: str, **kwargs) -> Optional[Goal]:
        for g in self._goals:
            if g.id == goal_id:
                for k, v in kwargs.items():
                    if hasattr(g, k) and v is not None:
                        setattr(g, k, v)
                self._save()
                return g
        return None

    def archive_goal(self, goal_id: str) -> bool:
        for g in self._goals:
            if g.id == goal_id:
                g.archived = True
                self._save()
                return True
        return False

    def delete_goal(self, goal_id: str) -> bool:
        orig = len(self._goals)
        self._goals = [g for g in self._goals if g.id != goal_id]
        if len(self._goals) < orig:
            self._save()
            return True
        return False

    def get_all(self, include_archived: bool = False) -> List[Dict]:
        goals = self._goals
        if not include_archived:
            goals = [g for g in goals if not g.archived]
        # 按优先级 + 紧迫度排序
        prio_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
        goals_sorted = sorted(
            goals,
            key=lambda g: (prio_order.get(g.priority, 2), -g.max_urgency())
        )
        return [g.to_dict() for g in goals_sorted]

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        for g in self._goals:
            if g.id == goal_id:
                return g
        return None

    # ---- SubTask CRUD ----

    def add_subtask(
        self, goal_id: str, title: str,
        deadline: Optional[str] = None,
        estimated_minutes: int = 0,
        app_keywords: Optional[List[str]] = None,
    ) -> Optional[SubTask]:
        goal = self.get_goal(goal_id)
        if not goal:
            return None
        st = SubTask(
            title=title.strip(),
            deadline=deadline,
            estimated_minutes=estimated_minutes,
            app_keywords=app_keywords or [],
        )
        goal.subtasks.append(st)
        self._save()
        return st

    def toggle_subtask(self, goal_id: str, subtask_id: str) -> Optional[SubTask]:
        goal = self.get_goal(goal_id)
        if not goal:
            return None
        for s in goal.subtasks:
            if s.id == subtask_id:
                s.completed = not s.completed
                s.completed_at = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if s.completed else None
                )
                self._save()
                return s
        return None

    def delete_subtask(self, goal_id: str, subtask_id: str) -> bool:
        goal = self.get_goal(goal_id)
        if not goal:
            return False
        orig = len(goal.subtasks)
        goal.subtasks = [s for s in goal.subtasks if s.id != subtask_id]
        if len(goal.subtasks) < orig:
            self._save()
            return True
        return False

    # ---- 核心能力：此刻该做什么 ----

    def what_should_i_do_now(self) -> Dict[str, Any]:
        """
        根据当前时间、deadline 紧迫度、优先级，推荐当前最该做的事。

        Returns:
            {
                "has_recommendation": bool,
                "recommended_task": {
                    "goal_id": str,
                    "goal_title": str,
                    "subtask_id": str | None,
                    "task_title": str,
                    "deadline": str | None,
                    "urgency_score": float,
                    "app_keywords": [str],
                    "reason": str,
                },
                "upcoming_deadlines": [...],
                "overdue_tasks": [...],
            }
        """
        now = datetime.now()
        candidates = []  # (urgency_score, goal, subtask_or_none)

        for goal in self._goals:
            if goal.archived:
                continue
            prio_bonus = {"urgent": 20, "high": 10, "normal": 0, "low": -10}.get(goal.priority, 0)

            pending = goal.pending_subtasks()
            if pending:
                for st in pending:
                    score = st._urgency_score() + prio_bonus
                    candidates.append((score, goal, st))
            else:
                # 目标没有子任务但未归档 → 作为整体推荐
                candidates.append((prio_bonus + 10, goal, None))

        # 排序：urgency 最高的排最前
        candidates.sort(key=lambda x: -x[0])

        # 收集逾期和即将到期
        overdue = []
        upcoming_dl = []
        for score, goal, st in candidates:
            if st and st._is_overdue():
                overdue.append({
                    "goal_title": goal.title,
                    "task_title": st.title,
                    "deadline": st.deadline,
                })
            elif st and st.deadline:
                dt = st._deadline_dt()
                if dt and 0 < (dt - now).total_seconds() < 72 * 3600:
                    upcoming_dl.append({
                        "goal_title": goal.title,
                        "task_title": st.title,
                        "deadline": st.deadline,
                        "hours_left": round((dt - now).total_seconds() / 3600, 1),
                    })

        if not candidates:
            return {
                "has_recommendation": False,
                "recommended_task": None,
                "upcoming_deadlines": upcoming_dl,
                "overdue_tasks": overdue,
            }

        # 取最优推荐
        best_score, best_goal, best_st = candidates[0]

        # 构造推荐理由
        if best_st and best_st._is_overdue():
            reason = f"已逾期！deadline 是 {best_st.deadline}"
        elif best_st and best_st.deadline:
            dt = best_st._deadline_dt()
            if dt:
                hours = (dt - now).total_seconds() / 3600
                if hours <= 2:
                    reason = f"还有不到 2 小时就到 deadline 了"
                elif hours <= 6:
                    reason = f"今天内需要完成（还剩 {hours:.0f} 小时）"
                elif hours <= 24:
                    reason = f"明天前需要完成"
                elif hours <= 72:
                    reason = f"3 天内到期"
                else:
                    reason = f"这是当前优先级最高的任务"
            else:
                reason = "当前优先级最高"
        elif best_goal.priority in ("urgent", "high"):
            reason = f"这是你标记为{best_goal.priority}优先级的目标"
        else:
            reason = "当前最适合推进的任务"

        recommended = {
            "goal_id": best_goal.id,
            "goal_title": best_goal.title,
            "subtask_id": best_st.id if best_st else None,
            "task_title": best_st.title if best_st else best_goal.title,
            "deadline": best_st.deadline if best_st else None,
            "urgency_score": best_score,
            "app_keywords": (best_st.app_keywords if best_st else []) or best_goal.app_keywords,
            "reason": reason,
        }

        return {
            "has_recommendation": True,
            "recommended_task": recommended,
            "upcoming_deadlines": upcoming_dl[:5],
            "overdue_tasks": overdue,
        }

    def get_upcoming_deadlines(self, hours: int = 72) -> List[Dict]:
        """获取即将到期的 deadline 列表"""
        now = datetime.now()
        cutoff = now + timedelta(hours=hours)
        results = []

        for goal in self._goals:
            if goal.archived:
                continue
            for st in goal.subtasks:
                if st.completed:
                    continue
                dt = st._deadline_dt()
                if dt and now < dt <= cutoff:
                    results.append({
                        "goal_id": goal.id,
                        "goal_title": goal.title,
                        "subtask_id": st.id,
                        "task_title": st.title,
                        "deadline": st.deadline,
                        "hours_left": round((dt - now).total_seconds() / 3600, 1),
                        "urgency_score": st._urgency_score(),
                    })

        results.sort(key=lambda x: x["hours_left"])
        return results

    def match_screen_to_plan(
        self, current_app: str, window_title: str
    ) -> Dict[str, Any]:
        """
        检查当前屏幕活动是否与推荐任务相关。

        Returns:
            {
                "matches_plan": bool,
                "recommended_task": {...} | None,
                "current_app": str,
                "match_reason": str,
            }
        """
        recommendation = self.what_should_i_do_now()
        if not recommendation["has_recommendation"]:
            return {
                "matches_plan": True,  # 没有计划 → 默认不干扰
                "recommended_task": None,
                "current_app": current_app,
                "match_reason": "当前没有待办目标",
            }

        rec = recommendation["recommended_task"]
        keywords = rec.get("app_keywords", [])

        # 匹配逻辑：检查当前应用/标题是否包含目标关键词
        combined = f"{current_app} {window_title}".lower()

        if keywords:
            for kw in keywords:
                if kw.lower() in combined:
                    return {
                        "matches_plan": True,
                        "recommended_task": rec,
                        "current_app": current_app,
                        "match_reason": f"正在进行与「{rec['task_title']}」相关的工作",
                    }

        # 如果没有配置关键词，通过应用分类粗略判断
        from attention.core.state_fusion import categorize_app
        cat = categorize_app(current_app, window_title)

        if cat in ("work", "learning"):
            return {
                "matches_plan": True,  # 虽然可能不是推荐的具体任务，但在工作/学习
                "recommended_task": rec,
                "current_app": current_app,
                "match_reason": "在进行工作/学习类活动（但可能不是推荐的任务）",
            }

        if cat == "communication":
            return {
                "matches_plan": True,  # 沟通也是合理的
                "recommended_task": rec,
                "current_app": current_app,
                "match_reason": "在沟通交流中",
            }

        # 不匹配
        return {
            "matches_plan": False,
            "recommended_task": rec,
            "current_app": current_app,
            "match_reason": f"当前活动与计划不符（推荐: {rec['task_title']}）",
        }

    def get_stats(self) -> Dict[str, Any]:
        """目标整体统计"""
        active = [g for g in self._goals if not g.archived]
        total_subtasks = sum(len(g.subtasks) for g in active)
        completed_subtasks = sum(
            sum(1 for s in g.subtasks if s.completed) for g in active
        )
        overdue_subtasks = sum(
            sum(1 for s in g.subtasks if s._is_overdue()) for g in active
        )
        return {
            "total_goals": len(active),
            "total_subtasks": total_subtasks,
            "completed_subtasks": completed_subtasks,
            "pending_subtasks": total_subtasks - completed_subtasks,
            "overdue_subtasks": overdue_subtasks,
            "overall_progress": (
                completed_subtasks / total_subtasks if total_subtasks else 0
            ),
        }


# ============================================================
# 单例
# ============================================================

_manager: Optional[GoalManager] = None


def get_goal_manager() -> GoalManager:
    global _manager
    if _manager is None:
        _manager = GoalManager()
    return _manager
