"""
统一对话悬浮窗管理器 — Attention OS 的交互中枢

职责：
  1. 启动/管理 chat_overlay_process.py 子进程
  2. 接收用户消息 → 路由到 DialogueAgent → 返回 AI 回复
  3. 接收系统事件（分心、休息等）→ 生成主动对话
  4. 管理番茄钟计时器状态推送
  5. 触发对话日志保存

替代原有的：
  - desktop_overlay.py（介入弹窗）
  - pomodoro_overlay.py（番茄钟浮窗）
  - 各种 AppleScript 对话框
"""
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Dict, Any

from attention.core.dialogue_agent import get_dialogue_agent, DialogueAgent
from attention.features.chat_logger import save_chat_log

logger = logging.getLogger(__name__)


class ChatOverlay:
    """
    统一对话悬浮窗管理器。

    通过独立子进程 (chat_overlay_process.py) 运行 GUI，
    父子进程通过 stdin/stdout JSON 通信。
    """

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._running = False
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._force_headless = False
        self._rapid_crash_count = 0

        # 对话 Agent
        self._agent: DialogueAgent = get_dialogue_agent()

        # 番茄钟回调
        self.on_focus_start: Optional[Callable] = None
        self.on_focus_pause: Optional[Callable] = None
        self.on_focus_resume: Optional[Callable] = None
        self.on_focus_stop: Optional[Callable] = None
        self.on_skip_break: Optional[Callable] = None

        # 介入冷却
        self._last_nudge_time: Optional[datetime] = None
        self._nudge_cooldown = 120  # 秒

        # 日志保存定时器
        self._last_log_save = None

    # ================================================================ #
    #  生命周期
    # ================================================================ #

    def start(self):
        """启动对话悬浮窗"""
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._spawn_process, daemon=True).start()
        logger.info("对话悬浮窗启动中...")

    def stop(self):
        """停止悬浮窗"""
        self._running = False

        # 保存对话日志
        self._save_log()

        # 通知子进程退出
        self._send({"cmd": "quit"})
        time.sleep(0.3)
        self._kill_proc()
        logger.info("对话悬浮窗已停止")

    def is_ready(self) -> bool:
        return self._ready.is_set()

    # ================================================================ #
    #  对外 API — 系统事件
    # ================================================================ #

    def show_nudge(self, reason: str, fused_state: Optional[dict] = None):
        """
        显示分心提醒（通过对话方式）。
        替代原 DesktopOverlay.show_intervention()。
        """
        now = datetime.now()
        if self._last_nudge_time:
            elapsed = (now - self._last_nudge_time).total_seconds()
            if elapsed < self._nudge_cooldown:
                logger.debug(f"提醒冷却中，还剩 {self._nudge_cooldown - elapsed:.0f}s")
                return
        self._last_nudge_time = now

        # 通过 DialogueAgent 生成提醒
        msg = self._agent.proactive_nudge(reason, fused_state)
        self._send_ai_message(msg, msg_type="nudge")
        logger.info(f"发送分心提醒: {msg[:50]}...")

    def show_break_reminder(self):
        """休息提醒（通过对话方式）"""
        msg = self._agent.proactive_break_chat()
        self._send_ai_message(msg, msg_type="status")

    def on_focus_started(self, task: str, duration_min: int):
        """专注模式开始 — 发送欢迎消息"""
        msg = self._agent.focus_start_message(task, duration_min)
        self._send_ai_message(msg, msg_type="status")
        self._agent.update_context(
            is_focus_mode=True,
            focus_task=task,
            focus_remaining_seconds=duration_min * 60,
        )

    def on_focus_ended(self, task: str, duration_min: int, completed: bool):
        """专注模式结束 — 发送总结消息"""
        msg = self._agent.focus_end_message(task, duration_min, completed)
        self._send_ai_message(msg, msg_type="status")
        self._agent.update_context(
            is_focus_mode=False,
            focus_task="",
            focus_remaining_seconds=0,
        )

    def update_timer(self, time_text: str, phase: str, progress: float):
        """更新计时器显示（番茄钟/休息）"""
        self._send({
            "cmd": "update_timer",
            "time": time_text,
            "phase": phase,
            "progress": progress,
        })
        # 同步更新 agent 上下文
        if phase == "working":
            parts = time_text.split(":")
            if len(parts) == 2:
                try:
                    remaining = int(parts[0]) * 60 + int(parts[1])
                    self._agent.update_context(focus_remaining_seconds=remaining)
                except ValueError:
                    pass

    def update_mood(self, is_productive: bool, is_distracted: bool,
                    attention_level: str):
        """根据工作状态更新小球表情"""
        if is_productive:
            mood = "happy"
        elif is_distracted:
            mood = "worried"
        else:
            mood = "normal"
        self._send({"cmd": "set_mood", "mood": mood})

    def update_agent_context(self, **kwargs):
        """更新对话 Agent 的上下文"""
        self._agent.update_context(**kwargs)

    def show_plan_message(self, message: str):
        """v5.2: 显示计划相关消息（主动引导）"""
        self._send_ai_message(message, msg_type="plan")
        logger.info(f"发送计划消息: {message[:50]}...")

    def show_rest_timer(self, remaining_minutes: int):
        """v5.2: 更新休息倒计时显示"""
        self._send({
            "cmd": "update_rest_timer",
            "remaining_minutes": remaining_minutes,
        })

    def get_agent(self) -> DialogueAgent:
        """获取对话 Agent 实例"""
        return self._agent

    # ================================================================ #
    #  内部 — 子进程管理
    # ================================================================ #

    def _spawn_process(self):
        """启动子进程"""
        script = Path(__file__).parent / "chat_overlay_process.py"

        while self._running:
            start_at = time.time()
            try:
                self._ready.clear()
                self._stderr_tail.clear()
                child_env = os.environ.copy()
                if self._force_headless:
                    child_env["ATTENTION_OS_CHAT_OVERLAY_FORCE_HEADLESS"] = "1"

                self._proc = subprocess.Popen(
                    [sys.executable, str(script)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=child_env,
                )
                logger.info(f"对话悬浮窗子进程启动 (PID={self._proc.pid})")

                # 后台线程读取 stderr 日志
                threading.Thread(
                    target=self._read_stderr,
                    args=(self._proc,),
                    daemon=True,
                ).start()

                # 读取子进程输出
                self._read_loop()

                # 输出退出原因，便于定位偶发崩溃
                proc = self._proc
                if proc is not None:
                    self._proc = None
                return_code = proc.returncode if proc else None
                uptime = time.time() - start_at
                if self._running:
                    tail = "\n".join(self._stderr_tail)
                    logger.warning(
                        "对话悬浮窗子进程异常退出 (code=%s, uptime=%.1fs)\n最近 stderr:\n%s",
                        return_code,
                        uptime,
                        tail if tail else "<empty>",
                    )

                    # macOS 下若 tkinter 快速崩溃（常见 NSException/SIGABRT），自动降级 headless
                    if (
                        platform.system() == "Darwin"
                        and not self._force_headless
                        and return_code in (-6, 134)
                        and uptime < 3
                    ):
                        self._rapid_crash_count += 1
                        if self._rapid_crash_count >= 2:
                            self._force_headless = True
                            logger.warning(
                                "检测到 macOS tkinter 子进程连续崩溃，已自动降级为 headless 模式以停止重启风暴"
                            )
                    else:
                        self._rapid_crash_count = 0

            except Exception as e:
                logger.error(f"启动子进程失败: {e}")

            # 如果仍在运行，尝试重启
            if self._running:
                logger.warning("子进程退出，2 秒后重启...")
                time.sleep(2)

    def _read_stderr(self, proc):
        """读取子进程 stderr 用于调试"""
        try:
            for line in proc.stderr:
                line = line.strip()
                if line:
                    self._stderr_tail.append(line)
                    logger.debug(f"[overlay子进程] {line}")
        except Exception:
            pass

    def _read_loop(self):
        """读取子进程 stdout，处理消息"""
        proc = self._proc
        if not proc:
            return

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug(f"子进程非 JSON: {line}")
                    continue

                self._handle_child_message(msg)

        except Exception as e:
            if self._running:
                logger.warning(f"读取子进程失败: {e}")
        finally:
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    def _handle_child_message(self, msg: dict):
        """处理子进程发来的消息"""
        msg_type = msg.get("type", "")

        if msg_type == "ready":
            self._ready.set()
            self._rapid_crash_count = 0
            logger.info("对话悬浮窗已就绪")

            # 发送欢迎消息
            threading.Thread(target=self._send_welcome, daemon=True).start()

        elif msg_type == "user_message":
            text = msg.get("text", "")
            if text:
                # 异步处理用户消息
                threading.Thread(
                    target=self._process_user_message,
                    args=(text,),
                    daemon=True
                ).start()

        elif msg_type == "action":
            action = msg.get("action", "")
            self._handle_action(action)

        elif msg_type == "expand":
            logger.debug("用户展开了对话窗")

        elif msg_type == "collapse":
            logger.debug("用户收起了对话窗")
            # 定期保存日志
            self._maybe_save_log()

    def _process_user_message(self, text: str):
        """处理用户消息（异步，在后台线程）"""
        try:
            response = self._agent.user_message(text)
            if response:
                self._send_ai_message(response)
        except Exception as e:
            logger.error(f"处理用户消息失败: {e}")
            self._send_ai_message("抱歉，出了点小问题。不过你的消息已记录 📝")

    def _handle_action(self, action: str):
        """处理用户操作"""
        callbacks = {
            "start_focus": self.on_focus_start,
            "pause": self.on_focus_pause,
            "resume": self.on_focus_resume,
            "stop": self.on_focus_stop,
            "skip_break": self.on_skip_break,
        }
        callback = callbacks.get(action)
        if callback:
            try:
                callback()
            except Exception as e:
                logger.error(f"执行操作 {action} 失败: {e}")

    def _send_welcome(self):
        """发送欢迎消息"""
        time.sleep(0.5)  # 等待 UI 就绪
        hour = datetime.now().hour
        if hour < 6:
            greeting = "🌙 深夜了，注意休息哦！"
        elif hour < 12:
            greeting = "☀️ 早上好！准备好开始高效的一天了吗？"
        elif hour < 14:
            greeting = "🌤 中午好！午饭吃了吗？"
        elif hour < 18:
            greeting = "☕ 下午好！继续加油！"
        else:
            greeting = "🌆 晚上好！还在忙吗？"

        self._send_ai_message(greeting, msg_type="status")

    # ================================================================ #
    #  内部 — 通信
    # ================================================================ #

    def _send(self, data: dict):
        """发送命令到子进程"""
        with self._lock:
            proc = self._proc
            if proc and proc.poll() is None:
                try:
                    proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
                    proc.stdin.flush()
                except Exception as e:
                    logger.debug(f"发送消息失败: {e}")

    def _send_ai_message(self, text: str, msg_type: str = "chat"):
        """发送 AI 消息到子进程显示"""
        self._send({
            "cmd": "ai_message",
            "text": text,
            "type": msg_type,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    def _kill_proc(self):
        """强制结束子进程"""
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None

    # ================================================================ #
    #  日志保存
    # ================================================================ #

    def _maybe_save_log(self):
        """如果距上次保存超过 5 分钟，保存日志"""
        now = datetime.now()
        if self._last_log_save:
            elapsed = (now - self._last_log_save).total_seconds()
            if elapsed < 300:
                return
        self._save_log()

    def _save_log(self):
        """保存对话日志"""
        try:
            messages = self._agent.get_history_for_export()
            if messages:
                save_chat_log(messages)
                self._last_log_save = datetime.now()
        except Exception as e:
            logger.warning(f"保存对话日志失败: {e}")


# ================================================================ #
#  单例
# ================================================================ #

_chat_overlay: Optional[ChatOverlay] = None


def get_chat_overlay() -> ChatOverlay:
    """获取对话悬浮窗单例"""
    global _chat_overlay
    if _chat_overlay is None:
        _chat_overlay = ChatOverlay()
    return _chat_overlay


def start_chat_overlay() -> ChatOverlay:
    """启动对话悬浮窗"""
    overlay = get_chat_overlay()
    overlay.start()
    return overlay


def stop_chat_overlay():
    """停止对话悬浮窗"""
    global _chat_overlay
    if _chat_overlay:
        _chat_overlay.stop()
