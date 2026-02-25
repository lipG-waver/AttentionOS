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
import subprocess
import sys
import threading
import time
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

        # 一次性签到回调：当有待处理的签到问题时，下一条用户消息路由到此回调
        self._pending_checkin_callback: Optional[Callable[[str], None]] = None
        self._pending_checkin_lock = threading.Lock()

        # macOS 快速崩溃检测
        self._quick_crash_count = 0
        self._force_headless = False
        self._QUICK_CRASH_THRESHOLD = 3  # 连续快速崩溃次数阈值
        self._QUICK_CRASH_UPTIME = 2.0   # 秒，小于此时间视为快速崩溃
        self._last_stderr_lines = []

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

    def show_break_reminder(self, continuous_minutes: int = 0):
        """休息提醒（通过对话方式），continuous_minutes 为实际连续工作分钟数"""
        msg = self._agent.proactive_break_chat(continuous_minutes=continuous_minutes)
        self._send_ai_message(msg, msg_type="status")

    def on_focus_started(self, task: str, duration_min: int):
        """专注模式开始 — 发送欢迎消息，切换到专注模式标签"""
        msg = self._agent.focus_start_message(task, duration_min)
        self._send_ai_message(msg, msg_type="status")
        self._agent.update_context(
            is_focus_mode=True,
            focus_task=task,
            focus_remaining_seconds=duration_min * 60,
        )
        # 自动切换悬浮窗到专注模式标签
        self._send({"cmd": "set_mode", "mode": "focus"})

    def on_focus_ended(self, task: str, duration_min: int, completed: bool):
        """专注模式结束 — 发送总结消息，切回 AI 对话标签"""
        msg = self._agent.focus_end_message(task, duration_min, completed)
        self._send_ai_message(msg, msg_type="status")
        self._agent.update_context(
            is_focus_mode=False,
            focus_task="",
            focus_remaining_seconds=0,
        )
        # 切回 AI 对话标签
        self._send({"cmd": "set_mode", "mode": "ai"})

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

    def show_checkin_prompt(self, callback: Callable[[str], None], prompt_text: str = ""):
        """
        在悬浮对话框内发起一次签到问答。

        发送签到提问消息，并注册一次性回调：下一条用户输入将路由到 callback
        而非 DialogueAgent。callback 接收原始文本，由调用方解析内容与情绪。

        Args:
            callback: 接收用户回复文本的一次性回调
            prompt_text: 自定义提问文本，默认使用标准签到问句
        """
        text = prompt_text or "⏰ 整点签到～过去一小时你在做什么？感觉怎么样？"
        with self._pending_checkin_lock:
            self._pending_checkin_callback = callback
        self._send_ai_message(text, msg_type="checkin")
        logger.info("发送签到提问，等待用户回复...")

    def get_agent(self) -> DialogueAgent:
        """获取对话 Agent 实例"""
        return self._agent

    # ================================================================ #
    #  内部 — 子进程管理
    # ================================================================ #

    def _spawn_process(self):
        """启动子进程（含快速崩溃检测 → headless 回退）"""
        script = Path(__file__).parent / "chat_overlay_process.py"

        while self._running:
            try:
                cmd = [sys.executable, str(script)]
                if self._force_headless:
                    cmd.append("--headless")

                spawn_time = time.monotonic()

                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                logger.info(
                    f"对话悬浮窗子进程启动 "
                    f"(PID={self._proc.pid}, force_headless={self._force_headless})"
                )

                # 后台线程读取 stderr 日志
                threading.Thread(
                    target=self._read_stderr,
                    args=(self._proc,),
                    daemon=True,
                ).start()

                # 读取子进程输出（阻塞直到子进程退出）
                self._read_loop()

                # ── 子进程退出后，检测是否快速崩溃 ──
                uptime = time.monotonic() - spawn_time
                exit_code = self._proc.returncode if self._proc else None

                if uptime < self._QUICK_CRASH_UPTIME and exit_code != 0:
                    self._quick_crash_count += 1
                    logger.warning(
                        f"对话悬浮窗子进程异常退出 "
                        f"(code={exit_code}, uptime={uptime:.1f}s)"
                    )
                    logger.warning(
                        f"检测到 macOS 快速崩溃 "
                        f"({self._quick_crash_count}/{self._QUICK_CRASH_THRESHOLD}): "
                        f"code={exit_code}, uptime={uptime:.1f}s"
                    )

                    if self._quick_crash_count >= self._QUICK_CRASH_THRESHOLD:
                        logger.warning(
                            "检测到连续快速崩溃，已自动切换为 headless 模式重启子进程。"
                        )
                        self._force_headless = True
                else:
                    # 正常运行后重置计数器
                    self._quick_crash_count = 0

            except Exception as e:
                logger.error(f"启动子进程失败: {e}")

            # 如果仍在运行，尝试重启
            if self._running:
                logger.warning("子进程退出，2 秒后重启...")
                time.sleep(2)

    def _read_stderr(self, proc):
        """读取子进程 stderr 用于调试，并缓存最近的输出用于崩溃诊断"""
        recent_lines = []
        try:
            for line in proc.stderr:
                line = line.strip()
                if line:
                    logger.debug(f"[overlay子进程] {line}")
                    recent_lines.append(line)
                    if len(recent_lines) > 30:
                        recent_lines.pop(0)
        except Exception:
            pass
        # 存储到实例供崩溃诊断使用
        self._last_stderr_lines = recent_lines

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
            self._proc = None

    def _handle_child_message(self, msg: dict):
        """处理子进程发来的消息"""
        msg_type = msg.get("type", "")

        if msg_type == "ready":
            self._ready.set()
            logger.info("对话悬浮窗已就绪")

            # 发送欢迎消息
            threading.Thread(target=self._send_welcome, daemon=True).start()

        elif msg_type == "user_message":
            text = msg.get("text", "")
            mode = msg.get("mode", "ai")
            if text:
                # 异步处理用户消息
                threading.Thread(
                    target=self._process_user_message,
                    args=(text, mode),
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

    def _process_user_message(self, text: str, mode: str = "ai"):
        """处理用户消息（异步，在后台线程）"""
        try:
            # 优先检查是否有待处理的签到回调
            checkin_callback = None
            with self._pending_checkin_lock:
                if self._pending_checkin_callback is not None:
                    checkin_callback = self._pending_checkin_callback
                    self._pending_checkin_callback = None

            if checkin_callback is not None:
                try:
                    checkin_callback(text)
                except Exception as e:
                    logger.error(f"签到回调执行失败: {e}")
                return  # 签到回复不再经过 DialogueAgent

            if mode == "memo":
                response = self._handle_memo_save(text)
            elif mode == "focus":
                response = self._agent.capture_thought(text)
            else:
                response = self._agent.user_message(text)
            if response:
                self._send_ai_message(response)
        except Exception as e:
            logger.error(f"处理用户消息失败: {e}")
            self._send_ai_message("抱歉，出了点小问题。不过你的消息已记录 📝")

    def _handle_memo_save(self, text: str) -> str:
        """将文本保存为随手记 Markdown 文件"""
        try:
            from attention.config import Config
            from datetime import datetime as _dt
            memo_dir = Config.DATA_DIR / "memos"
            memo_dir.mkdir(parents=True, exist_ok=True)
            timestamp = _dt.now().strftime("%Y-%m-%d_%H-%M-%S")
            filepath = memo_dir / f"memo_{timestamp}.md"
            md_content = f"# 随手记 {_dt.now().strftime('%Y-%m-%d %H:%M')}\n\n{text}\n"
            filepath.write_text(md_content, encoding="utf-8")
            logger.info(f"随手记已保存: {filepath.name}")
            return f"✅ 已保存到随手记 📝"
        except Exception as e:
            logger.error(f"保存随手记失败: {e}")
            return "❌ 保存失败，请稍后再试"

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
                # 同步专注模式状态到 Agent 上下文
                if action == "pause":
                    self._agent.update_context(is_focus_mode=False)
                elif action == "resume":
                    self._agent.update_context(is_focus_mode=True)
                elif action == "stop":
                    self._agent.update_context(
                        is_focus_mode=False,
                        focus_task="",
                        focus_remaining_seconds=0,
                    )
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
