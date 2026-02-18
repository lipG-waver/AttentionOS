"""
本地活动信号采集模块
检测键盘/鼠标活动、焦点窗口，提供轻量级用户活动状态判断
"""
import logging
import platform
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from collections import deque

logger = logging.getLogger(__name__)


@dataclass
class ActivitySnapshot:
    """单次活动快照"""
    timestamp: datetime
    keyboard_active: bool = False  # 检测期间是否有键盘输入
    mouse_active: bool = False     # 检测期间是否有鼠标移动/点击
    mouse_position: tuple = (0, 0) # 当前鼠标位置
    active_window_title: str = ""  # 当前焦点窗口标题
    active_window_app: str = ""    # 当前焦点应用名称
    active_window_pid: int = 0     # 当前焦点应用PID
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "keyboard_active": self.keyboard_active,
            "mouse_active": self.mouse_active,
            "mouse_position": self.mouse_position,
            "active_window_title": self.active_window_title,
            "active_window_app": self.active_window_app,
            "active_window_pid": self.active_window_pid
        }


@dataclass 
class ActivityState:
    """聚合的活动状态（基于一段时间内的快照）"""
    period_start: datetime
    period_end: datetime
    
    # 活动统计
    keyboard_events: int = 0       # 检测到键盘活动的快照数
    mouse_events: int = 0          # 检测到鼠标活动的快照数
    total_snapshots: int = 0       # 总快照数
    
    # 窗口信息
    primary_window_title: str = "" # 主要使用的窗口标题
    primary_window_app: str = ""   # 主要使用的应用
    window_switches: int = 0       # 窗口切换次数
    
    # 计算属性
    @property
    def activity_ratio(self) -> float:
        """活动比例：有输入的快照占总快照的比例"""
        if self.total_snapshots == 0:
            return 0.0
        active = max(self.keyboard_events, self.mouse_events)
        return active / self.total_snapshots
    
    @property
    def is_active(self) -> bool:
        """是否处于活跃状态（活动比例>50%）"""
        return self.activity_ratio > 0.5
    
    @property
    def is_idle(self) -> bool:
        """是否处于空闲状态（活动比例<10%）"""
        return self.activity_ratio < 0.1
    
    @property
    def engagement_level(self) -> str:
        """参与度级别"""
        ratio = self.activity_ratio
        if ratio >= 0.7:
            return "高度活跃"
        elif ratio >= 0.4:
            return "中度活跃"
        elif ratio >= 0.1:
            return "低度活跃"
        else:
            return "空闲"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_start": self.period_start.strftime("%Y-%m-%d %H:%M:%S"),
            "period_end": self.period_end.strftime("%Y-%m-%d %H:%M:%S"),
            "keyboard_events": self.keyboard_events,
            "mouse_events": self.mouse_events,
            "total_snapshots": self.total_snapshots,
            "activity_ratio": round(self.activity_ratio, 2),
            "engagement_level": self.engagement_level,
            "primary_window_title": self.primary_window_title,
            "primary_window_app": self.primary_window_app,
            "window_switches": self.window_switches,
            "is_active": self.is_active,
            "is_idle": self.is_idle
        }


class ActivityMonitor:
    """
    活动监控器
    在后台持续采集用户活动信号，提供聚合的活动状态
    """
    
    def __init__(self, sample_interval: float = 1.0, history_size: int = 120):
        """
        Args:
            sample_interval: 采样间隔（秒），默认1秒
            history_size: 保留的历史快照数量，默认120（2分钟）
        """
        self.sample_interval = sample_interval
        self.history_size = history_size
        self.system = platform.system()
        
        # 状态
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # 历史快照（环形缓冲）
        self._snapshots: deque = deque(maxlen=history_size)
        
        # 上一次的状态（用于检测变化）
        self._last_mouse_pos = (0, 0)
        self._last_window_title = ""
        
        # 键盘/鼠标事件计数（在采样间隔内）
        self._keyboard_count = 0
        self._mouse_click_count = 0
        self._mouse_move_count = 0
        
        # 输入监听器
        self._input_listener = None
        
        # 初始化平台特定组件
        self._init_platform()
    
    def _init_platform(self):
        """初始化平台特定组件"""
        if self.system == "Darwin":
            self._init_macos()
        elif self.system == "Windows":
            self._init_windows()
        elif self.system == "Linux":
            self._init_linux()
        else:
            logger.warning(f"未支持的平台: {self.system}")
    
    def _init_macos(self):
        """macOS初始化"""
        try:
            # 检查必要的库
            import Quartz
            import AppKit
            logger.info("macOS: Quartz/AppKit 可用")
        except ImportError:
            logger.warning("macOS: 需要安装 pyobjc-framework-Quartz 和 pyobjc-framework-Cocoa")
            logger.warning("运行: pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa")
    
    def _init_windows(self):
        """Windows初始化"""
        try:
            import win32gui
            import win32process
            logger.info("Windows: win32gui 可用")
        except ImportError:
            logger.warning("Windows: 需要安装 pywin32")
            logger.warning("运行: pip install pywin32")
    
    def _init_linux(self):
        """Linux初始化"""
        try:
            import Xlib
            from Xlib import display
            logger.info("Linux: Xlib 可用")
        except ImportError:
            logger.warning("Linux: 需要安装 python-xlib")
            logger.warning("运行: pip install python-xlib")
    
    def start(self):
        """启动后台监控"""
        if self._running:
            return
        
        self._running = True
        self._start_input_listener()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info(f"活动监控已启动 (采样间隔: {self.sample_interval}s)")
    
    def stop(self):
        """停止后台监控"""
        self._running = False
        self._stop_input_listener()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("活动监控已停止")
    
    def _start_input_listener(self):
        """启动输入事件监听"""
        try:
            from pynput import keyboard, mouse
            
            def on_key_press(key):
                self._keyboard_count += 1
            
            def on_mouse_click(x, y, button, pressed):
                if pressed:
                    self._mouse_click_count += 1
            
            def on_mouse_move(x, y):
                self._mouse_move_count += 1
            
            # 键盘监听器
            self._keyboard_listener = keyboard.Listener(on_press=on_key_press)
            self._keyboard_listener.start()
            
            # 鼠标监听器  
            self._mouse_listener = mouse.Listener(
                on_click=on_mouse_click,
                on_move=on_mouse_move
            )
            self._mouse_listener.start()
            
            logger.info("输入监听器已启动 (pynput)")
            
        except ImportError:
            logger.warning("pynput 未安装，将使用轮询方式检测鼠标位置")
            logger.warning("运行: pip install pynput")
        except KeyError as e:
            # macOS 上 pynput 与某些 PyObjC 版本不兼容
            # 常见: KeyError: 'AXIsProcessTrusted'
            logger.warning(f"pynput 兼容性问题 (KeyError: {e})，回退到轮询模式")
            logger.warning("提示: 尝试 pip install --upgrade pynput pyobjc-framework-Quartz")
        except Exception as e:
            logger.warning(f"启动输入监听器失败: {e}")
    
    def _stop_input_listener(self):
        """停止输入事件监听"""
        try:
            if hasattr(self, '_keyboard_listener'):
                self._keyboard_listener.stop()
            if hasattr(self, '_mouse_listener'):
                self._mouse_listener.stop()
        except Exception as e:
            logger.warning(f"停止输入监听器失败: {e}")
    
    def _monitor_loop(self):
        """后台监控循环"""
        while self._running:
            try:
                snapshot = self._capture_snapshot()
                with self._lock:
                    self._snapshots.append(snapshot)
            except Exception as e:
                logger.error(f"采集快照失败: {e}")
            
            time.sleep(self.sample_interval)
    
    def _capture_snapshot(self) -> ActivitySnapshot:
        """采集单次快照"""
        now = datetime.now()
        
        # 获取焦点窗口信息
        window_title, window_app, window_pid = self._get_active_window()
        
        # 获取鼠标位置
        mouse_pos = self._get_mouse_position()
        
        # 检测活动
        keyboard_active = self._keyboard_count > 0
        mouse_active = (
            self._mouse_click_count > 0 or 
            self._mouse_move_count > 5 or  # 忽略微小移动
            mouse_pos != self._last_mouse_pos
        )
        
        # 重置计数器
        self._keyboard_count = 0
        self._mouse_click_count = 0
        self._mouse_move_count = 0
        self._last_mouse_pos = mouse_pos
        
        return ActivitySnapshot(
            timestamp=now,
            keyboard_active=keyboard_active,
            mouse_active=mouse_active,
            mouse_position=mouse_pos,
            active_window_title=window_title,
            active_window_app=window_app,
            active_window_pid=window_pid
        )
    
    def _get_active_window(self) -> tuple:
        """获取当前焦点窗口信息"""
        if self.system == "Darwin":
            return self._get_active_window_macos()
        elif self.system == "Windows":
            return self._get_active_window_windows()
        elif self.system == "Linux":
            return self._get_active_window_linux()
        return ("", "", 0)
    
    def _get_active_window_macos(self) -> tuple:
        """macOS: 获取焦点窗口
        
        注意: NSWorkspace.frontmostApplication() 在子线程中不会自动更新，
        因为 AppKit 依赖主线程 RunLoop 来分发事件通知。
        这里使用两种可靠的替代方案：
        1. 优先使用 Quartz CGWindowListCopyWindowInfo（C层API，不依赖RunLoop）
        2. 备选使用 AppleScript（通过系统调用，始终准确）
        """
        try:
            return self._get_active_window_macos_quartz()
        except Exception as e:
            logger.debug(f"macOS Quartz方式失败: {e}, 回退到AppleScript")
            try:
                return self._get_active_window_macos_applescript()
            except Exception as e2:
                logger.debug(f"macOS AppleScript方式也失败: {e2}")
                return ("", "", 0)
    
    def _get_active_window_macos_quartz(self) -> tuple:
        """macOS: 使用Quartz C层API获取焦点窗口（线程安全）"""
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowListExcludeDesktopElements,
            kCGNullWindowID
        )
        import subprocess
        
        # 用 AppleScript 快速获取前台应用名和PID（最可靠的方式）
        # 这比完整的 AppleScript 方案轻量，只取两个值
        script = '''
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set appID to unix id of frontApp
            return appName & "|" & appID
        end tell
        '''
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=2
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"osascript failed: {result.stderr}")
        
        parts = result.stdout.strip().split('|')
        if len(parts) != 2:
            raise RuntimeError(f"Unexpected osascript output: {result.stdout}")
        
        app_name = parts[0].strip()
        try:
            app_pid = int(parts[1].strip())
        except ValueError:
            app_pid = 0
        
        # 用 Quartz 获取该PID对应的窗口标题
        window_title = ""
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        window_list = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
        
        if window_list:
            for window in window_list:
                if window.get('kCGWindowOwnerPID') == app_pid:
                    title = window.get('kCGWindowName', '')
                    if title:
                        window_title = title
                        break
        
        return (window_title, app_name, app_pid)
    
    def _get_active_window_macos_applescript(self) -> tuple:
        """macOS: 纯AppleScript备选方案"""
        import subprocess
        
        script = '''
        tell application "System Events"
            set frontApp to first application process whose frontmost is true
            set appName to name of frontApp
            set appID to unix id of frontApp
            set winTitle to ""
            try
                set winTitle to name of front window of frontApp
            end try
            return appName & "|" & appID & "|" & winTitle
        end tell
        '''
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=3
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"osascript failed: {result.stderr}")
        
        parts = result.stdout.strip().split('|', 2)
        app_name = parts[0].strip() if len(parts) > 0 else ""
        try:
            app_pid = int(parts[1].strip()) if len(parts) > 1 else 0
        except ValueError:
            app_pid = 0
        window_title = parts[2].strip() if len(parts) > 2 else ""
        
        return (window_title, app_name, app_pid)
    
    def _get_active_window_windows(self) -> tuple:
        """Windows: 获取焦点窗口"""
        try:
            import win32gui
            import win32process
            import psutil
            
            hwnd = win32gui.GetForegroundWindow()
            window_title = win32gui.GetWindowText(hwnd)
            
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            
            try:
                process = psutil.Process(pid)
                app_name = process.name()
            except:
                app_name = ""
            
            return (window_title, app_name, pid)
            
        except Exception as e:
            logger.debug(f"Windows获取窗口失败: {e}")
            return ("", "", 0)
    
    def _get_active_window_linux(self) -> tuple:
        """Linux: 获取焦点窗口"""
        try:
            from Xlib import display, X
            from Xlib.protocol import rq
            
            d = display.Display()
            root = d.screen().root
            
            # 获取活跃窗口
            NET_ACTIVE_WINDOW = d.intern_atom('_NET_ACTIVE_WINDOW')
            active_window_id = root.get_full_property(NET_ACTIVE_WINDOW, X.AnyPropertyType)
            
            if not active_window_id:
                return ("", "", 0)
            
            window_id = active_window_id.value[0]
            window = d.create_resource_object('window', window_id)
            
            # 获取窗口标题
            window_title = ""
            try:
                NET_WM_NAME = d.intern_atom('_NET_WM_NAME')
                name = window.get_full_property(NET_WM_NAME, 0)
                if name:
                    window_title = name.value.decode('utf-8', errors='ignore')
            except:
                pass
            
            if not window_title:
                try:
                    window_title = window.get_wm_name() or ""
                except:
                    pass
            
            # 获取PID
            pid = 0
            try:
                NET_WM_PID = d.intern_atom('_NET_WM_PID')
                pid_prop = window.get_full_property(NET_WM_PID, X.AnyPropertyType)
                if pid_prop:
                    pid = pid_prop.value[0]
            except:
                pass
            
            # 获取应用名称
            app_name = ""
            if pid:
                try:
                    import psutil
                    process = psutil.Process(pid)
                    app_name = process.name()
                except:
                    pass
            
            return (window_title, app_name, pid)
            
        except Exception as e:
            logger.debug(f"Linux获取窗口失败: {e}")
            return ("", "", 0)
    
    def _get_mouse_position(self) -> tuple:
        """获取鼠标位置"""
        try:
            from pynput.mouse import Controller
            mouse = Controller()
            return mouse.position
        except:
            pass
        
        # 备用方案
        if self.system == "Darwin":
            try:
                from Quartz import CGEventGetLocation, CGEventCreate
                event = CGEventCreate(None)
                pos = CGEventGetLocation(event)
                return (int(pos.x), int(pos.y))
            except:
                pass
        
        return self._last_mouse_pos
    
    def get_current_state(self, seconds: int = 60) -> ActivityState:
        """
        获取指定时间段内的聚合活动状态
        
        Args:
            seconds: 回溯的秒数，默认60秒
            
        Returns:
            聚合的活动状态
        """
        cutoff = datetime.now() - timedelta(seconds=seconds)
        
        with self._lock:
            # 筛选时间范围内的快照
            recent = [s for s in self._snapshots if s.timestamp >= cutoff]
        
        if not recent:
            return ActivityState(
                period_start=cutoff,
                period_end=datetime.now()
            )
        
        # 统计
        keyboard_events = sum(1 for s in recent if s.keyboard_active)
        mouse_events = sum(1 for s in recent if s.mouse_active)
        
        # 窗口切换统计
        window_switches = 0
        window_counts: Dict[str, int] = {}
        last_window = None
        
        for s in recent:
            window_key = f"{s.active_window_app}:{s.active_window_title}"
            window_counts[window_key] = window_counts.get(window_key, 0) + 1
            
            if last_window and window_key != last_window:
                window_switches += 1
            last_window = window_key
        
        # 找出主要窗口
        primary_window = max(window_counts.items(), key=lambda x: x[1])[0] if window_counts else ":"
        primary_app, primary_title = primary_window.split(":", 1)
        
        return ActivityState(
            period_start=recent[0].timestamp,
            period_end=recent[-1].timestamp,
            keyboard_events=keyboard_events,
            mouse_events=mouse_events,
            total_snapshots=len(recent),
            primary_window_title=primary_title,
            primary_window_app=primary_app,
            window_switches=window_switches
        )
    
    def get_latest_snapshot(self) -> Optional[ActivitySnapshot]:
        """获取最新的快照"""
        with self._lock:
            if self._snapshots:
                return self._snapshots[-1]
        return None
    
    def get_idle_duration(self) -> int:
        """
        获取连续空闲时长（秒）
        
        Returns:
            连续空闲的秒数
        """
        with self._lock:
            snapshots = list(self._snapshots)
        
        if not snapshots:
            return 0
        
        idle_seconds = 0
        for s in reversed(snapshots):
            if s.keyboard_active or s.mouse_active:
                break
            idle_seconds += self.sample_interval
        
        return int(idle_seconds)


# 单例
_monitor: Optional[ActivityMonitor] = None


def get_activity_monitor() -> ActivityMonitor:
    """获取活动监控器单例"""
    global _monitor
    if _monitor is None:
        _monitor = ActivityMonitor()
    return _monitor


def start_activity_monitoring():
    """启动活动监控"""
    monitor = get_activity_monitor()
    monitor.start()
    return monitor


def stop_activity_monitoring():
    """停止活动监控"""
    global _monitor
    if _monitor:
        _monitor.stop()


def get_current_activity(seconds: int = 60) -> ActivityState:
    """获取当前活动状态的便捷函数"""
    return get_activity_monitor().get_current_state(seconds)


# 测试代码
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("启动活动监控测试...")
    print("按 Ctrl+C 停止\n")
    
    monitor = start_activity_monitoring()
    
    try:
        while True:
            time.sleep(5)
            
            # 获取最新快照
            snapshot = monitor.get_latest_snapshot()
            if snapshot:
                print(f"\n[{snapshot.timestamp.strftime('%H:%M:%S')}] 最新快照:")
                print(f"  键盘活动: {snapshot.keyboard_active}")
                print(f"  鼠标活动: {snapshot.mouse_active}")
                print(f"  焦点窗口: {snapshot.active_window_app} - {snapshot.active_window_title[:50]}")
            
            # 获取聚合状态
            state = monitor.get_current_state(30)
            print(f"\n  [过去30秒统计]")
            print(f"  活动比例: {state.activity_ratio:.0%}")
            print(f"  参与度: {state.engagement_level}")
            print(f"  窗口切换: {state.window_switches}次")
            print(f"  空闲时长: {monitor.get_idle_duration()}秒")
            
    except KeyboardInterrupt:
        print("\n停止监控...")
        stop_activity_monitoring()
