#!/usr/bin/env python3
"""
统一对话悬浮窗子进程 — Attention OS 的唯一交互入口

两种形态：
  1. 收起态（Ball）：桌面右下角的小球，显示状态 + 计时器动画
  2. 展开态（Chat）：带对话消息列表、输入框的聊天窗口

所有交互通过对话完成：
  - 分心提醒 → 对话气泡弹出
  - 专注模式 → 小球显示计时，点击可记录想法
  - 休息提醒 → 对话开场
  - 日常交流 → 自由对话

通信协议 (父→子 stdin, JSON):
  {"cmd": "ai_message", "text": "...", "type": "chat|nudge|status|thought_confirm"}
  {"cmd": "update_timer", "time": "24:30", "phase": "working|break|idle", "progress": 0.75}
  {"cmd": "expand"}             — 展开对话窗
  {"cmd": "collapse"}           — 收起为小球
  {"cmd": "set_mood", "mood": "happy|worried|focused|sleeping"}
  {"cmd": "quit"}

子→父 stdout (JSON):
  {"type": "ready"}
  {"type": "user_message", "text": "..."}
  {"type": "action", "action": "start_focus|pause|resume|stop|skip_break"}
  {"type": "expand"}            — 用户点击展开
  {"type": "collapse"}          — 用户点击收起
"""
import json
import math
import platform
import signal
import sys
import threading
import time
import traceback

SYSTEM = platform.system()

# ─── 设计系统 ───
# 深色主题（默认）
_PALETTE_DARK = {
    "BG_DARK":      "#0d0d14",
    "BG_PANEL":     "#141420",
    "BG_INPUT":     "#1c1c2e",
    "BG_HOVER":     "#2a2a3e",
    "BG_MSG_AI":    "#1e2a3a",
    "BG_MSG_USER":  "#1a3a2a",
    "BG_MSG_NUDGE": "#3a2a1a",
    "BG_MSG_STATUS":"#1a1a2e",
    "TEXT_PRIMARY": "#e0e4ec",
    "TEXT_DIM":     "#6b7084",
    "TEXT_MUTED":   "#4a4e5a",
    "GREEN":        "#34d399",
    "GREEN_DIM":    "#1a3a2a",
    "AMBER":        "#fbbf24",
    "RED":          "#f87171",
    "BLUE":         "#60a5fa",
    "PURPLE":       "#a78bfa",
}
# 浅色主题
_PALETTE_LIGHT = {
    "BG_DARK":      "#f0f1f5",
    "BG_PANEL":     "#ffffff",
    "BG_INPUT":     "#e4e6ef",
    "BG_HOVER":     "#d0d3e0",
    "BG_MSG_AI":    "#dceeff",
    "BG_MSG_USER":  "#dcf0e4",
    "BG_MSG_NUDGE": "#fff3e0",
    "BG_MSG_STATUS":"#ebebff",
    "TEXT_PRIMARY": "#1a1c24",
    "TEXT_DIM":     "#5a5e6a",
    "TEXT_MUTED":   "#8a8e9a",
    "GREEN":        "#16a34a",
    "GREEN_DIM":    "#dcfce7",
    "AMBER":        "#d97706",
    "RED":          "#dc2626",
    "BLUE":         "#2563eb",
    "PURPLE":       "#7c3aed",
}

BG_DARK      = _PALETTE_DARK["BG_DARK"]
BG_PANEL     = _PALETTE_DARK["BG_PANEL"]
BG_INPUT     = _PALETTE_DARK["BG_INPUT"]
BG_HOVER     = _PALETTE_DARK["BG_HOVER"]
BG_MSG_AI    = _PALETTE_DARK["BG_MSG_AI"]
BG_MSG_USER  = _PALETTE_DARK["BG_MSG_USER"]
BG_MSG_NUDGE = _PALETTE_DARK["BG_MSG_NUDGE"]
BG_MSG_STATUS= _PALETTE_DARK["BG_MSG_STATUS"]
TEXT_PRIMARY = _PALETTE_DARK["TEXT_PRIMARY"]
TEXT_DIM     = _PALETTE_DARK["TEXT_DIM"]
TEXT_MUTED   = _PALETTE_DARK["TEXT_MUTED"]
GREEN        = _PALETTE_DARK["GREEN"]
GREEN_DIM    = _PALETTE_DARK["GREEN_DIM"]
AMBER        = _PALETTE_DARK["AMBER"]
RED          = _PALETTE_DARK["RED"]
BLUE         = _PALETTE_DARK["BLUE"]
PURPLE       = _PALETTE_DARK["PURPLE"]


def _apply_palette(palette: dict):
    """将调色板应用到模块级颜色变量"""
    global BG_DARK, BG_PANEL, BG_INPUT, BG_HOVER
    global BG_MSG_AI, BG_MSG_USER, BG_MSG_NUDGE, BG_MSG_STATUS
    global TEXT_PRIMARY, TEXT_DIM, TEXT_MUTED
    global GREEN, GREEN_DIM, AMBER, RED, BLUE, PURPLE
    BG_DARK       = palette["BG_DARK"]
    BG_PANEL      = palette["BG_PANEL"]
    BG_INPUT      = palette["BG_INPUT"]
    BG_HOVER      = palette["BG_HOVER"]
    BG_MSG_AI     = palette["BG_MSG_AI"]
    BG_MSG_USER   = palette["BG_MSG_USER"]
    BG_MSG_NUDGE  = palette["BG_MSG_NUDGE"]
    BG_MSG_STATUS = palette["BG_MSG_STATUS"]
    TEXT_PRIMARY  = palette["TEXT_PRIMARY"]
    TEXT_DIM      = palette["TEXT_DIM"]
    TEXT_MUTED    = palette["TEXT_MUTED"]
    GREEN         = palette["GREEN"]
    GREEN_DIM     = palette["GREEN_DIM"]
    AMBER         = palette["AMBER"]
    RED           = palette["RED"]
    BLUE          = palette["BLUE"]
    PURPLE        = palette["PURPLE"]

# 小球尺寸
BALL_SIZE = 56
# 展开窗口尺寸
CHAT_W = 360
CHAT_H = 500

FONT_FAMILY = "Helvetica"
if SYSTEM == "Darwin":
    FONT_FAMILY = "SF Pro Text"


def emit(data):
    """发送 JSON 消息到父进程"""
    try:
        if isinstance(data, str):
            data = {"type": data}
        sys.stdout.write(json.dumps(data, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def log(msg):
    try:
        sys.stderr.write(f"[chat_overlay] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


# ============================================================
# tkinter 实现
# ============================================================

def run_tkinter():
    log("启动 tkinter 对话悬浮窗...")
    try:
        import tkinter as tk
        from tkinter import font as tkfont
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

    # macOS: 在 tk.Tk() 成功之后设置 NSApplication 属性
    # 这样 Tk 的 Cocoa 初始化不会被干扰
    _post_tk_macos_init()

    root.title("Attention OS")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=BG_DARK)

    if SYSTEM == "Windows":
        try:
            root.attributes("-alpha", 0.96)
        except Exception:
            pass
    if SYSTEM == "Darwin":
        try:
            root.call("::tk::unsupported::MacWindowStyle", "style",
                       root._w, "plain", "none")
            root.attributes("-alpha", 0.96)
        except Exception:
            pass
        # 使用 Quartz 设置浮动窗口级别，确保始终在最上层
        try:
            import Quartz
            from AppKit import NSApp
            # 延迟设置窗口级别（需要先显示窗口才能获取 NSWindow）
            def _set_macos_window_level():
                try:
                    root.update_idletasks()
                    # 获取 tkinter 窗口对应的 NSWindow
                    ns_windows = NSApp.windows()
                    for w in ns_windows:
                        w.setLevel_(Quartz.kCGFloatingWindowLevel)
                        w.setCollectionBehavior_(1 << 0)  # 所有桌面可见
                    log("macOS 窗口级别已设置为 FloatingWindowLevel")
                except Exception as e:
                    log(f"设置 macOS 窗口级别失败: {e}")
            root.after(200, _set_macos_window_level)
        except ImportError:
            log("Quartz 不可用，使用 tkinter 默认 topmost")

    # ─── 状态 ───
    state = {
        "expanded": False,
        "phase": "idle",       # idle | working | short_break | long_break | paused
        "time_text": "",
        "progress": 0.0,       # 0~1 计时器进度
        "mood": "normal",      # normal | happy | worried | focused | sleeping
        "messages": [],        # [{role, content, msg_type, timestamp}]
        "unread": 0,           # 未读消息数
        "pulse_angle": 0.0,    # 呼吸动画角度
        "mode": "ai",          # ai | memo | focus
    }

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()

    # 初始位置：右下角
    ball_x = screen_w - BALL_SIZE - 20
    ball_y = screen_h - BALL_SIZE - 80
    root.geometry(f"{BALL_SIZE}x{BALL_SIZE}+{ball_x}+{ball_y}")

    # ─── 小球画布 ───
    ball_canvas = tk.Canvas(root, width=BALL_SIZE, height=BALL_SIZE,
                           bg=BG_DARK, highlightthickness=0, bd=0)
    ball_canvas.pack(fill="both", expand=True)

    # ─── 展开窗口 Frame（初始隐藏）───
    chat_frame = tk.Frame(root, bg=BG_PANEL, bd=0)

    # 头部
    header = tk.Frame(chat_frame, bg=BG_DARK, height=44)
    header.pack(fill="x")
    header.pack_propagate(False)

    header_left = tk.Frame(header, bg=BG_DARK)
    header_left.pack(side="left", padx=12, pady=8)

    status_dot = tk.Canvas(header_left, width=10, height=10, bg=BG_DARK,
                           highlightthickness=0)
    status_dot.pack(side="left", padx=(0, 6))
    status_dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="", tags="dot")

    title_label = tk.Label(header_left, text="Attention OS", font=(FONT_FAMILY, 12, "bold"),
                           fg=TEXT_PRIMARY, bg=BG_DARK)
    title_label.pack(side="left")

    timer_label = tk.Label(header, text="", font=("JetBrains Mono", 11),
                           fg=TEXT_DIM, bg=BG_DARK)
    timer_label.pack(side="right", padx=12)

    # 收起按钮
    collapse_btn = tk.Label(header, text="▾", font=(FONT_FAMILY, 16),
                            fg=TEXT_DIM, bg=BG_DARK, cursor="hand2")
    collapse_btn.pack(side="right", padx=(0, 4))

    # 新对话按钮
    new_chat_btn = tk.Label(header, text="＋", font=(FONT_FAMILY, 14),
                            fg=TEXT_DIM, bg=BG_DARK, cursor="hand2")
    new_chat_btn.pack(side="right", padx=(0, 4))
    new_chat_btn.bind("<Enter>", lambda e: new_chat_btn.config(fg=GREEN))
    new_chat_btn.bind("<Leave>", lambda e: new_chat_btn.config(fg=TEXT_DIM))

    # 消息列表区域
    msg_container = tk.Frame(chat_frame, bg=BG_PANEL)
    msg_container.pack(fill="both", expand=True, padx=0, pady=0)

    msg_canvas = tk.Canvas(msg_container, bg=BG_PANEL, highlightthickness=0, bd=0)
    msg_scrollbar = tk.Scrollbar(msg_container, orient="vertical", command=msg_canvas.yview)
    msg_inner = tk.Frame(msg_canvas, bg=BG_PANEL)

    msg_inner.bind("<Configure>",
                   lambda e: msg_canvas.configure(scrollregion=msg_canvas.bbox("all")))
    msg_canvas.create_window((0, 0), window=msg_inner, anchor="nw", width=CHAT_W - 2)
    msg_canvas.configure(yscrollcommand=msg_scrollbar.set)

    msg_canvas.pack(side="left", fill="both", expand=True)
    # 鼠标滚轮滚动
    def _on_mousewheel(e):
        if SYSTEM == "Linux":
            if e.num == 4:
                msg_canvas.yview_scroll(-1, "units")
            elif e.num == 5:
                msg_canvas.yview_scroll(1, "units")
        else:
            msg_canvas.yview_scroll(-1 * (e.delta // 120), "units")
    if SYSTEM == "Linux":
        msg_canvas.bind("<Button-4>", _on_mousewheel)
        msg_canvas.bind("<Button-5>", _on_mousewheel)
        msg_inner.bind("<Button-4>", _on_mousewheel)
        msg_inner.bind("<Button-5>", _on_mousewheel)
    else:
        msg_canvas.bind("<MouseWheel>", _on_mousewheel)
        msg_inner.bind("<MouseWheel>", _on_mousewheel)

    # 输入区域
    input_frame = tk.Frame(chat_frame, bg=BG_DARK, height=52)
    input_frame.pack(fill="x", side="bottom")
    input_frame.pack_propagate(False)

    input_entry = tk.Entry(input_frame, font=(FONT_FAMILY, 13),
                           bg=BG_INPUT, fg=TEXT_PRIMARY,
                           insertbackground=TEXT_PRIMARY,
                           relief="flat", bd=0)
    input_entry.pack(fill="x", padx=(12, 50), pady=10, ipady=4)
    input_entry.insert(0, "")

    # placeholder
    def on_entry_focus_in(e):
        if input_entry.get() == "":
            pass
    def on_entry_focus_out(e):
        pass

    input_entry.bind("<FocusIn>", on_entry_focus_in)
    input_entry.bind("<FocusOut>", on_entry_focus_out)

    send_btn = tk.Label(input_frame, text="↑", font=(FONT_FAMILY, 16, "bold"),
                        fg=GREEN, bg=BG_INPUT, cursor="hand2", padx=6, pady=2)
    send_btn.place(relx=1.0, rely=0.5, anchor="e", x=-14)

    # ─── 专注控制栏（暂停/继续/停止/跳过休息）───
    focus_bar = tk.Frame(chat_frame, bg=BG_DARK, height=36)
    # 不立即 pack，按需显示
    focus_bar.pack_propagate(False)
    focus_bar_widgets = []  # 存放动态按钮引用

    def _make_focus_btn(parent, text, fg_color, action_name):
        """创建专注控制按钮"""
        btn_bg = BG_INPUT
        btn = tk.Label(
            parent, text=text,
            font=(FONT_FAMILY, 11, "bold"),
            fg=fg_color, bg=btn_bg,
            cursor="hand2", padx=12, pady=4,
        )
        btn.bind("<Button-1>", lambda e: emit({"type": "action", "action": action_name}))
        # hover 效果
        btn.bind("<Enter>", lambda e: btn.config(bg=BG_HOVER))
        btn.bind("<Leave>", lambda e: btn.config(bg=BG_INPUT))
        return btn

    def update_focus_bar():
        """根据当前 phase 和 mode 更新专注控制栏的按钮"""
        phase = state["phase"]
        mode = state["mode"]
        # 清除旧按钮
        for w in focus_bar_widgets:
            w.destroy()
        focus_bar_widgets.clear()
        focus_bar.pack_forget()

        if phase == "working":
            b1 = _make_focus_btn(focus_bar, "⏸ 暂停", AMBER, "pause")
            b1.pack(side="left", padx=(12, 4), pady=4)
            focus_bar_widgets.append(b1)
            b2 = _make_focus_btn(focus_bar, "⏹ 停止", RED, "stop")
            b2.pack(side="left", padx=4, pady=4)
            focus_bar_widgets.append(b2)
            focus_bar.pack(fill="x", side="bottom", before=mode_frame)
        elif phase == "paused":
            b1 = _make_focus_btn(focus_bar, "▶ 继续", GREEN, "resume")
            b1.pack(side="left", padx=(12, 4), pady=4)
            focus_bar_widgets.append(b1)
            b2 = _make_focus_btn(focus_bar, "⏹ 停止", RED, "stop")
            b2.pack(side="left", padx=4, pady=4)
            focus_bar_widgets.append(b2)
            focus_bar.pack(fill="x", side="bottom", before=mode_frame)
        elif phase in ("short_break", "long_break"):
            b1 = _make_focus_btn(focus_bar, "⏩ 跳过休息", BLUE, "skip_break")
            b1.pack(side="left", padx=(12, 4), pady=4)
            focus_bar_widgets.append(b1)
            focus_bar.pack(fill="x", side="bottom", before=mode_frame)
        elif phase == "idle" and mode == "focus":
            # 空闲 + 专注标签：显示"开始专注"按钮，等待用户主动确认
            b1 = _make_focus_btn(focus_bar, "▶ 开始专注", GREEN, "start_focus")
            b1.pack(side="left", padx=(12, 4), pady=4)
            focus_bar_widgets.append(b1)
            focus_bar.pack(fill="x", side="bottom", before=mode_frame)

    # ─── 模式标签栏（随手记 / 问 AI / 专注模式）───
    mode_frame = tk.Frame(chat_frame, bg=BG_DARK, height=32)
    mode_frame.pack(fill="x", side="bottom")
    mode_frame.pack_propagate(False)

    MODE_DEFS = [
        ("memo",  "📝 随手记"),
        ("ai",    "🤖 问 AI"),
        ("focus", "🎯 专注"),
    ]
    mode_btns = {}

    def set_mode_ui(m):
        state["mode"] = m
        for key, btn in mode_btns.items():
            if key == m:
                btn.config(fg=GREEN, bg=BG_PANEL)
            else:
                btn.config(fg=TEXT_DIM, bg=BG_DARK)

        # 切换标签时更新控制栏（专注标签在空闲时显示"开始专注"按钮）
        if state["expanded"]:
            update_focus_bar()

        input_entry.config(fg=TEXT_PRIMARY)

    for mode_key, mode_label in MODE_DEFS:
        is_active = (mode_key == "ai")
        btn = tk.Label(
            mode_frame,
            text=mode_label,
            font=(FONT_FAMILY, 10),
            fg=GREEN if is_active else TEXT_DIM,
            bg=BG_PANEL if is_active else BG_DARK,
            cursor="hand2",
            padx=10, pady=4,
        )
        btn.pack(side="left", fill="y")
        btn.bind("<Button-1>", lambda e, m=mode_key: set_mode_ui(m))
        mode_btns[mode_key] = btn

    # ─── 消息渲染 ───
    def render_messages():
        """重新渲染所有消息"""
        for w in msg_inner.winfo_children():
            w.destroy()

        for msg in state["messages"][-50:]:  # 最多显示 50 条
            render_single_message(msg)

        # 滚动到底部
        root.after(50, lambda: msg_canvas.yview_moveto(1.0))

    def render_single_message(msg):
        role = msg.get("role", "assistant")
        content = msg.get("content", "")
        msg_type = msg.get("msg_type", "chat")
        ts = msg.get("timestamp", "")
        time_str = ts[11:16] if len(ts) >= 16 else ""

        # 选择样式
        if role == "user":
            bg = BG_MSG_USER
            anchor = "e"
            padx = (50, 8)
        elif msg_type == "nudge":
            bg = BG_MSG_NUDGE
            anchor = "w"
            padx = (8, 50)
        elif msg_type == "status":
            bg = BG_MSG_STATUS
            anchor = "center"
            padx = (30, 30)
        else:
            bg = BG_MSG_AI
            anchor = "w"
            padx = (8, 50)

        wrapper = tk.Frame(msg_inner, bg=BG_PANEL)
        wrapper.pack(fill="x", padx=padx, pady=3, anchor=anchor)

        bubble = tk.Frame(wrapper, bg=bg, padx=10, pady=8)
        bubble.pack(fill="x")

        # 圆角效果通过 padding 模拟
        text_label = tk.Label(bubble, text=content, font=(FONT_FAMILY, 12),
                              fg=TEXT_PRIMARY, bg=bg, wraplength=230,
                              justify="left" if role != "user" else "right",
                              anchor="w")
        text_label.pack(fill="x")

        if time_str:
            time_lbl = tk.Label(bubble, text=time_str, font=(FONT_FAMILY, 9),
                                fg=TEXT_MUTED, bg=bg, anchor="e" if role == "user" else "w")
            time_lbl.pack(fill="x")

    def add_message(msg):
        state["messages"].append(msg)
        if not state["expanded"]:
            state["unread"] += 1
        render_single_message(msg)
        root.after(50, lambda: msg_canvas.yview_moveto(1.0))

    # ─── 展开/收起 ───
    def expand():
        if state["expanded"]:
            return
        state["expanded"] = True
        state["unread"] = 0

        # 隐藏小球
        ball_canvas.pack_forget()

        # 计算新位置
        cur_x = root.winfo_x()
        cur_y = root.winfo_y()
        new_x = max(0, min(cur_x - CHAT_W + BALL_SIZE, screen_w - CHAT_W))
        new_y = max(0, min(cur_y - CHAT_H + BALL_SIZE, screen_h - CHAT_H))

        root.geometry(f"{CHAT_W}x{CHAT_H}+{new_x}+{new_y}")
        chat_frame.pack(fill="both", expand=True)
        render_messages()
        update_focus_bar()
        input_entry.focus_set()
        emit({"type": "expand"})

    def collapse():
        if not state["expanded"]:
            return
        state["expanded"] = False

        chat_frame.pack_forget()

        cur_x = root.winfo_x()
        cur_y = root.winfo_y()
        new_x = cur_x + CHAT_W - BALL_SIZE
        new_y = cur_y + CHAT_H - BALL_SIZE

        root.geometry(f"{BALL_SIZE}x{BALL_SIZE}+{new_x}+{new_y}")
        ball_canvas.pack(fill="both", expand=True)
        emit({"type": "collapse"})

    # ─── 发送消息 ───
    def send_message(event=None):
        text = input_entry.get().strip()
        if not text:
            return
        input_entry.delete(0, "end")

        # 本地渲染
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        user_msg = {"role": "user", "content": text, "msg_type": "chat", "timestamp": ts}
        add_message(user_msg)

        # 发送给父进程（包含当前模式）
        emit({"type": "user_message", "text": text, "mode": state["mode"]})

    input_entry.bind("<Return>", send_message)
    send_btn.bind("<Button-1>", lambda e: send_message())

    # 收起按钮
    collapse_btn.bind("<Button-1>", lambda e: collapse())

    # 新对话 → 清空历史
    def new_conversation():
        state["messages"].clear()
        state["unread"] = 0
        for w in msg_inner.winfo_children():
            w.destroy()
        emit({"type": "new_conversation"})
    new_chat_btn.bind("<Button-1>", lambda e: new_conversation())

    # ─── 小球渲染 ───
    def draw_ball():
        ball_canvas.delete("all")
        cx, cy = BALL_SIZE // 2, BALL_SIZE // 2
        r = BALL_SIZE // 2 - 2

        phase = state["phase"]
        mood = state["mood"]
        progress = state["progress"]
        pulse = state["pulse_angle"]

        # 底色
        if phase == "working":
            base_color = GREEN
        elif phase in ("short_break", "long_break"):
            base_color = BLUE
        elif phase == "paused":
            base_color = AMBER
        else:
            base_color = "#4a5568"

        # 呼吸效果
        breath = 0.06 * math.sin(pulse)
        alpha_r = r + breath * r

        # 外环背景
        ball_canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                               fill=BG_DARK, outline="#2a2e3a", width=2)

        # 进度弧（专注/休息时显示）
        if phase in ("working", "short_break", "long_break") and progress > 0:
            extent = 360 * progress
            ball_canvas.create_arc(cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3,
                                   start=90, extent=-extent,
                                   fill="", outline=base_color, width=3,
                                   style="arc")

        # 中心文字
        if state["time_text"] and phase != "idle":
            ball_canvas.create_text(cx, cy - 2, text=state["time_text"],
                                    font=("JetBrains Mono", 10, "bold"),
                                    fill=base_color)
            # 小标签
            label = {"working": "专注", "short_break": "休息",
                     "long_break": "长休", "paused": "暂停"}.get(phase, "")
            ball_canvas.create_text(cx, cy + 14, text=label,
                                    font=(FONT_FAMILY, 7), fill=TEXT_DIM)
        else:
            # 空闲状态：显示表情
            emoji = {"happy": "😊", "worried": "👀", "focused": "🎯",
                     "sleeping": "😴"}.get(mood, "🟢")
            ball_canvas.create_text(cx, cy, text=emoji,
                                    font=(FONT_FAMILY, 20))

        # 未读标记
        if state["unread"] > 0 and not state["expanded"]:
            badge_x = cx + r - 6
            badge_y = cy - r + 6
            badge_r = 9
            ball_canvas.create_oval(badge_x - badge_r, badge_y - badge_r,
                                    badge_x + badge_r, badge_y + badge_r,
                                    fill=RED, outline="")
            count = str(min(state["unread"], 9))
            if state["unread"] > 9:
                count = "9+"
            ball_canvas.create_text(badge_x, badge_y, text=count,
                                    font=(FONT_FAMILY, 8, "bold"), fill="white")

    # 小球点击 → 展开
    ball_canvas.bind("<Button-1>", lambda e: expand())

    # ─── 拖动 ───
    drag = {"x": 0, "y": 0, "dragging": False}

    def on_press(e):
        drag["x"] = e.x
        drag["y"] = e.y
        drag["dragging"] = False

    def on_drag(e):
        dx = abs(e.x - drag["x"])
        dy = abs(e.y - drag["y"])
        if dx > 3 or dy > 3:
            drag["dragging"] = True
        root.geometry(f"+{root.winfo_x() + e.x - drag['x']}+{root.winfo_y() + e.y - drag['y']}")

    def on_release(e):
        if not drag["dragging"] and not state["expanded"]:
            expand()

    ball_canvas.bind("<ButtonPress-1>", on_press)
    ball_canvas.bind("<B1-Motion>", on_drag)
    ball_canvas.bind("<ButtonRelease-1>", on_release)

    # 也为 header 添加拖动
    for w in [header, title_label, status_dot]:
        w.bind("<ButtonPress-1>", on_press)
        w.bind("<B1-Motion>", on_drag)

    # ─── 头部状态更新 ───
    def update_header():
        phase = state["phase"]
        if phase == "working":
            status_dot.itemconfig("dot", fill=GREEN)
            timer_label.config(text=state["time_text"], fg=GREEN)
        elif phase in ("short_break", "long_break"):
            status_dot.itemconfig("dot", fill=BLUE)
            timer_label.config(text=state["time_text"], fg=BLUE)
        elif phase == "paused":
            status_dot.itemconfig("dot", fill=AMBER)
            timer_label.config(text=f"⏸ {state['time_text']}", fg=AMBER)
        else:
            status_dot.itemconfig("dot", fill=GREEN)
            timer_label.config(text="")

    # ─── 命令处理 ───
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

        for cmd in cmds:
            act = cmd.get("cmd", "")

            if act == "ai_message":
                # AI 发来的消息
                text = cmd.get("text", "")
                msg_type = cmd.get("type", "chat")
                ts = cmd.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
                msg = {"role": "assistant", "content": text,
                       "msg_type": msg_type, "timestamp": ts}
                add_message(msg)

                # 有新消息时自动展开，让用户直接看到内容
                if not state["expanded"]:
                    if msg_type == "nudge":
                        state["mood"] = "worried"
                    expand()

            elif act == "update_timer":
                old_phase = state["phase"]
                state["time_text"] = cmd.get("time", "")
                state["phase"] = cmd.get("phase", "idle")
                state["progress"] = cmd.get("progress", 0.0)
                if state["expanded"]:
                    update_header()
                    # 当 phase 变化时更新控制栏按钮
                    if state["phase"] != old_phase:
                        update_focus_bar()

            elif act == "expand":
                expand()

            elif act == "collapse":
                collapse()

            elif act == "set_mood":
                state["mood"] = cmd.get("mood", "normal")

            elif act == "set_mode":
                m = cmd.get("mode", "ai")
                if m in ("ai", "memo", "focus"):
                    set_mode_ui(m)

            elif act == "set_theme":
                theme = cmd.get("theme", "dark")
                _apply_palette(_PALETTE_LIGHT if theme == "light" else _PALETTE_DARK)
                # 更新所有静态组件颜色
                root.configure(bg=BG_DARK)
                ball_canvas.configure(bg=BG_DARK)
                chat_frame.configure(bg=BG_PANEL)
                header.configure(bg=BG_DARK)
                header_left.configure(bg=BG_DARK)
                title_label.configure(fg=TEXT_PRIMARY, bg=BG_DARK)
                timer_label.configure(fg=TEXT_DIM, bg=BG_DARK)
                collapse_btn.configure(fg=TEXT_DIM, bg=BG_DARK)
                new_chat_btn.configure(fg=TEXT_DIM, bg=BG_DARK)
                msg_container.configure(bg=BG_PANEL)
                msg_canvas.configure(bg=BG_PANEL)
                msg_inner.configure(bg=BG_PANEL)
                input_frame.configure(bg=BG_DARK)
                input_entry.configure(bg=BG_INPUT, fg=TEXT_PRIMARY,
                                      insertbackground=TEXT_PRIMARY)
                send_btn.configure(fg=GREEN, bg=BG_INPUT)
                focus_bar.configure(bg=BG_DARK)
                mode_frame.configure(bg=BG_DARK)
                for key, btn in mode_btns.items():
                    is_active = (key == state["mode"])
                    btn.configure(
                        fg=GREEN if is_active else TEXT_DIM,
                        bg=BG_PANEL if is_active else BG_DARK,
                    )
                # 重绘消息和小球
                if state["expanded"]:
                    render_messages()
                    update_focus_bar()
                    update_header()
                draw_ball()

            elif act == "quit":
                try:
                    root.destroy()
                except Exception:
                    pass
                return

        # 动画
        state["pulse_angle"] += 0.08
        if not state["expanded"]:
            draw_ball()

        # 保持置顶
        try:
            root.attributes("-topmost", True)
            root.lift()
        except Exception:
            pass

        root.after(50, process_commands)  # 20fps

    # ─── 初始绘制 ───
    draw_ball()

    # 发送就绪信号
    emit({"type": "ready"})
    log("对话悬浮窗已就绪")

    root.after(100, process_commands)

    try:
        root.mainloop()
    except Exception as e:
        log(f"mainloop 退出: {e}")


# ============================================================
# Headless 后备
# ============================================================

def run_headless():
    log("使用 headless 模式")
    emit({"type": "ready"})
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

def _post_tk_macos_init():
    """
    在 tk.Tk() 成功创建之后，再设置 macOS NSApplication 属性。
    
    关键：必须在 tk.Tk() 之后调用！
    Tk 在初始化时会创建自己的 NSApplication 并设置 Cocoa 颜色子系统。
    如果在 tk.Tk() 之前调用 NSApplication.sharedApplication()，会干扰
    Tk 的 Cocoa 初始化，导致 GetRGBA → TkpGetColor 崩溃 (NSException → SIGABRT)。
    """
    if SYSTEM != "Darwin":
        return
    try:
        from AppKit import NSApplication
        app = NSApplication.sharedApplication()
        # ActivationPolicy 1 = NSApplicationActivationPolicyAccessory
        # 窗口可见，但不在 Dock 显示图标
        app.setActivationPolicy_(1)
        log("macOS NSApplication ActivationPolicy 设置成功 (Accessory)")
    except ImportError:
        log("PyObjC (AppKit) 不可用，tkinter 窗口在 macOS 上可能不可见")
    except Exception as e:
        log(f"macOS NSApplication 设置失败: {e}")


def main():
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    # 清除父进程传递的 tkinter 禁用标记（该标记仅用于保护父进程主线程）
    import os
    os.environ.pop("ATTENTION_OS_NO_TKINTER", None)

    # 支持 --headless 参数（父进程检测到连续崩溃后传入）
    force_headless = "--headless" in sys.argv

    # 支持 --theme 参数，在启动时应用初始主题
    if "--theme" in sys.argv:
        idx = sys.argv.index("--theme")
        if idx + 1 < len(sys.argv):
            init_theme = sys.argv[idx + 1]
            _apply_palette(_PALETTE_LIGHT if init_theme == "light" else _PALETTE_DARK)
            log(f"初始主题: {init_theme}")

    log(f"对话悬浮窗子进程启动 (platform={SYSTEM}, force_headless={force_headless})")

    if force_headless:
        run_headless()
    else:
        # 注意：不要在 tk.Tk() 之前调用 NSApplication.sharedApplication()！
        # macOS NSApplication 属性在 run_tkinter 内部、tk.Tk() 成功后设置
        run_tkinter()


if __name__ == "__main__":
    main()
