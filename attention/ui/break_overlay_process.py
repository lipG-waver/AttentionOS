#!/usr/bin/env python3
"""
休息遮罩子进程
被 desktop_overlay.py 作为独立子进程启动。
拥有自己的主线程，因此 PyObjC (macOS) / tkinter 的 GUI 能正常工作。

通信协议（通过 stdin/stdout）：
  父进程 → 子进程 stdin:  "skip\n"  请求跳过
  子进程 → 父进程 stdout: "started\n" / "ended\n" / "skipped\n"

启动参数：
  python break_overlay_process.py <duration_seconds>
"""
import sys
import platform
import threading
import time
import random
import signal

SYSTEM = platform.system()
TIPS = [
    "☁️ 闭上眼睛，深呼吸三次",
    "🌿 站起来伸展一下身体",
    "👀 看看远处，放松眼部肌肉",
    "💧 去喝一杯水吧",
    "🧘 转转脖子，活动肩膀",
    "🌅 望向窗外，享受片刻宁静",
]


def emit(msg: str):
    """向父进程发送消息"""
    try:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except:
        pass


# ============================================================
# macOS 实现 — 用 PyObjC 在主线程创建全屏窗口
# ============================================================

def run_macos_overlay(total_seconds: int):
    """macOS: PyObjC 全屏遮罩（在主线程运行）"""
    try:
        import objc
        from AppKit import (
            NSApplication, NSWindow, NSColor, NSFont,
            NSTextField, NSButton, NSScreen,
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            NSTextAlignmentCenter,
            NSApp,
        )
        from Foundation import NSMakeRect, NSObject, NSTimer, NSRunLoop, NSDefaultRunLoopMode
        from PyObjCTools import AppHelper
        import Quartz
    except ImportError:
        # PyObjC 不可用，回退到 tkinter
        run_tkinter_overlay(total_seconds)
        return

    # 初始化 NSApplication（必须在 mainloop 前）
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    screen = NSScreen.mainScreen()
    frame = screen.frame()
    sw, sh = frame.size.width, frame.size.height

    # --- 全屏窗口 ---
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, sw, sh),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False,
    )
    window.setLevel_(Quartz.kCGScreenSaverWindowLevel)
    window.setOpaque_(False)
    window.setBackgroundColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.05, 0.12, 0.82)
    )
    window.setIgnoresMouseEvents_(False)
    # 覆盖所有桌面空间
    window.setCollectionBehavior_(1 << 0)  # canJoinAllSpaces

    content = window.contentView()

    # --- UI 元素 ---
    # 标题
    title = NSTextField.alloc().initWithFrame_(NSMakeRect(sw / 2 - 200, sh / 2 + 140, 400, 45))
    title.setStringValue_("🌙 休息时间")
    title.setFont_(NSFont.systemFontOfSize_weight_(32, 0.5))
    title.setTextColor_(NSColor.whiteColor())
    title.setDrawsBackground_(False)
    title.setBezeled_(False)
    title.setEditable_(False)
    title.setSelectable_(False)
    title.setAlignment_(NSTextAlignmentCenter)
    content.addSubview_(title)

    # 倒计时
    mins0, secs0 = divmod(total_seconds, 60)
    timer_label = NSTextField.alloc().initWithFrame_(NSMakeRect(sw / 2 - 160, sh / 2 + 30, 320, 90))
    timer_label.setStringValue_(f"{mins0:02d}:{secs0:02d}")
    timer_label.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(72, 0.2))
    timer_label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.29, 0.87, 0.5, 1))
    timer_label.setDrawsBackground_(False)
    timer_label.setBezeled_(False)
    timer_label.setEditable_(False)
    timer_label.setSelectable_(False)
    timer_label.setAlignment_(NSTextAlignmentCenter)
    content.addSubview_(timer_label)

    # 提示文字
    tip_label = NSTextField.alloc().initWithFrame_(NSMakeRect(sw / 2 - 220, sh / 2 - 40, 440, 30))
    tip_label.setStringValue_(random.choice(TIPS))
    tip_label.setFont_(NSFont.systemFontOfSize_(17))
    tip_label.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.7, 0.7, 0.75, 1))
    tip_label.setDrawsBackground_(False)
    tip_label.setBezeled_(False)
    tip_label.setEditable_(False)
    tip_label.setSelectable_(False)
    tip_label.setAlignment_(NSTextAlignmentCenter)
    content.addSubview_(tip_label)

    # 跳过按钮
    skip_btn = NSButton.alloc().initWithFrame_(NSMakeRect(sw / 2 - 55, sh / 2 - 110, 110, 34))
    skip_btn.setTitle_("跳过休息")
    skip_btn.setBezelStyle_(1)

    # --- Controller (ObjC callable) ---
    class OverlayDelegate(NSObject):
        remaining = objc.ivar("remaining", objc._C_INT)
        tick_timer = objc.ivar("tick_timer")

        def init(self):
            self = objc.super(OverlayDelegate, self).init()
            if self is None:
                return None
            self.remaining = total_seconds
            return self

        @objc.typedSelector(b"v@:@")
        def tick_(self, ns_timer):
            self.remaining -= 1
            if self.remaining <= 0:
                self.finish()
                return
            m, s = divmod(self.remaining, 60)
            timer_label.setStringValue_(f"{m:02d}:{s:02d}")
            if self.remaining % 30 == 0:
                tip_label.setStringValue_(random.choice(TIPS))
            # 保持最前
            window.orderFrontRegardless()

        @objc.typedSelector(b"v@:@")
        def skipClicked_(self, sender):
            emit("skipped")
            self.teardown()

        @objc.typedSelector(b"v@:@")
        def stdinSkip_(self, ns_timer):
            """被 stdin 监听线程通过 performSelector 调用"""
            emit("skipped")
            self.teardown()

        def finish(self):
            emit("ended")
            self.teardown()

        def teardown(self):
            if self.tick_timer:
                self.tick_timer.invalidate()
                self.tick_timer = None
            window.orderOut_(None)
            window.close()
            NSApp.stop_(None)
            # 发一个 dummy 事件让 run loop 退出
            from AppKit import NSEvent, NSApplicationDefined, NSEventModifierFlagCommand
            e = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0
            )
            NSApp.postEvent_atStart_(e, True)

    delegate = OverlayDelegate.alloc().init()
    skip_btn.setTarget_(delegate)
    skip_btn.setAction_(objc.selector(delegate.skipClicked_, signature=b"v@:@"))
    content.addSubview_(skip_btn)

    # --- stdin 监听（子线程） ---
    def listen_stdin():
        """监听父进程的 skip 命令"""
        try:
            for line in sys.stdin:
                cmd = line.strip()
                if cmd == "skip":
                    # 安全地在主线程执行
                    delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                        delegate.stdinSkip_, None, False
                    )
                    return
        except:
            pass

    stdin_thread = threading.Thread(target=listen_stdin, daemon=True)
    stdin_thread.start()

    # --- 启动 ---
    # 1秒 tick 定时器
    delegate.tick_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, delegate, delegate.tick_, None, True
    )

    window.makeKeyAndOrderFront_(None)
    window.orderFrontRegardless()
    emit("started")

    NSApp.run()  # 阻塞主线程，直到 NSApp.stop_() 被调用


# ============================================================
# tkinter 实现（Windows / Linux / macOS fallback）
# ============================================================

def run_tkinter_overlay(total_seconds: int):
    """跨平台 tkinter 全屏遮罩"""
    try:
        import tkinter as tk
    except ImportError:
        # 完全没有 GUI 能力，直接等待然后结束
        emit("started")
        time.sleep(total_seconds)
        emit("ended")
        return

    try:
        root = tk.Tk()
    except Exception as e:
        # tkinter 初始化失败（macOS + Python 3.13 + Tk 8.6 已知问题）
        print(f"tkinter init failed: {e}", file=sys.stderr)
        emit("started")
        time.sleep(total_seconds)
        emit("ended")
        return
    root.title("休息时间")
    root.configure(bg="#0d0d1a")
    root.attributes("-topmost", True)
    root.overrideredirect(True)

    # 全屏
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")

    if SYSTEM == "Darwin":
        # macOS 需要额外处理才能真正覆盖全屏
        try:
            root.attributes("-fullscreen", True)
        except:
            pass

    # --- UI ---
    frame = tk.Frame(root, bg="#0d0d1a")
    frame.place(relx=0.5, rely=0.5, anchor="center")

    tk.Label(
        frame, text="🌙 休息时间",
        font=("Helvetica", 32, "bold"), fg="white", bg="#0d0d1a",
    ).pack(pady=(0, 20))

    mins0, secs0 = divmod(total_seconds, 60)
    timer_var = tk.StringVar(value=f"{mins0:02d}:{secs0:02d}")
    tk.Label(
        frame, textvariable=timer_var,
        font=("Courier", 64, "bold"), fg="#4ade80", bg="#0d0d1a",
    ).pack(pady=(0, 20))

    tip_var = tk.StringVar(value=random.choice(TIPS))
    tk.Label(
        frame, textvariable=tip_var,
        font=("Helvetica", 16), fg="#94a3b8", bg="#0d0d1a",
    ).pack(pady=(0, 30))

    skipped = {"v": False}

    def on_skip():
        skipped["v"] = True
        emit("skipped")
        root.destroy()

    tk.Button(
        frame, text="跳过休息",
        font=("Helvetica", 13), fg="white", bg="#334155",
        activebackground="#475569", activeforeground="white",
        command=on_skip, padx=16, pady=6, relief="flat", cursor="hand2",
    ).pack()

    # --- stdin 监听 ---
    def listen_stdin():
        try:
            for line in sys.stdin:
                if line.strip() == "skip":
                    root.after(0, on_skip)
                    return
        except:
            pass

    stdin_thread = threading.Thread(target=listen_stdin, daemon=True)
    stdin_thread.start()

    # --- 倒计时 ---
    start_time = time.time()

    def tick():
        if skipped["v"]:
            return
        elapsed = time.time() - start_time
        remaining = int(total_seconds - elapsed)
        if remaining <= 0:
            emit("ended")
            root.destroy()
            return
        m, s = divmod(remaining, 60)
        timer_var.set(f"{m:02d}:{s:02d}")
        if remaining % 30 == 0:
            tip_var.set(random.choice(TIPS))
        # 保持最前
        try:
            root.attributes("-topmost", True)
            root.lift()
        except:
            pass
        root.after(1000, tick)

    emit("started")
    root.after(1000, tick)

    # 禁止关闭 / Alt+F4
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    # 禁止 Escape
    root.bind("<Escape>", lambda e: None)

    try:
        root.mainloop()
    except:
        pass


# ============================================================
# 入口
# ============================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python break_overlay_process.py <duration_seconds>", file=sys.stderr)
        sys.exit(1)

    total_seconds = int(sys.argv[1])

    # 忽略 SIGINT，让父进程管理生命周期
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    if SYSTEM == "Darwin":
        run_macos_overlay(total_seconds)
    else:
        run_tkinter_overlay(total_seconds)


if __name__ == "__main__":
    main()
