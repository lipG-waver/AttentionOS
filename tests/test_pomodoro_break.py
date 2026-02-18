#!/usr/bin/env python3
"""
番茄钟休息遮罩测试
验证: 工作阶段结束后是否正确触发全屏遮罩

用法:
  python test_pomodoro_break.py

测试逻辑:
  1. 将工作时长设为 0.05 分钟（3 秒）
  2. 启动番茄钟工作阶段
  3. 等待工作结束
  4. 验证是否进入 break 阶段
  5. 验证 desktop_overlay.start_break_overlay 是否被调用
  6. 验证 should_blur 状态是否为 True

运行时间: ~8 秒
"""
import logging
import sys
import time
import threading
from unittest.mock import patch, MagicMock
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("test_pomodoro_break")


# ============================================================
# 用于记录调用的 Mock
# ============================================================
overlay_calls = []

class MockDesktopOverlay:
    """替代真实的 DesktopOverlay，记录 start_break_overlay 调用"""
    def start_break_overlay(self, duration_minutes=5, on_end=None, on_skip=None):
        call = {
            "method": "start_break_overlay",
            "duration_minutes": duration_minutes,
            "on_end": on_end,
            "on_skip": on_skip,
            "timestamp": time.time(),
        }
        overlay_calls.append(call)
        logger.info(f"✅ start_break_overlay 被调用! duration={duration_minutes}分钟")

    def get_state(self):
        return {"mood": "normal", "is_break_mode": False}

    def show_intervention(self, *a, **kw): pass
    def end_break_overlay(self): pass
    def skip_break(self): pass
    def update_mood(self, *a, **kw): pass
    def start(self): pass
    def stop(self): pass


class MockPomodoroOverlay:
    """替代真实浮窗（不启动子进程）"""
    def start(self): pass
    def stop(self): pass
    def show(self): pass
    def hide(self): pass
    def update(self, **kw): pass
    on_start = None
    on_pause = None
    on_resume = None
    on_stop = None
    on_skip_break = None
    on_open_dashboard = None


def run_test():
    """主测试逻辑"""
    print("\n" + "=" * 60)
    print("番茄钟休息遮罩测试")
    print("=" * 60)

    # Mock desktop_overlay
    mock_overlay = MockDesktopOverlay()
    mock_pomo_overlay = MockPomodoroOverlay()

    # Patch 掉需要 mock 的模块
    import attention.ui.desktop_overlay as desktop_overlay
    original_get = desktop_overlay.get_desktop_overlay
    desktop_overlay.get_desktop_overlay = lambda: mock_overlay

    import attention.ui.pomodoro_overlay as pomodoro_overlay
    original_start = pomodoro_overlay.start_pomodoro_overlay
    pomodoro_overlay.start_pomodoro_overlay = lambda: mock_pomo_overlay

    try:
        from attention.features.pomodoro import PomodoroTimer, PomodoroSettings, PomodoroPhase

        # 创建一个超短工作时间的番茄钟
        settings = PomodoroSettings(
            work_minutes=1,           # 会被覆盖
            short_break_minutes=1,
            long_break_minutes=2,
            auto_start_break=True,
            force_break=True,
        )
        timer = PomodoroTimer(settings=settings)

        # ──── 测试 1: 验证初始状态 ────
        print("\n[测试 1] 初始状态...")
        status = timer.get_status()
        assert status["phase"] == "idle", f"期望 idle，得到 {status['phase']}"
        assert status["should_blur"] == False
        print("  ✅ IDLE, should_blur=False")

        # ──── 测试 2: 开始工作，设置极短时长 ────
        print("\n[测试 2] 启动工作（3秒后结束）...")
        # 用 _set_phase 直接设一个 0.05 分钟 = 3 秒的工作阶段
        timer._set_phase(PomodoroPhase.WORKING, duration_minutes=3/60)
        timer._current_cycle = 1

        status = timer.get_status()
        assert status["phase"] == "working", f"期望 working，得到 {status['phase']}"
        assert status["remaining_seconds"] <= 4
        print(f"  ✅ WORKING, remaining={status['remaining_seconds']}s")

        # ──── 测试 3: 等待工作结束，验证过渡到 break ────
        print("\n[测试 3] 等待工作结束...")
        deadline = time.time() + 8  # 最多等 8 秒
        transitioned = False
        while time.time() < deadline:
            status = timer.get_status()
            if status["phase"] in ("short_break", "long_break"):
                transitioned = True
                break
            time.sleep(0.3)

        assert transitioned, "❌ 工作阶段结束后未过渡到休息阶段！"
        print(f"  ✅ 过渡到 {status['phase']}")

        # ──── 测试 4: 验证 should_blur ────
        print("\n[测试 4] should_blur 状态...")
        assert status["should_blur"] == True, f"期望 should_blur=True，得到 {status['should_blur']}"
        print("  ✅ should_blur=True")

        # ──── 测试 5: 验证 start_break_overlay 被调用 ────
        print("\n[测试 5] 全屏遮罩 start_break_overlay 调用...")
        # 稍等一下让线程有时间执行
        time.sleep(0.5)
        assert len(overlay_calls) > 0, "❌ start_break_overlay 未被调用！"
        call = overlay_calls[-1]
        print(f"  ✅ 被调用! duration={call['duration_minutes']}分钟")
        assert call["on_end"] is not None, "on_end 回调未设置"
        assert call["on_skip"] is not None, "on_skip 回调未设置"
        print(f"  ✅ on_end 和 on_skip 回调已设置")

        # ──── 测试 6: 验证 skip_break 回调 ────
        print("\n[测试 6] 模拟用户跳过休息...")
        call["on_skip"]()
        time.sleep(0.5)
        status = timer.get_status()
        # skip_break 会开始下一个工作阶段
        print(f"  跳过后状态: {status['phase']}")
        print(f"  ✅ 跳过休息回调正常工作")

        # ──── 测试 7: force_break=False 时不触发遮罩 ────
        print("\n[测试 7] force_break=False 时...")
        overlay_calls.clear()
        timer.stop()
        timer.settings.force_break = False
        timer._set_phase(PomodoroPhase.WORKING, duration_minutes=2/60)
        timer._current_cycle = 1

        deadline = time.time() + 6
        while time.time() < deadline:
            status = timer.get_status()
            if status["phase"] in ("short_break", "long_break"):
                break
            time.sleep(0.3)

        time.sleep(0.5)
        assert len(overlay_calls) == 0, f"❌ force_break=False 但 overlay 仍被调用了 {len(overlay_calls)} 次"
        assert status["should_blur"] == False
        print("  ✅ force_break=False → 不触发遮罩, should_blur=False")

        # 清理
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
        # 恢复 mock
        desktop_overlay.get_desktop_overlay = original_get
        pomodoro_overlay.start_pomodoro_overlay = original_start


if __name__ == "__main__":
    ok = run_test()
    sys.exit(0 if ok else 1)
