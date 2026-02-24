#!/usr/bin/env python3
"""
注意力管理Agent - 统一启动入口
"""
import argparse
import sys
import traceback


def main():
    # 启动时立即加载持久化的 API 配置（data/api_settings.json → MultiLLMClient）
    # 必须在所有模式分支之前执行，确保第一个分析周期就能读到 key
    from attention.core.api_settings import get_api_settings
    get_api_settings()

    parser = argparse.ArgumentParser(
        description="注意力管理Agent v2.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
启动模式:
  python run.py              # 完整模式（托盘图标 + Web仪表盘 + 监控）
  python run.py --no-tray    # 无托盘模式（适合调试）
  python run.py --web-only   # 仅启动Web服务（查看历史数据）
  python run.py --cli        # 仅命令行监控（无Web）
        """,
    )

    parser.add_argument("--no-tray", action="store_true", help="不使用托盘图标（调试模式）")
    parser.add_argument("--web-only", action="store_true", help="仅启动Web服务，不启动监控")
    parser.add_argument("--cli", action="store_true", help="仅命令行模式，不启动Web")
    parser.add_argument("--port", "-p", type=int, default=5000, help="Web服务端口（默认5000）")

    args = parser.parse_args()

    if args.web_only:
        # 仅Web服务
        print("启动Web服务模式...")
        from attention.ui.web_server import run_server
        from attention.utils import setup_logging
        import logging
        import webbrowser
        import time
        import threading

        setup_logging(logging.INFO)

        thread = threading.Thread(
            target=run_server,
            kwargs={"host": "127.0.0.1", "port": args.port},
            daemon=True,
        )
        thread.start()

        time.sleep(1)
        webbrowser.open(f"http://127.0.0.1:{args.port}")

        print(f"\nWeb服务已启动: http://127.0.0.1:{args.port}")
        print("按 Ctrl+C 退出\n")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n退出")
            sys.exit(0)

    elif args.cli:
        print("启动命令行监控模式...")
        from attention.main import main as cli_main
        cli_main()

    elif args.no_tray:
        print("启动调试模式（无托盘）...")
        from attention.ui.tray_app import run_without_tray
        run_without_tray()

    else:
        # 完整模式（带托盘）
        print("启动完整模式...")
        try:
            from attention.ui.tray_app import TRAY_AVAILABLE, run_with_tray, run_without_tray
            if TRAY_AVAILABLE:
                run_with_tray()
            else:
                print("托盘图标依赖未安装，回退到无托盘模式")
                print("  提示: pip install pystray pillow")
                run_without_tray()
        except Exception as e:
            print(f"启动失败: {e}")
            traceback.print_exc()
            print("\n尝试以无托盘模式启动...")
            try:
                from attention.ui.tray_app import run_without_tray
                run_without_tray()
            except Exception as e2:
                print(f"无托盘模式也失败: {e2}")
                traceback.print_exc()
                sys.exit(1)


if __name__ == "__main__":
    main()
