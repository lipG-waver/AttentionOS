"""
测试 GoalManager 和 ActivePlanner (v5.2)
"""
import sys
import os
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# 确保可以导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_goal_manager():
    """测试目标管理器核心功能"""
    from attention.features.goal_manager import GoalManager, Goal, SubTask, GOALS_FILE

    # 使用临时文件
    import attention.features.goal_manager as gm
    original_file = gm.GOALS_FILE
    gm.GOALS_FILE = Path(tempfile.mktemp(suffix=".json"))

    try:
        mgr = GoalManager()

        # 1. 添加目标
        g1 = mgr.add_goal(
            title="完成毕业论文",
            priority="high",
            app_keywords=["word", "latex", "overleaf"],
        )
        assert g1.title == "完成毕业论文"
        assert g1.priority == "high"
        print("✅ 添加目标 OK")

        # 2. 添加子任务
        st1 = mgr.add_subtask(
            g1.id, "写完第三章",
            deadline=(datetime.now() + timedelta(hours=4)).strftime("%Y-%m-%d %H:%M"),
            estimated_minutes=120,
        )
        assert st1 is not None
        assert st1.title == "写完第三章"
        print("✅ 添加子任务 OK")

        st2 = mgr.add_subtask(
            g1.id, "修改参考文献",
            deadline=(datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
        )

        # 3. 推荐任务
        rec = mgr.what_should_i_do_now()
        assert rec["has_recommendation"] is True
        assert rec["recommended_task"]["task_title"] == "写完第三章"  # 更紧急
        print(f"✅ 推荐任务: {rec['recommended_task']['task_title']} (urgency={rec['recommended_task']['urgency_score']})")

        # 4. 屏幕匹配
        match = mgr.match_screen_to_plan("Microsoft Word", "论文第三章.docx")
        assert match["matches_plan"] is True
        print(f"✅ 屏幕匹配 (Word + 论文): {match['match_reason']}")

        match2 = mgr.match_screen_to_plan("Bilibili", "搞笑视频合集")
        assert match2["matches_plan"] is False
        print(f"✅ 屏幕不匹配 (Bilibili): {match2['match_reason']}")

        # 5. 完成子任务
        mgr.toggle_subtask(g1.id, st1.id)
        rec2 = mgr.what_should_i_do_now()
        assert rec2["recommended_task"]["task_title"] == "修改参考文献"
        print("✅ 完成子任务后推荐更新 OK")

        # 6. 统计
        stats = mgr.get_stats()
        assert stats["total_subtasks"] == 2
        assert stats["completed_subtasks"] == 1
        print(f"✅ 统计: {stats}")

        # 7. deadline 查询
        deadlines = mgr.get_upcoming_deadlines(hours=96)
        assert len(deadlines) >= 1
        print(f"✅ 即将到期: {len(deadlines)} 个")

        print("\n🎉 GoalManager 所有测试通过!")

    finally:
        gm.GOALS_FILE = original_file
        # 清理临时文件
        try:
            os.unlink(gm.GOALS_FILE)
        except:
            pass


def test_active_planner():
    """测试主动规划引擎"""
    from attention.features.active_planner import ActivePlanner

    planner = ActivePlanner()

    # 1. 合法休息
    rest = planner.declare_rest(15, reason="刷会儿手机")
    assert rest["is_active"] is True
    assert rest["duration_minutes"] == 15
    print(f"✅ 声明休息: {rest['remaining_minutes']}分钟")

    assert planner.is_resting() is True
    print("✅ 休息状态检查 OK")

    # 休息中不干预
    result = planner.check_cycle(
        current_app="Bilibili",
        window_title="搞笑视频",
        is_productive=False,
        is_distracted=True,
        app_category="entertainment",
    )
    assert result is None  # 休息中不干预
    print("✅ 休息中不干预 OK")

    # 结束休息
    planner.end_rest()
    assert planner.is_resting() is False
    print("✅ 结束休息 OK")

    # 2. 计划变更
    planner.override_plan("回复邮件", duration_minutes=30)
    plan = planner.get_active_plan()
    assert plan["source"] == "user_override"
    assert plan["task_title"] == "回复邮件"
    print(f"✅ 计划变更: {plan['task_title']}")

    # override 期间不检查
    result = planner.check_cycle(
        current_app="Chrome",
        window_title="YouTube",
        is_productive=False,
        is_distracted=True,
        app_category="entertainment",
    )
    assert result is None
    print("✅ Override 期间不干预 OK")

    # 清除 override
    planner.clear_override()

    # 3. 状态摘要
    status = planner.get_status()
    assert "current_plan" in status
    assert "is_resting" in status
    print(f"✅ 状态摘要: {json.dumps(status, ensure_ascii=False, default=str)[:100]}...")

    print("\n🎉 ActivePlanner 所有测试通过!")


def test_dialogue_commands():
    """测试对话系统新增命令"""
    from attention.core.dialogue_agent import DialogueAgent

    agent = DialogueAgent()

    # /plan 命令
    response = agent.user_message("/plan")
    assert response  # 应返回计划信息
    print(f"✅ /plan: {response[:50]}...")

    # /rest 命令
    response = agent.user_message("/rest 10")
    assert "10" in response or "休息" in response
    print(f"✅ /rest: {response[:50]}...")

    # /back 命令
    response = agent.user_message("/back")
    assert response
    print(f"✅ /back: {response[:50]}...")

    # /deadlines 命令
    response = agent.user_message("/deadlines")
    assert response
    print(f"✅ /deadlines: {response[:50]}...")

    # /help 命令（检查新命令出现在帮助中）
    response = agent.user_message("/help")
    assert "/plan" in response
    assert "/rest" in response
    print(f"✅ /help 包含新命令")

    # 自然语言休息检测
    response = agent.user_message("我想摆烂20分钟")
    assert "休息" in response or "摆烂" in response
    print(f"✅ 自然语言休息: {response[:50]}...")

    print("\n🎉 Dialogue 命令测试全部通过!")


if __name__ == "__main__":
    print("=" * 50)
    print("Attention OS v5.2 功能测试")
    print("=" * 50)

    print("\n--- GoalManager 测试 ---")
    test_goal_manager()

    print("\n--- ActivePlanner 测试 ---")
    test_active_planner()

    print("\n--- Dialogue 命令测试 ---")
    test_dialogue_commands()

    print("\n" + "=" * 50)
    print("全部测试通过! 🚀")
    print("=" * 50)
