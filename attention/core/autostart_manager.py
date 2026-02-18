"""
开机自启动管理器
与config.py配置深度集成
"""
import os
import sys
import platform
from pathlib import Path
from attention.config import Config  # 导入您的配置类


class AutoStartManager:
    def __init__(self):
        """从Config类读取配置"""
        self.app_name = Config.AUTO_START["app_name"]
        # 获取当前程序路径
        self.app_path = self._get_app_path()
        self.system = platform.system()

    def _get_app_path(self):
        """智能获取当前程序路径（兼容开发模式和打包模式）"""
        if getattr(sys, 'frozen', False):
            # 打包后的exe文件
            return sys.executable
        else:
            # 开发模式下的Python脚本
            return os.path.abspath(sys.argv[0])

    def enable(self) -> bool:
        """启用开机自启动"""
        if Config.AUTO_START["enabled"]:
            print(f"[自启动] 配置中已启用，正在设置...")

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
                # 更新配置状态（如果需要持久化）
                Config.AUTO_START["enabled"] = True
                print(f"[自启动] 设置成功: {self.app_name}")
            return success

        except PermissionError:
            print("[自启动] 权限不足，请尝试使用管理员/root权限运行")
            return False
        except Exception as e:
            print(f"[自启动] 设置失败: {e}")
            return False

    def _enable_windows(self) -> bool:
        """Windows: 创建启动文件夹快捷方式"""
        try:
            import win32com.client  # 确保已安装 pywin32

            # 1. 获取启动文件夹路径
            startup_dir = Path(os.getenv('APPDATA')) / \
                          'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'
            startup_dir.mkdir(parents=True, exist_ok=True)

            # 2. 设置快捷方式路径
            shortcut_path = startup_dir / f"{self.app_name}.lnk"

            # 3. 构建启动参数（可选）
            arguments = ""
            if Config.AUTO_START.get("run_minimized", False):
                arguments = "--minimized"  # 假设您的程序支持此参数

            # 4. 创建快捷方式
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortcut(str(shortcut_path))

            # 🔴 关键：确保使用绝对路径
            shortcut.TargetPath = str(self.app_path)
            shortcut.Arguments = ""
            shortcut.WorkingDirectory = str(Config.BASE_DIR)  # 设置工作目录，这对您的项目很重要
            shortcut.Description = "个人注意力管理助手"
            # shortcut.IconLocation = str(icon_path) # 可选：设置图标

            shortcut.save()
            print(f"[Windows] ✅ 快捷方式已创建: {shortcut_path}")
            print(f"        目标: {self.app_path} {arguments}")
            return True

        except ImportError:
            print("[Windows] ❌ 未安装pywin32，请运行: pip install pywin32")
            return False
        except Exception as e:
            print(f"[Windows] ❌ 创建快捷方式失败: {e}")
            return False

    def _enable_linux(self) -> bool:
        """Linux: 创建systemd用户服务"""
        service_content = f"""[Unit]
Description={self.app_name} - 个人注意力管理助手
After=graphical-session.target

[Service]
Type=simple
ExecStart={self.app_path} {'--minimized' if Config.AUTO_START.get('run_minimized') else ''}
WorkingDirectory={Config.BASE_DIR}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""

        # 用户级systemd服务目录
        service_dir = Path.home() / '.config' / 'systemd' / 'user'
        service_dir.mkdir(parents=True, exist_ok=True)

        service_file = service_dir / f"{self.app_name}.service"
        service_file.write_text(service_content)

        # 启用服务
        os.system(f'systemctl --user enable {self.app_name}.service')
        os.system(f'systemctl --user start {self.app_name}.service')

        print(f"[Linux] systemd服务已创建: {service_file}")
        return True

    def _enable_macos(self) -> bool:
        """macOS: LaunchAgent"""
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.{self.app_name}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{self.app_path}</string>
        {'<string>--minimized</string>' if Config.AUTO_START.get('run_minimized') else ''}
    </array>
    <key>WorkingDirectory</key>
    <string>{Config.BASE_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""

        launch_agents_dir = Path.home() / 'Library' / 'LaunchAgents'
        launch_agents_dir.mkdir(parents=True, exist_ok=True)

        plist_file = launch_agents_dir / f"com.{self.app_name}.plist"
        plist_file.write_text(plist_content)

        os.system(f'launchctl load {plist_file}')
        print(f"[macOS] LaunchAgent已创建: {plist_file}")
        return True

    def disable(self) -> bool:
        """禁用开机自启动 - 删除所有创建的自启动项"""
        try:
            success = False

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
                # 更新配置状态
                Config.AUTO_START["enabled"] = False
                print(f"[自启动] ✅ 已成功禁用")
            else:
                print(f"[自启动] ⚠️  禁用操作可能未完全成功")

            return success

        except Exception as e:
            print(f"[自启动] ❌ 禁用失败: {e}")
            return False

    def _disable_windows(self) -> bool:
        """Windows: 删除所有自启动项"""
        items_removed = []

        try:
            # 1. 删除启动文件夹中的快捷方式
            startup_dir = Path(os.getenv('APPDATA')) / \
                          'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'

            # 可能存在的文件扩展名
            possible_files = [
                startup_dir / f"{self.app_name}.lnk",  # 快捷方式
                startup_dir / f"{self.app_name}.vbs",  # VBS脚本
                startup_dir / f"{self.app_name}.bat",  # 批处理文件
                startup_dir / f"{self.app_name}.cmd",  # CMD文件
            ]

            for file_path in possible_files:
                if file_path.exists():
                    try:
                        file_path.unlink()
                        items_removed.append(str(file_path.name))
                        print(f"[Windows] 已删除: {file_path.name}")
                    except Exception as e:
                        print(f"[Windows] 删除 {file_path.name} 失败: {e}")

            # 2. 删除注册表项（如果存在）
            try:
                import winreg
                key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path,
                                         0, winreg.KEY_ALL_ACCESS)

                    # 尝试读取并删除
                    try:
                        winreg.DeleteValue(key, self.app_name)
                        items_removed.append(f"注册表项: {self.app_name}")
                        print(f"[Windows] 已删除注册表项: {self.app_name}")
                    except FileNotFoundError:
                        pass  # 注册表项不存在，正常
                    finally:
                        winreg.CloseKey(key)

                except PermissionError:
                    print("[Windows] 无权限访问注册表，需要管理员权限")
                except Exception as e:
                    print(f"[Windows] 注册表操作异常: {e}")

            except ImportError:
                print("[Windows] 无法导入winreg模块")

            # 3. 如果删除了任何项目，返回成功
            if items_removed:
                print(f"[Windows] ✅ 共删除 {len(items_removed)} 个自启动项")
                return True
            else:
                print("[Windows] ⚠️  未找到需要删除的自启动项")
                return True  # 没有找到项目也算禁用成功

        except Exception as e:
            print(f"[Windows] ❌ 禁用过程中出错: {e}")
            return False

    def _disable_linux(self) -> bool:
        """Linux: 禁用并删除systemd用户服务"""
        try:
            # 1. 停止并禁用服务
            service_name = f"{self.app_name}.service"

            # 停止服务
            stop_result = os.system(f'systemctl --user stop {service_name} 2>/dev/null')
            if stop_result == 0:
                print(f"[Linux] 已停止服务: {service_name}")

            # 禁用服务
            disable_result = os.system(f'systemctl --user disable {service_name} 2>/dev/null')
            if disable_result == 0:
                print(f"[Linux] 已禁用服务: {service_name}")

            # 2. 删除服务文件
            service_dir = Path.home() / '.config' / 'systemd' / 'user'
            service_file = service_dir / service_name

            if service_file.exists():
                service_file.unlink()
                print(f"[Linux] 已删除服务文件: {service_file}")

            # 3. 重载systemd
            os.system('systemctl --user daemon-reload 2>/dev/null')

            # 4. 检查是否还有其他自启动方式（如.desktop文件）
            autostart_dir = Path.home() / '.config' / 'autostart'
            desktop_file = autostart_dir / f"{self.app_name}.desktop"

            if desktop_file.exists():
                desktop_file.unlink()
                print(f"[Linux] 已删除桌面自启动文件: {desktop_file}")

            print(f"[Linux] ✅ 自启动已禁用")
            return True

        except Exception as e:
            print(f"[Linux] ❌ 禁用失败: {e}")
            return False

    def _disable_macos(self) -> bool:
        """macOS: 卸载并删除LaunchAgent"""
        try:
            # 1. 构建plist文件路径
            plist_file = Path.home() / 'Library' / 'LaunchAgents' / f"com.{self.app_name}.plist"

            # 2. 卸载LaunchAgent
            if plist_file.exists():
                # 先停止并卸载
                unload_result = os.system(f'launchctl unload {plist_file} 2>/dev/null')
                if unload_result == 0:
                    print(f"[macOS] 已卸载LaunchAgent")

                # 删除plist文件
                plist_file.unlink()
                print(f"[macOS] 已删除plist文件: {plist_file}")
            else:
                print(f"[macOS] plist文件不存在: {plist_file}")

            # 3. 检查系统级LaunchAgents（如果有权限）
            system_plist = Path('/Library/LaunchAgents') / f"com.{self.app_name}.plist"
            if system_plist.exists():
                try:
                    os.system(f'sudo launchctl unload {system_plist} 2>/dev/null')
                    os.system(f'sudo rm {system_plist} 2>/dev/null')
                    print(f"[macOS] 已删除系统级LaunchAgent")
                except:
                    print(f"[macOS] 需要管理员权限删除系统级LaunchAgent")

            print(f"[macOS] ✅ 自启动已禁用")
            return True

        except Exception as e:
            print(f"[macOS] ❌ 禁用失败: {e}")
            return False


# 便捷函数
def setup_auto_start():
    """根据配置自动设置自启动"""
    if Config.AUTO_START["enabled"]:
        manager = AutoStartManager()
        return manager.enable()
    return False