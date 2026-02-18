#!/usr/bin/env python3
"""
番茄钟浮窗子进程 — 始终悬浮在桌面上的迷你控制器。
被 pomodoro_overlay.py 作为独立子进程启动，拥有自己的主线程。

启动链: PyObjC (macOS) → tkinter → headless (stdin/stdout only)

通信协议：
  父→子 stdin (JSON):
    {"cmd":"update","time":"24:30","phase":"working","phase_label":"专注工作中",
     "color":"#34d399","cycle":1,"total_cycles":4}
    {"cmd":"quit"}
  子→父 stdout:
    "ready"
    "action:start" / "action:pause" / "action:resume" /
    "action:stop" / "action:skip_break" / "action:open_dashboard"
"""
import json
import platform
import signal
import sys
import threading
import traceback

SYSTEM = platform.system()

# 颜色
BG = "#12121f"
BG_BTN = "#1e1e32"
BG_BTN_HOVER = "#2a2a44"
TEXT_DIM = "#6b7084"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"
BLUE = "#60a5fa"


def emit(msg: str):
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def log(msg: str):
    """写到 stderr，父进程可以读取做诊断"""
    try:
        sys.stderr.write(f"[pomodoro_overlay] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


# ============================================================
# tkinter 实现（跨平台 + macOS fallback）
# ============================================================

def run_tkinter_overlay():
    log("尝试 tkinter 后端...")
    try:
        import tkinter as tk
    except ImportError:
        log("tkinter 不可用")
        run_headless()
        return

    try:
        root = tk.Tk()
    except Exception as e:
        log(f"tk.Tk() 失败: {e}")
        run_headless()
        return

    log("tkinter 初始化成功")

    root.title("🍅 Pomodoro")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=BG)

    if SYSTEM == "Windows":
        try:
            root.attributes("-alpha", 0.94)
        except Exception:
            pass
    if SYSTEM == "Darwin":
        try:
            root.call("::tk::unsupported::MacWindowStyle", "style",
                       root._w, "plain", "none")
        except Exception:
            pass

    WIN_W, WIN_H = 200, 110
    screen_w = root.winfo_screenwidth()
    x = screen_w - WIN_W - 16
    y = 44
    root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

    container = tk.Frame(root, bg=BG)
    container.pack(fill="both", expand=True, padx=1, pady=1)

    # 顶部: 阶段 + 周期
    top_row = tk.Frame(container, bg=BG)
    top_row.pack(fill="x", padx=10, pady=(8, 0))

    phase_var = tk.StringVar(value="🍅 番茄钟")
    phase_lbl = tk.Label(top_row, textvariable=phase_var,
                         font=("Helvetica", 10), fg=TEXT_DIM, bg=BG, anchor="w")
    phase_lbl.pack(side="left")

    cycle_var = tk.StringVar(value="")
    cycle_lbl = tk.Label(top_row, textvariable=cycle_var,
                         font=("Helvetica", 9), fg=TEXT_DIM, bg=BG, anchor="e")
    cycle_lbl.pack(side="right")

    # 中间: 时间
    font_mono = "JetBrains Mono" if SYSTEM != "Linux" else "Monospace"
    time_var = tk.StringVar(value="25:00")
    time_lbl = tk.Label(container, textvariable=time_var,
                        font=(font_mono, 28, "bold"), fg="#7a7f8a", bg=BG)
    time_lbl.pack(pady=(2, 4))

    # 底部: 按钮
    btn_frame = tk.Frame(container, bg=BG)
    btn_frame.pack(fill="x", padx=8, pady=(0, 8))

    state = {"phase": "idle"}

    def make_btn(parent, text, fg_color, command):
        b = tk.Label(parent, text=text, font=("Helvetica", 10, "bold"),
                     fg=fg_color, bg=BG_BTN, cursor="hand2", padx=8, pady=3)
        b.pack(side="left", padx=2, expand=True, fill="x")
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.config(bg=BG_BTN_HOVER))
        b.bind("<Leave>", lambda e: b.config(bg=BG_BTN))
        return b

    def rebuild_buttons():
        for w in btn_frame.winfo_children():
            w.destroy()
        p = state["phase"]
        if p == "idle":
            make_btn(btn_frame, "▶ 开始专注", GREEN, lambda: emit("action:start"))
        elif p == "working":
            make_btn(btn_frame, "⏸ 暂停", AMBER, lambda: emit("action:pause"))
            make_btn(btn_frame, "⏹ 停止", RED, lambda: emit("action:stop"))
        elif p == "paused":
            make_btn(btn_frame, "▶ 继续", GREEN, lambda: emit("action:resume"))
            make_btn(btn_frame, "⏹ 停止", RED, lambda: emit("action:stop"))
        elif p in ("short_break", "long_break"):
            make_btn(btn_frame, "⏩ 跳过休息", BLUE, lambda: emit("action:skip_break"))

    rebuild_buttons()

    # 拖动
    drag = {"x": 0, "y": 0}
    def on_press(e): drag["x"], drag["y"] = e.x, e.y
    def on_drag(e):
        root.geometry(f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}")
    for w in [container, top_row, phase_lbl, time_lbl]:
        w.bind("<Button-1>", on_press)
        w.bind("<B1-Motion>", on_drag)
        w.bind("<Double-Button-1>", lambda e: emit("action:open_dashboard"))

    # 命令队列
    cmd_queue = []
    cmd_lock = threading.Lock()

    def listen_stdin():
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    with cmd_lock:
                        cmd_queue.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
        with cmd_lock:
            cmd_queue.append({"cmd": "quit"})

    threading.Thread(target=listen_stdin, daemon=True).start()

    def process_commands():
        with cmd_lock:
            cmds = list(cmd_queue)
            cmd_queue.clear()

        need_rebuild = False
        for cmd in cmds:
            act = cmd.get("cmd", "")
            if act == "update":
                new_phase = cmd.get("phase", state["phase"])
                if new_phase != state["phase"]:
                    state["phase"] = new_phase
                    need_rebuild = True
                time_var.set(cmd.get("time", "25:00"))
                phase_var.set(cmd.get("phase_label", "🍅 番茄钟"))
                try:
                    time_lbl.config(fg=cmd.get("color", "#7a7f8a"))
                except Exception:
                    pass
                # 周期点
                c, tc = cmd.get("cycle", 0), cmd.get("total_cycles", 4)
                cycle_var.set("".join("●" if i <= c else "○" for i in range(1, tc + 1)) if c > 0 else "")
            elif act == "quit":
                root.destroy()
                return

        if need_rebuild:
            rebuild_buttons()
        try:
            root.attributes("-topmost", True)
            root.lift()
        except Exception:
            pass
        root.after(100, process_commands)

    emit("ready")
    log("tkinter 浮窗已就绪")
    root.after(100, process_commands)

    try:
        root.mainloop()
    except Exception as e:
        log(f"mainloop 退出: {e}")


# ============================================================
# macOS PyObjC 实现
# ============================================================

def run_macos_pyobjc():
    log("尝试 PyObjC 后端...")
    try:
        import objc
        from AppKit import (
            NSApplication, NSWindow, NSColor, NSFont,
            NSTextField, NSButton, NSScreen,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            NSTextAlignmentCenter,
            NSApp, NSEvent, NSApplicationDefined,
        )
        from Foundation import NSMakeRect, NSObject, NSTimer
        import Quartz
    except ImportError as e:
        log(f"PyObjC 导入失败: {e}")
        return False

    try:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(1)

        screen = NSScreen.mainScreen()
        frame = screen.frame()
        sw, sh = frame.size.width, frame.size.height

        WIN_W, WIN_H = 210, 120
        x = sw - WIN_W - 16
        y = sh - WIN_H - 45

        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, WIN_W, WIN_H),
            NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        window.setLevel_(Quartz.kCGFloatingWindowLevel)
        window.setOpaque_(False)
        window.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.07, 0.07, 0.12, 0.94))
        window.setMovableByWindowBackground_(True)
        window.setCollectionBehavior_(1 << 0)
        window.setHasShadow_(True)

        content = window.contentView()

        # UI
        phase_label = NSTextField.alloc().initWithFrame_(NSMakeRect(12, 92, 130, 18))
        phase_label.setStringValue_("🍅 番茄钟")
        phase_label.setFont_(NSFont.systemFontOfSize_(11))
        phase_label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.42, 0.44, 0.52, 1))
        phase_label.setDrawsBackground_(False)
        phase_label.setBezeled_(False)
        phase_label.setEditable_(False)
        phase_label.setSelectable_(False)
        content.addSubview_(phase_label)

        cycle_label = NSTextField.alloc().initWithFrame_(NSMakeRect(140, 92, 60, 18))
        cycle_label.setStringValue_("")
        cycle_label.setFont_(NSFont.systemFontOfSize_(10))
        cycle_label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.42, 0.44, 0.52, 1))
        cycle_label.setDrawsBackground_(False)
        cycle_label.setBezeled_(False)
        cycle_label.setEditable_(False)
        cycle_label.setSelectable_(False)
        cycle_label.setAlignment_(NSTextAlignmentCenter)
        content.addSubview_(cycle_label)

        time_label = NSTextField.alloc().initWithFrame_(NSMakeRect(12, 46, 186, 42))
        time_label.setStringValue_("25:00")
        time_label.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(30, 0.3))
        time_label.setTextColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.48, 0.5, 0.54, 1))
        time_label.setDrawsBackground_(False)
        time_label.setBezeled_(False)
        time_label.setEditable_(False)
        time_label.setSelectable_(False)
        time_label.setAlignment_(NSTextAlignmentCenter)
        content.addSubview_(time_label)

        # 按钮
        btn_left = NSButton.alloc().initWithFrame_(NSMakeRect(12, 8, 186, 30))
        btn_left.setTitle_("▶ 开始专注")
        btn_left.setBezelStyle_(1)
        content.addSubview_(btn_left)

        btn_right = NSButton.alloc().initWithFrame_(NSMakeRect(108, 8, 88, 30))
        btn_right.setTitle_("⏹ 停止")
        btn_right.setBezelStyle_(1)
        btn_right.setHidden_(True)
        content.addSubview_(btn_right)

        cur_state = {"phase": "idle"}

        def update_buttons(phase):
            if phase == "idle":
                btn_left.setTitle_("▶ 开始专注")
                btn_left.setFrame_(NSMakeRect(12, 8, 186, 30))
                btn_left.setHidden_(False)
                btn_right.setHidden_(True)
            elif phase == "working":
                btn_left.setTitle_("⏸ 暂停")
                btn_left.setFrame_(NSMakeRect(12, 8, 88, 30))
                btn_left.setHidden_(False)
                btn_right.setTitle_("⏹ 停止")
                btn_right.setHidden_(False)
            elif phase == "paused":
                btn_left.setTitle_("▶ 继续")
                btn_left.setFrame_(NSMakeRect(12, 8, 88, 30))
                btn_left.setHidden_(False)
                btn_right.setTitle_("⏹ 停止")
                btn_right.setHidden_(False)
            elif phase in ("short_break", "long_break"):
                btn_left.setTitle_("⏩ 跳过休息")
                btn_left.setFrame_(NSMakeRect(12, 8, 186, 30))
                btn_left.setHidden_(False)
                btn_right.setHidden_(True)

        class Delegate(NSObject):
            def init(self):
                self = objc.super(Delegate, self).init()
                return self

            @objc.typedSelector(b"v@:@")
            def leftClicked_(self, sender):
                p = cur_state["phase"]
                actions = {"idle": "start", "working": "pause",
                           "paused": "resume", "short_break": "skip_break",
                           "long_break": "skip_break"}
                emit(f"action:{actions.get(p, 'start')}")

            @objc.typedSelector(b"v@:@")
            def rightClicked_(self, sender):
                emit("action:stop")

            @objc.typedSelector(b"v@:@")
            def processCommand_(self, ns_timer):
                while cmd_queue:
                    cmd = cmd_queue.pop(0)
                    act = cmd.get("cmd", "")
                    if act == "update":
                        new_phase = cmd.get("phase", cur_state["phase"])
                        time_label.setStringValue_(cmd.get("time", "25:00"))
                        phase_label.setStringValue_(cmd.get("phase_label", "🍅 番茄钟"))
                        color_hex = cmd.get("color", "#7a7f8a")
                        try:
                            r = int(color_hex[1:3], 16) / 255.0
                            g = int(color_hex[3:5], 16) / 255.0
                            b = int(color_hex[5:7], 16) / 255.0
                            time_label.setTextColor_(
                                NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1))
                        except Exception:
                            pass
                        c = cmd.get("cycle", 0)
                        tc = cmd.get("total_cycles", 4)
                        cycle_label.setStringValue_(
                            "".join("●" if i <= c else "○" for i in range(1, tc + 1)) if c > 0 else "")
                        if new_phase != cur_state["phase"]:
                            cur_state["phase"] = new_phase
                            update_buttons(new_phase)
                        window.orderFrontRegardless()
                    elif act == "quit":
                        window.orderOut_(None)
                        window.close()
                        NSApp.stop_(None)
                        e = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                            NSApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
                        NSApp.postEvent_atStart_(e, True)

        delegate = Delegate.alloc().init()
        btn_left.setTarget_(delegate)
        btn_left.setAction_(objc.selector(delegate.leftClicked_, signature=b"v@:@"))
        btn_right.setTarget_(delegate)
        btn_right.setAction_(objc.selector(delegate.rightClicked_, signature=b"v@:@"))

        cmd_queue = []

        def listen_stdin():
            try:
                for line in sys.stdin:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        cmd_queue.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass
            cmd_queue.append({"cmd": "quit"})

        threading.Thread(target=listen_stdin, daemon=True).start()

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, delegate, delegate.processCommand_, None, True)

        window.makeKeyAndOrderFront_(None)
        window.orderFrontRegardless()

        emit("ready")
        log("PyObjC 浮窗已就绪")
        NSApp.run()
        return True

    except Exception as e:
        log(f"PyObjC 运行失败: {e}\n{traceback.format_exc()}")
        return False


# ============================================================
# Headless 后备（无 GUI 环境）
# ============================================================

def run_headless():
    """无 GUI — 仅维持 stdin/stdout 通信，不显示任何窗口"""
    log("使用 headless 模式（无浮窗 GUI）")
    emit("ready")
    try:
        for line in sys.stdin:
            try:
                cmd = json.loads(line.strip())
                if cmd.get("cmd") == "quit":
                    break
            except Exception:
                pass
    except Exception:
        pass


# ============================================================
# 入口
# ============================================================

def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    log(f"浮窗子进程启动 (platform={SYSTEM}, python={sys.version})")

    if SYSTEM == "Darwin":
        # macOS: PyObjC → tkinter → headless
        ok = run_macos_pyobjc()
        if not ok:
            log("PyObjC 失败，回退到 tkinter")
            run_tkinter_overlay()
    else:
        run_tkinter_overlay()


if __name__ == "__main__":
    main()
