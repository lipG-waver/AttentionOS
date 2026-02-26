#!/usr/bin/env python3
"""
番茄钟休息提醒测试

验证: 工作阶段结束后是否正确通过 ChatOverlay 触发休息提醒

用法:
  python tests/test_pomodoro_break.py

测试逻辑:
  1. 验证初始状态为 idle
  2. 启动工作阶段（3秒后结束）
  3. 等待过渡到休息阶段
  4. 验证 should_blur 状态为 True
  5. 验证 ChatOverlay.show_break_reminder() 被调用（替代原全屏遮罩）
  6. 验证 force_break=False 时不触发提醒

运行时间: ~8 秒
"""
import logging
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("test_pomodoro_break")


# ============================================================
# Mock ChatOverlay — 记录 show_break_reminder 调用
# ============================================================

overlay_calls = []


class MockChatOverlay:
    """替代真实的 ChatOverlay，记录 show_break_reminder 调用"""

    def show_break_reminder(self, continuous_minutes: int = 0):
        call = {
            "method": "show_break_reminder",
            "continuous_minutes": continuous_minutes,
            "timestamp": time.time(),
        }
        overlay_calls.append(call)
        logger.info(f"✅ show_break_reminder 被调用!")

    # 其余方法存根
    def update_timer(self, **kw): pass
    def on_focus_started(self, **kw): pass
    def on_focus_ended(self, **kw): pass
    def update_mood(self, **kw): pass
    def update_agent_context(self, **kw): pass
    def is_ready(self): return True
    def _send_ai_message(self, *a, **kw): pass
    on_focus_start = None
    on_focus_pause = None
    on_focus_resume = None
    on_focus_stop = None
    on_skip_break = None


def run_test():
    """主测试逻辑"""
    print("\n" + "=" * 60)
    print("番茄钟休息提醒测试（ChatOverlay 路径）")
    print("=" * 60)

    mock_overlay = MockChatOverlay()

    # chat_overlay 依赖 openai（未安装），用 sys.modules mock 整个模块，
    # 使 PomodoroTimer._trigger_break_overlay() 内部的延迟 import 拿到 mock。
    mock_chat_module = MagicMock()
    mock_chat_module.get_chat_overlay.return_value = mock_overlay
    sys.modules.setdefault("attention.ui.chat_overlay", mock_chat_module)
    original_module = sys.modules.get("attention.ui.chat_overlay")
    sys.modules["attention.ui.chat_overlay"] = mock_chat_module

    try:
        from attention.features.pomodoro import PomodoroTimer, PomodoroSettings, PomodoroPhase

        settings = PomodoroSettings(
            work_minutes=1,
            short_break_minutes=1,
            long_break_minutes=2,
            auto_start_break=True,
            force_break=True,
        )
        timer = PomodoroTimer(settings=settings)

        # ──── 测试 1: 初始状态 ────
        print("\n[测试 1] 初始状态...")
        status = timer.get_status()
        assert status["phase"] == "idle", f"期望 idle，得到 {status['phase']}"
        assert status["should_blur"] == False
        print("  ✅ IDLE, should_blur=False")

        # ──── 测试 2: 启动工作阶段（3 秒超短）────
        print("\n[测试 2] 启动工作（3秒后结束）...")
        timer._set_phase(PomodoroPhase.WORKING, duration_minutes=3 / 60)
        timer._current_cycle = 1

        status = timer.get_status()
        assert status["phase"] == "working", f"期望 working，得到 {status['phase']}"
        assert status["remaining_seconds"] <= 4
        print(f"  ✅ WORKING, remaining={status['remaining_seconds']}s")

        # ──── 测试 3: 等待过渡到休息阶段 ────
        print("\n[测试 3] 等待工作结束...")
        deadline = time.time() + 8
        transitioned = False
        while time.time() < deadline:
            status = timer.get_status()
            if status["phase"] in ("short_break", "long_break"):
                transitioned = True
                break
            time.sleep(0.3)

        assert transitioned, "❌ 工作阶段结束后未过渡到休息阶段！"
        print(f"  ✅ 过渡到 {status['phase']}")

        # ──── 测试 4: should_blur ────
        print("\n[测试 4] should_blur 状态...")
        assert status["should_blur"] == True, f"期望 should_blur=True，得到 {status['should_blur']}"
        print("  ✅ should_blur=True")

        # ──── 测试 5: ChatOverlay.show_break_reminder() 被调用 ────
        print("\n[测试 5] ChatOverlay.show_break_reminder() 调用...")
        time.sleep(0.5)  # 等线程执行
        assert len(overlay_calls) > 0, (
            "❌ show_break_reminder 未被调用！\n"
            "  提示: PomodoroTimer._trigger_break_overlay() 应调用 overlay.show_break_reminder()"
        )
        print(f"  ✅ 被调用 {len(overlay_calls)} 次")

        # ──── 测试 6: force_break=False 时不触发 ────
        print("\n[测试 6] force_break=False 时不触发提醒...")
        overlay_calls.clear()
        timer.stop()
        timer.settings.force_break = False
        timer._set_phase(PomodoroPhase.WORKING, duration_minutes=2 / 60)
        timer._current_cycle = 1

        deadline = time.time() + 6
        while time.time() < deadline:
            status = timer.get_status()
            if status["phase"] in ("short_break", "long_break"):
                break
            time.sleep(0.3)

        time.sleep(0.5)
        assert len(overlay_calls) == 0, (
            f"❌ force_break=False 但 show_break_reminder 仍被调用了 {len(overlay_calls)} 次"
        )
        assert status["should_blur"] == False
        print("  ✅ force_break=False → 不触发提醒, should_blur=False")

        timer.stop()

        # ──── 结果 ────
        print("\n" + "=" * 60)
        print("🎉 所有测试通过!")
        print("=" * 60 + "\n")
        return True

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        return False
    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if original_module is not None:
            sys.modules["attention.ui.chat_overlay"] = original_module
        else:
            sys.modules.pop("attention.ui.chat_overlay", None)


if __name__ == "__main__":
    ok = run_test()
    sys.exit(0 if ok else 1)
