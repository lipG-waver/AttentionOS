"""
开机自启动管理器
支持 macOS（LaunchAgent）、Windows（Startup 快捷方式）、Linux（XDG autostart + systemd）
"""
import os
import sys
import platform
import subprocess
from pathlib import Path

from attention.config import Config


class AutoStartManager:
    def __init__(self):
        self.app_name = Config.AUTO_START["app_name"]
        self.app_path = self._get_app_path()
        self.system = platform.system()

    def _get_app_path(self) -> str:
        """智能获取当前程序路径（兼容开发模式和打包模式）"""
        if getattr(sys, "frozen", False):
            return sys.executable
        return os.path.abspath(sys.argv[0])

    def _get_exec_command(self) -> str:
        """获取完整的可执行命令（非打包模式自动补全 Python 解释器路径）"""
        if getattr(sys, "frozen", False):
            # 打包后的可执行文件，直接运行
            return self.app_path
        # 开发模式：必须用当前 Python 解释器运行脚本
        return f"{sys.executable} {self.app_path}"

    # ==================================================================
    # 公开接口
    # ==================================================================

    def enable(self) -> bool:
        """启用开机自启动"""
        try:
            if self.system == "Windows":
                success = self._enable_windows()
            elif self.system == "Linux":
                success = self._enable_linux()
            elif self.system == "Darwin":
                success = self._enable_macos()
            else:
                print(f"[自启动] 不支持的系统: {self.system}")
                return False

            if success:
                Config.AUTO_START["enabled"] = True
                print(f"[自启动] 设置成功: {self.app_name}")
            return success

        except PermissionError:
            print("[自启动] 权限不足，请尝试以管理员/root 权限运行")
            return False
        except Exception as e:
            print(f"[自启动] 设置失败: {e}")
            return False

    def disable(self) -> bool:
        """禁用开机自启动"""
        try:
            if self.system == "Windows":
                success = self._disable_windows()
            elif self.system == "Linux":
                success = self._disable_linux()
            elif self.system == "Darwin":
                success = self._disable_macos()
            else:
                print(f"[自启动] 不支持的系统: {self.system}")
                return False

            if success:
                Config.AUTO_START["enabled"] = False
                print("[自启动] 已成功禁用")
            return success

        except Exception as e:
            print(f"[自启动] 禁用失败: {e}")
            return False

    def is_enabled(self) -> bool:
        """检测系统级自启动项是否已配置"""
        try:
            if self.system == "Windows":
                return self._is_enabled_windows()
            elif self.system == "Linux":
                return self._is_enabled_linux()
            elif self.system == "Darwin":
                return self._is_enabled_macos()
            return False
        except Exception:
            return False

    # ==================================================================
    # macOS — LaunchAgent（用户级 plist）
    # ==================================================================

    @property
    def _macos_plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"com.{self.app_name}.plist"

    def _macos_plist_content(self) -> str:
        minimized = Config.AUTO_START.get("minimize", True)
        extra_arg = "<string>--minimized</string>" if minimized else ""

        # 非打包模式需要把 Python 解释器和脚本路径分别作为数组元素
        if getattr(sys, "frozen", False):
            program_args = f"<string>{self.app_path}</string>"
        else:
            program_args = (
                f"<string>{sys.executable}</string>\n"
                f"        <string>{self.app_path}</string>"
            )

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.{self.app_name}</string>
    <key>ProgramArguments</key>
    <array>
        {program_args}
        {extra_arg}
    </array>
    <key>WorkingDirectory</key>
    <string>{Config.BASE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{Config.BASE_DIR}/data/launch_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{Config.BASE_DIR}/data/launch_stderr.log</string>
</dict>
</plist>
"""

    def _enable_macos(self) -> bool:
        plist_file = self._macos_plist_path
        plist_file.parent.mkdir(parents=True, exist_ok=True)
        plist_file.write_text(self._macos_plist_content(), encoding="utf-8")

        # 先尝试 unload 避免重复加载错误，再 load
        self._macos_launchctl("unload", plist_file, check=False)
        ok = self._macos_launchctl("load", plist_file, check=True)
        if ok:
            print(f"[macOS] LaunchAgent 已创建并加载: {plist_file}")
        else:
            # 即使 load 失败，plist 文件已写入，下次登录时系统会自动加载
            print(f"[macOS] LaunchAgent plist 已写入 (load 可能需要重新登录生效): {plist_file}")
        return True  # plist 写入即视为成功

    def _disable_macos(self) -> bool:
        plist_file = self._macos_plist_path
        if plist_file.exists():
            self._macos_launchctl("unload", plist_file, check=False)
            plist_file.unlink()
            print(f"[macOS] LaunchAgent 已卸载并删除: {plist_file}")
        else:
            print(f"[macOS] plist 文件不存在，无需删除")
        return True

    def _is_enabled_macos(self) -> bool:
        return self._macos_plist_path.exists()

    @staticmethod
    def _macos_launchctl(cmd: str, plist_file: Path, check: bool = True) -> bool:
        """执行 launchctl load/unload，同时兼容旧版和新版 launchctl。"""
        try:
            # 优先尝试 bootstrap（macOS 10.10+）
            uid = os.getuid()
            result = subprocess.run(
                ["launchctl", "bootstrap" if cmd == "load" else "bootout",
                 f"gui/{uid}", str(plist_file)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

        # 兜底：旧版 launchctl load/unload
        try:
            result = subprocess.run(
                ["launchctl", cmd, str(plist_file)],
                capture_output=True, text=True
            )
            return result.returncode == 0
        except Exception as e:
            if check:
                print(f"[macOS] launchctl {cmd} 失败: {e}")
            return False

    # ==================================================================
    # Windows — Startup 文件夹快捷方式
    # ==================================================================

    def _enable_windows(self) -> bool:
        try:
            import win32com.client

            startup_dir = (
                Path(os.getenv("APPDATA"))
                / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            )
            startup_dir.mkdir(parents=True, exist_ok=True)

            shortcut_path = startup_dir / f"{self.app_name}.lnk"
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(str(shortcut_path))

            if getattr(sys, "frozen", False):
                shortcut.TargetPath = str(self.app_path)
            else:
                # 开发模式：目标是 Python 解释器，脚本路径作为参数
                shortcut.TargetPath = sys.executable
                shortcut.Arguments = f'"{self.app_path}"'

            shortcut.WorkingDirectory = str(Config.BASE_DIR)
            shortcut.Description = "个人注意力管理助手"
            shortcut.save()

            print(f"[Windows] 快捷方式已创建: {shortcut_path}")
            return True

        except ImportError:
            print("[Windows] 未安装 pywin32，请运行: pip install pywin32")
            return False
        except Exception as e:
            print(f"[Windows] 创建快捷方式失败: {e}")
            return False

    def _disable_windows(self) -> bool:
        startup_dir = (
            Path(os.getenv("APPDATA"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )
        removed = False
        for ext in (".lnk", ".vbs", ".bat", ".cmd"):
            f = startup_dir / f"{self.app_name}{ext}"
            if f.exists():
                f.unlink()
                removed = True
                print(f"[Windows] 已删除: {f.name}")

        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
            try:
                winreg.DeleteValue(key, self.app_name)
                removed = True
                print(f"[Windows] 已删除注册表项: {self.app_name}")
            except FileNotFoundError:
                pass
            finally:
                winreg.CloseKey(key)
        except Exception:
            pass

        return True  # 没找到也算成功

    def _is_enabled_windows(self) -> bool:
        startup_dir = (
            Path(os.getenv("APPDATA", ""))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        )
        return (startup_dir / f"{self.app_name}.lnk").exists()

    # ==================================================================
    # Linux — XDG autostart（主）+ systemd 用户服务（副）
    # ==================================================================

    # --- XDG autostart（.desktop 文件，兼容所有桌面环境） ---

    @property
    def _xdg_autostart_path(self) -> Path:
        return Path.home() / ".config" / "autostart" / f"{self.app_name}.desktop"

    def _xdg_desktop_content(self) -> str:
        exec_cmd = self._get_exec_command()
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={self.app_name}\n"
            "Comment=个人注意力管理助手\n"
            f"Exec={exec_cmd}\n"
            f"WorkingDirectory={Config.BASE_DIR}\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )

    def _enable_linux_xdg(self) -> bool:
        desktop_file = self._xdg_autostart_path
        desktop_file.parent.mkdir(parents=True, exist_ok=True)
        desktop_file.write_text(self._xdg_desktop_content(), encoding="utf-8")
        print(f"[Linux] XDG 自启动文件已创建: {desktop_file}")
        return True

    def _is_enabled_linux_xdg(self) -> bool:
        return self._xdg_autostart_path.exists()

    # --- systemd 用户服务（适合无桌面 / 服务器环境） ---

    @property
    def _systemd_service_path(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user" / f"{self.app_name}.service"

    def _systemd_service_content(self) -> str:
        exec_cmd = self._get_exec_command()
        return (
            "[Unit]\n"
            f"Description={self.app_name} - 个人注意力管理助手\n"
            "After=graphical-session.target\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={exec_cmd}\n"
            f"WorkingDirectory={Config.BASE_DIR}\n"
            "Restart=on-failure\n"
            "RestartSec=10\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )

    def _enable_linux_systemd(self) -> bool:
        service_file = self._systemd_service_path
        service_file.parent.mkdir(parents=True, exist_ok=True)
        service_file.write_text(self._systemd_service_content(), encoding="utf-8")
        try:
            subprocess.run(
                ["systemctl", "--user", "enable", f"{self.app_name}.service"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # systemctl 不存在或超时，不影响 XDG 路径
        print(f"[Linux] systemd 服务已创建: {service_file}")
        return True

    def _is_enabled_linux_systemd(self) -> bool:
        return self._systemd_service_path.exists()

    # --- 统一入口 ---

    def _enable_linux(self) -> bool:
        # 主路径：XDG autostart，兼容所有桌面环境（GNOME/KDE/XFCE/…）
        xdg_ok = self._enable_linux_xdg()

        # 副路径：systemd 用户服务（适合无桌面 / 服务器场景），失败不影响主路径
        try:
            self._enable_linux_systemd()
        except Exception as e:
            print(f"[Linux] systemd 服务创建失败（不影响 XDG 自启动）: {e}")

        return xdg_ok

    def _disable_linux(self) -> bool:
        # 删除 XDG autostart 文件
        desktop_file = self._xdg_autostart_path
        if desktop_file.exists():
            desktop_file.unlink()
            print(f"[Linux] XDG 自启动文件已删除: {desktop_file}")

        # 停用并删除 systemd 服务
        service_name = f"{self.app_name}.service"
        try:
            subprocess.run(
                ["systemctl", "--user", "stop", service_name],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["systemctl", "--user", "disable", service_name],
                capture_output=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        service_file = self._systemd_service_path
        if service_file.exists():
            service_file.unlink()

        try:
            subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return True

    def _is_enabled_linux(self) -> bool:
        # 任一方式存在即视为已启用
        return self._is_enabled_linux_xdg() or self._is_enabled_linux_systemd()


# ------------------------------------------------------------------
# 便捷函数
# ------------------------------------------------------------------

def setup_auto_start():
    """根据配置自动设置自启动"""
    if Config.AUTO_START["enabled"]:
        manager = AutoStartManager()
        return manager.enable()
    return False
