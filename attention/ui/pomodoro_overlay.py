"""
番茄钟浮窗模块 — 始终悬浮在桌面的迷你控制器

设计：
  - 子进程 (pomodoro_overlay_process.py) 负责 GUI 渲染
  - 本模块负责：启动子进程、转发状态更新、接收用户操作并回调给 PomodoroTimer
  - 浮窗始终可见（IDLE 时显示"开始"按钮，工作/休息时显示倒计时+控制按钮）

通信协议 (父→子 stdin, JSON):
  {"cmd":"update","time":"24:30","phase":"working","phase_label":"专注工作中",
   "color":"#34d399","cycle":1,"total_cycles":4}
  {"cmd":"quit"}

子→父 stdout:
  "ready"                — 子进程就绪
  "action:start"         — 用户点击开始
  "action:pause"         — 用户点击暂停
  "action:resume"        — 用户点击继续
  "action:stop"          — 用户点击停止
  "action:skip_break"    — 用户点击跳过休息
  "action:open_dashboard"— 用户双击打开仪表盘
"""
import json
import logging
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)
SYSTEM = platform.system()


class PomodoroFloatingWindow:
    """
    番茄钟浮窗 — 始终悬浮、带交互按钮的迷你控制器。
    通过独立子进程运行 GUI，避免 macOS tkinter 线程崩溃。
    """

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._running = False

        # 缓存最新状态（用于子进程重启时恢复）
        self._last_update: Optional[dict] = None

        # 操作回调 — 由 PomodoroTimer 设置
        self.on_start: Optional[Callable] = None
        self.on_pause: Optional[Callable] = None
        self.on_resume: Optional[Callable] = None
        self.on_stop: Optional[Callable] = None
        self.on_skip_break: Optional[Callable] = None
        self.on_open_dashboard: Optional[Callable] = None

    def start(self):
        """启动浮窗子进程"""
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._spawn_process, daemon=True).start()
        logger.info("番茄钟浮窗启动中...")

    def stop(self):
        """停止浮窗"""
        self._running = False
        self._send_cmd({"cmd": "quit"})
        time.sleep(0.3)
        self._kill_proc()
        logger.info("番茄钟浮窗已停止")

    def update(self, time_text: str, phase: str, phase_label: str,
               color: str = "#34d399", cycle: int = 0, total_cycles: int = 4):
        """
        更新浮窗显示。

        Args:
            time_text:    "24:30"
            phase:        "idle" / "working" / "paused" / "short_break" / "long_break"
            phase_label:  "专注工作中" / "空闲" / ...
            color:        主题色 hex
            cycle:        当前第几个番茄
            total_cycles: 一组几个
        """
        cmd = {
            "cmd": "update",
            "time": time_text,
            "phase": phase,
            "phase_label": phase_label,
            "color": color,
            "cycle": cycle,
            "total_cycles": total_cycles,
        }
        self._last_update = cmd
        self._send_cmd(cmd)

    # ─── 兼容旧接口 ───

    def show(self):
        """兼容旧接口 — 浮窗始终可见，此方法为空操作"""
        pass

    def hide(self):
        """兼容旧接口 — 浮窗始终可见，此方法为空操作"""
        pass

    # ─── 内部 ───

    def _spawn_process(self):
        script = Path(__file__).parent / "pomodoro_overlay_process.py"
        if not script.exists():
            logger.warning(f"浮窗子进程脚本不存在: {script}")
            return

        max_retries = 3
        for attempt in range(max_retries):
            try:
                import os as _os
                env = _os.environ.copy()
                env["PYTHONUNBUFFERED"] = "1"

                self._proc = subprocess.Popen(
                    [sys.executable, str(script)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                logger.info(f"番茄钟浮窗子进程 PID={self._proc.pid}")

                # 等待就绪
                ready = False
                deadline = time.time() + 5
                while time.time() < deadline:
                    if self._proc.poll() is not None:
                        break
                    line = self._proc.stdout.readline()
                    if line:
                        msg = line.strip()
                        if msg == "ready":
                            ready = True
                            break
                        elif msg.startswith("action:"):
                            self._dispatch_action(msg)

                if not ready:
                    # 子进程可能崩溃了，先终止再读 stderr
                    stderr_out = ""
                    try:
                        self._proc.kill()
                        self._proc.wait(timeout=2)
                    except Exception:
                        pass
                    try:
                        stderr_out = self._proc.stderr.read()
                    except Exception:
                        pass
                    logger.warning(
                        f"浮窗子进程未就绪 (attempt {attempt+1}/{max_retries})"
                        + (f"\n  stderr: {stderr_out.strip()[:300]}" if stderr_out and stderr_out.strip() else "")
                    )
                    self._proc = None
                    if attempt < max_retries - 1:
                        time.sleep(1)
                    continue

                # 成功 — 启动 stdout 监听
                threading.Thread(target=self._watch_stdout, daemon=True).start()

                # 启动 stderr 监听（诊断用）
                threading.Thread(target=self._watch_stderr, daemon=True).start()

                # 恢复上次状态
                if self._last_update:
                    self._send_cmd(self._last_update)

                logger.info("番茄钟浮窗已就绪")
                return

            except Exception as e:
                logger.error(f"启动浮窗子进程失败 (attempt {attempt+1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)

        logger.warning("番茄钟浮窗不可用（不影响核心功能）")

    def _watch_stdout(self):
        """监听子进程输出，分发操作事件"""
        try:
            for line in self._proc.stdout:
                msg = line.strip()
                if msg.startswith("action:"):
                    self._dispatch_action(msg)
        except Exception:
            pass

        # 子进程退出后自动重启（除非正在关闭）
        if self._running:
            logger.info("浮窗子进程意外退出，2秒后尝试重启...")
            time.sleep(2)
            self._kill_proc()
            self._spawn_process()

    def _watch_stderr(self):
        """读取子进程 stderr 输出作为诊断日志"""
        try:
            for line in self._proc.stderr:
                msg = line.strip()
                if msg:
                    logger.debug(f"[浮窗子进程] {msg}")
        except Exception:
            pass

    def _dispatch_action(self, msg: str):
        """分发用户操作"""
        action = msg.split(":", 1)[1] if ":" in msg else ""
        logger.debug(f"浮窗操作: {action}")

        callback_map = {
            "start": self.on_start,
            "pause": self.on_pause,
            "resume": self.on_resume,
            "stop": self.on_stop,
            "skip_break": self.on_skip_break,
            "open_dashboard": self.on_open_dashboard,
        }

        cb = callback_map.get(action)
        if cb:
            try:
                # 在新线程中执行回调，避免阻塞 stdout 读取
                threading.Thread(target=cb, daemon=True).start()
            except Exception as e:
                logger.error(f"执行浮窗回调 {action} 失败: {e}")
        elif action == "open_dashboard":
            # 默认行为
            try:
                import webbrowser
                webbrowser.open("http://127.0.0.1:5000")
            except Exception:
                pass

    def _send_cmd(self, cmd: dict):
        with self._lock:
            proc = self._proc
            if proc and proc.poll() is None:
                try:
                    proc.stdin.write(json.dumps(cmd, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass

    def _kill_proc(self):
        with self._lock:
            if self._proc:
                try:
                    if self._proc.poll() is None:
                        self._proc.kill()
                        self._proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None


# ==================== 单例 ====================

_overlay: Optional[PomodoroFloatingWindow] = None


def get_pomodoro_overlay() -> PomodoroFloatingWindow:
    global _overlay
    if _overlay is None:
        _overlay = PomodoroFloatingWindow()
    return _overlay


def start_pomodoro_overlay() -> PomodoroFloatingWindow:
    overlay = get_pomodoro_overlay()
    overlay.start()
    return overlay


def stop_pomodoro_overlay():
    global _overlay
    if _overlay:
        _overlay.stop()
