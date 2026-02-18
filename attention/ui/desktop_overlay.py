"""
桌面悬浮窗模块
提供两种形态：
1. 桌面小精灵（Pet）：始终在最上层的小窗口，显示当前状态和提醒
2. 全屏休息遮罩（Break Overlay）：强制休息时覆盖整个屏幕

macOS: 使用 PyObjC (AppKit/Cocoa) 实现原生窗口
其他平台: 使用 tkinter 实现
"""
import logging
import platform
import sys
import threading
import time
import random
import subprocess
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SYSTEM = platform.system()


@dataclass
class PetState:
    """小精灵状态"""
    mood: str = "normal"          # normal / happy / worried / sleeping / alert
    message: str = ""             # 当前显示的消息
    message_type: str = "info"    # info / warning / intervention / break
    show_message: bool = False    # 是否显示消息气泡
    is_break_mode: bool = False   # 是否处于休息遮罩模式
    break_remaining: int = 0      # 休息剩余秒数
    blink_alert: bool = False     # 是否闪烁警告


# ============================================================
# 跨平台接口
# ============================================================

class DesktopOverlay:
    """
    桌面悬浮窗管理器（跨平台接口）
    """
    
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._state = PetState()
        self._impl = None  # 平台实现
        self._lock = threading.Lock()
        
        # 消息队列
        self._pending_messages = []
        
        # 休息遮罩子进程
        self._overlay_proc = None
        
        # 休息回调
        self._on_break_end: Optional[Callable] = None
        self._on_break_skip: Optional[Callable] = None
        
        # 介入提醒冷却
        self._last_intervention_time: Optional[datetime] = None
        self._intervention_cooldown = 120  # 2分钟冷却
    
    def start(self):
        """启动悬浮窗"""
        if self._running:
            return
        
        self._running = True
        
        if SYSTEM == "Darwin":
            self._thread = threading.Thread(target=self._run_macos, daemon=True)
        else:
            self._thread = threading.Thread(target=self._run_tkinter, daemon=True)
        
        self._thread.start()
        logger.info(f"桌面悬浮窗已启动 (平台: {SYSTEM})")
    
    def stop(self):
        """停止悬浮窗"""
        self._running = False
        
        # 杀掉遮罩子进程
        if self._overlay_proc and self._overlay_proc.poll() is None:
            try:
                self._overlay_proc.kill()
            except:
                pass
            self._overlay_proc = None
        
        if self._impl:
            try:
                self._impl.close()
            except:
                pass
        logger.info("桌面悬浮窗已停止")
    
    # ---- 公开API ----
    
    def show_intervention(self, reason: str, style: str = "encouraging"):
        """
        显示介入提醒（小精灵弹出消息气泡）
        
        Args:
            reason: 介入原因
            style: 提醒风格
        """
        # 冷却检查
        now = datetime.now()
        if self._last_intervention_time:
            elapsed = (now - self._last_intervention_time).total_seconds()
            if elapsed < self._intervention_cooldown:
                logger.debug(f"介入提醒冷却中，还剩{self._intervention_cooldown - elapsed:.0f}秒")
                return
        
        self._last_intervention_time = now
        
        # 生成友好提醒消息
        message = self._generate_intervention_message(reason, style)
        
        with self._lock:
            self._state.mood = "worried"
            self._state.message = message
            self._state.message_type = "intervention"
            self._state.show_message = True
            self._state.blink_alert = True
        
        # 同时弹出系统对话框（确保用户看到）
        threading.Thread(
            target=self._show_intervention_dialog,
            args=(reason, message),
            daemon=True
        ).start()
        
        logger.info(f"显示介入提醒: {reason}")
    
    def start_break_overlay(
        self, 
        duration_minutes: int = 5, 
        on_end: Optional[Callable] = None,
        on_skip: Optional[Callable] = None
    ):
        """
        启动全屏休息遮罩
        
        Args:
            duration_minutes: 休息时长（分钟）
            on_end: 休息结束回调
            on_skip: 跳过休息回调
        """
        self._on_break_end = on_end
        self._on_break_skip = on_skip
        
        with self._lock:
            self._state.is_break_mode = True
            self._state.break_remaining = duration_minutes * 60
            self._state.mood = "sleeping"
            self._state.message = "休息时间到了，让眼睛和大脑放松一下 ☁️"
            self._state.show_message = True
        
        # 启动倒计时线程
        threading.Thread(target=self._break_countdown, daemon=True).start()
        
        # 启动全屏遮罩
        threading.Thread(
            target=self._show_break_overlay_native,
            args=(duration_minutes,),
            daemon=True
        ).start()
        
        logger.info(f"启动休息遮罩: {duration_minutes}分钟")
    
    def end_break_overlay(self):
        """结束休息遮罩"""
        with self._lock:
            self._state.is_break_mode = False
            self._state.break_remaining = 0
            self._state.mood = "happy"
            self._state.message = "休息结束！精力已恢复 ✨"
            self._state.show_message = True
        
        # 通知子进程关闭
        self._send_overlay_command("skip")
        
        if self._on_break_end:
            self._on_break_end()
    
    def skip_break(self):
        """跳过休息"""
        with self._lock:
            self._state.is_break_mode = False
            self._state.break_remaining = 0
            self._state.mood = "normal"
            self._state.message = ""
            self._state.show_message = False
        
        # 通知子进程关闭
        self._send_overlay_command("skip")
        
        if self._on_break_skip:
            self._on_break_skip()
    
    def update_mood(self, is_productive: bool, is_distracted: bool, attention_level: str):
        """根据工作状态更新小精灵心情"""
        with self._lock:
            if self._state.is_break_mode:
                return  # 休息模式不更新
            
            if is_productive:
                self._state.mood = "happy"
                self._state.blink_alert = False
            elif is_distracted:
                self._state.mood = "worried"
            else:
                self._state.mood = "normal"
                self._state.blink_alert = False
    
    def get_state(self) -> Dict[str, Any]:
        """获取当前状态"""
        with self._lock:
            return {
                "mood": self._state.mood,
                "message": self._state.message,
                "message_type": self._state.message_type,
                "show_message": self._state.show_message,
                "is_break_mode": self._state.is_break_mode,
                "break_remaining": self._state.break_remaining,
                "blink_alert": self._state.blink_alert,
            }
    
    # ---- 内部方法 ----
    
    def _generate_intervention_message(self, reason: str, style: str) -> str:
        """生成介入提醒消息"""
        encouraging_messages = {
            "持续处于娱乐状态": [
                "🎯 嘿，注意到你休息了一会儿，要不要切回工作状态？",
                "💡 适当放松很好，不过时间差不多了，继续冲！",
                "🌟 休息够了吗？你之前的工作状态很棒哦！",
                "⏰ 娱乐时间到啦，你的任务还在等你呢~",
            ],
            "频繁切换窗口": [
                "🧘 我注意到你在频繁切换窗口，要不试试专注在一件事上？",
                "🎯 一次做好一件事，效率会更高哦！",
                "💭 切换太频繁会消耗注意力，深呼吸，选一个任务继续吧。",
            ],
            "注意力分散到娱乐内容": [
                "👀 注意到你在看一些有趣的内容，要不先把手头的事做完？",
                "🎯 先完成当前任务，之后可以奖励自己放松一下~",
                "💪 再坚持一下！完成之后尽情娱乐。",
            ],
        }
        
        # 尝试匹配原因
        for key, messages in encouraging_messages.items():
            if key in reason:
                return random.choice(messages)
        
        # 通用消息
        return random.choice([
            f"🔔 小提醒：{reason}",
            f"💡 注意到一些情况：{reason}，要不要调整一下？",
            f"🎯 {reason}，试试重新聚焦吧！",
        ])
    
    def _break_countdown(self):
        """休息倒计时"""
        while self._running and self._state.is_break_mode and self._state.break_remaining > 0:
            time.sleep(1)
            with self._lock:
                if self._state.is_break_mode:
                    self._state.break_remaining = max(0, self._state.break_remaining - 1)
                else:
                    return
        
        # 倒计时结束
        if self._state.is_break_mode:
            self.end_break_overlay()
    
    def _show_intervention_dialog(self, reason: str, message: str):
        """显示介入提醒对话框（系统原生）"""
        if SYSTEM == "Darwin":
            self._show_intervention_dialog_macos(reason, message)
        elif SYSTEM == "Windows":
            self._show_intervention_dialog_windows(reason, message)
        else:
            self._show_intervention_dialog_linux(reason, message)
    
    def _show_intervention_dialog_macos(self, reason: str, message: str):
        """macOS: AppleScript介入提醒对话框"""
        # 清理消息中的特殊字符
        clean_msg = message.replace('"', '\\"').replace("'", "\\'")
        
        script = f'''
        tell application "System Events"
            activate
            display dialog "{clean_msg}" with title "🎯 注意力提醒" buttons {{"继续摸鱼", "马上回去"}} default button "马上回去" with icon note giving up after 30
            return button returned of result
        end tell
        '''
        
        try:
            result = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True, text=True, timeout=35
            )
            response = result.stdout.strip()
            
            if response == "马上回去":
                with self._lock:
                    self._state.mood = "happy"
                    self._state.message = "太棒了！加油 💪"
                    self._state.blink_alert = False
                logger.info("用户选择回归工作")
            else:
                logger.info("用户选择继续当前活动")
                # 5分钟后再提醒
                with self._lock:
                    self._state.show_message = False
                    self._state.blink_alert = False
                
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.error(f"显示介入对话框失败: {e}")
    
    def _show_intervention_dialog_windows(self, reason: str, message: str):
        """Windows: MessageBox介入提醒"""
        try:
            import ctypes
            result = ctypes.windll.user32.MessageBoxW(
                0,
                message,
                "🎯 注意力提醒",
                0x01 | 0x40  # MB_OKCANCEL | MB_ICONINFORMATION
            )
            if result == 1:  # IDOK
                with self._lock:
                    self._state.mood = "happy"
                    self._state.message = "太棒了！加油 💪"
                    self._state.blink_alert = False
        except Exception as e:
            logger.error(f"Windows对话框失败: {e}")
    
    def _show_intervention_dialog_linux(self, reason: str, message: str):
        """Linux: zenity/kdialog介入提醒"""
        try:
            subprocess.run(
                ['zenity', '--info', '--title=注意力提醒',
                 f'--text={message}', '--timeout=30'],
                capture_output=True, timeout=35
            )
        except FileNotFoundError:
            try:
                subprocess.run(
                    ['kdialog', '--msgbox', message, '--title', '注意力提醒'],
                    capture_output=True, timeout=35
                )
            except:
                pass
        except:
            pass
    
    def _show_break_overlay_native(self, duration_minutes: int):
        """
        启动全屏休息遮罩。
        
        核心思路：spawn 一个独立子进程 (break_overlay_process.py) 来显示 GUI。
        子进程拥有自己的主线程，因此 PyObjC / tkinter 能正常运行。
        父子通过 stdin/stdout 通信：
          父 → 子 stdin:  "skip\n"
          子 → 父 stdout: "started\n" / "ended\n" / "skipped\n"
        """
        import subprocess as sp
        from pathlib import Path
        
        script = Path(__file__).parent / "break_overlay_process.py"
        total_seconds = duration_minutes * 60
        
        try:
            self._overlay_proc = sp.Popen(
                [sys.executable, str(script), str(total_seconds)],
                stdin=sp.PIPE,
                stdout=sp.PIPE,
                stderr=sp.DEVNULL,
                text=True,
            )
        except Exception as e:
            logger.error(f"启动遮罩子进程失败: {e}")
            # 最后的回退：AppleScript 对话框
            self._show_break_overlay_applescript_fallback(duration_minutes)
            return
        
        logger.info(f"遮罩子进程已启动 (PID={self._overlay_proc.pid})")
        
        # 在子线程中读取子进程 stdout，等待结束信号
        def watch_proc():
            proc = self._overlay_proc
            try:
                for line in proc.stdout:
                    msg = line.strip()
                    logger.debug(f"遮罩子进程消息: {msg}")
                    if msg == "ended":
                        if self._state.is_break_mode:
                            self.end_break_overlay()
                        break
                    elif msg == "skipped":
                        if self._state.is_break_mode:
                            self.skip_break()
                        break
            except:
                pass
            finally:
                try:
                    proc.wait(timeout=3)
                except:
                    proc.kill()
                self._overlay_proc = None
        
        threading.Thread(target=watch_proc, daemon=True).start()
    
    def _send_overlay_command(self, cmd: str):
        """向遮罩子进程发送命令"""
        proc = getattr(self, '_overlay_proc', None)
        if proc and proc.poll() is None:
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
            except:
                pass
    
    def _show_break_overlay_applescript_fallback(self, duration_minutes: int):
        """最终回退：单次 AppleScript 对话框通知用户休息"""
        total_seconds = duration_minutes * 60
        start_time = time.time()
        
        tips = [
            "闭上眼睛，深呼吸三次",
            "站起来伸展一下身体",
            "看看远处，放松眼部肌肉",
            "去喝一杯水吧",
            "转转脖子，活动肩膀",
        ]
        
        while self._running and self._state.is_break_mode:
            elapsed = time.time() - start_time
            remaining = int(total_seconds - elapsed)
            
            if remaining <= 0:
                break
            
            mins = remaining // 60
            secs = remaining % 60
            tip = random.choice(tips)
            
            script = f'''
            tell application "System Events"
                activate
                display dialog "🌙 休息中...

⏱ 剩余时间: {mins:02d}:{secs:02d}

💡 {tip}" with title "休息时间" buttons {{"跳过休息", "继续休息"}} default button "继续休息" with icon note giving up after 20
                return button returned of result
            end tell
            '''
            
            try:
                result = subprocess.run(
                    ['osascript', '-e', script],
                    capture_output=True, text=True, timeout=25
                )
                if result.stdout.strip() == "跳过休息":
                    self.skip_break()
                    return
            except:
                pass
            
            time.sleep(15)
        
        if self._state.is_break_mode:
            self.end_break_overlay()
    
    # ---- 小精灵主循环 ----
    
    def _run_macos(self):
        """macOS: 小精灵主循环（使用系统状态栏图标方式）"""
        # macOS小精灵不需要自己的窗口循环
        # 状态通过WebSocket推送给前端，前端渲染小精灵
        # 这里只维护状态更新
        logger.info("macOS桌面悬浮窗已就绪（通过Web前端渲染）")
        while self._running:
            time.sleep(1)
    
    def _run_tkinter(self):
        """其他平台: tkinter小精灵主循环"""
        logger.info("桌面悬浮窗已就绪（通过Web前端渲染）")
        while self._running:
            time.sleep(1)


# ============================================================
# 单例管理
# ============================================================

_overlay: Optional[DesktopOverlay] = None


def get_desktop_overlay() -> DesktopOverlay:
    """获取桌面悬浮窗单例"""
    global _overlay
    if _overlay is None:
        _overlay = DesktopOverlay()
    return _overlay


def start_desktop_overlay() -> DesktopOverlay:
    """启动桌面悬浮窗"""
    overlay = get_desktop_overlay()
    overlay.start()
    return overlay


def stop_desktop_overlay():
    """停止桌面悬浮窗"""
    global _overlay
    if _overlay:
        _overlay.stop()


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("桌面悬浮窗测试")
    print("=" * 40)
    
    overlay = start_desktop_overlay()
    time.sleep(2)
    
    # 测试介入提醒
    print("\n测试介入提醒...")
    overlay.show_intervention("持续处于娱乐状态")
    time.sleep(5)
    
    # 测试休息遮罩
    print("\n测试休息遮罩 (0.5分钟)...")
    overlay.start_break_overlay(
        duration_minutes=1,
        on_end=lambda: print("休息结束！"),
        on_skip=lambda: print("跳过休息！")
    )
    
    # 等待
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_desktop_overlay()
        print("\n已退出")
