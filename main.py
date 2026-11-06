# -*- coding: utf-8 -*-
"""
WiFi 遥控小车 — Android 版（Kivy）
  按住按钮 = 持续发送指令，松手 = 停止
  实时显示 温度 / 湿度 / 光照 曲线，支持导出 CSV
  与桌面版 遥控器.py 通信协议完全一致（1 字节指令 + 文本数据行）
"""
import collections
import csv
import os
import re
import socket
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex

# ---------------- 指令定义 ----------------
CMD_STOP        = b"\x00"
CMD_FORWARD     = b"\x01"
CMD_BACKWARD    = b"\x02"
CMD_LEFT        = b"\x03"
CMD_RIGHT       = b"\x04"
CMD_SLIDE_LEFT  = b"\x05"
CMD_SLIDE_RIGHT = b"\x06"
CMD_ARC_LEFT    = b"\x07"
CMD_ARC_RIGHT   = b"\x08"
CMD_LINEFOLLOW  = b"\x09"

SEND_INTERVAL = 0.04

DEFAULT_IP = "10.51.37.140"
DEFAULT_PORT = "8080"

C_TEMP = "#e74c3c"
C_HUMI = "#3498db"
C_LUX = "#27ae60"

# 中文字体候选（安卓系统字体优先，其次 Windows / macOS，便于电脑上调试）
CJK_FONTS = (
    "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/NotoSansSC-Regular.otf",
    "/system/fonts/DroidSansFallback.ttf",
    "/system/fonts/DroidSansChinese.ttf",
    "/system/fonts/NotoSansCJKsc-Regular.otf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)


def setup_font():
    """把默认字体换成支持中文的字体，否则中文会显示成方块"""
    for path in CJK_FONTS:
        if os.path.exists(path):
            try:
                LabelBase.register(name="Roboto", fn_regular=path, fn_bold=path)
                return path
            except Exception:
                continue
    return None


# ---------------- 网络控制 ----------------
class CarController:
    """TCP 连接小车：指令发送线程 + 数据接收线程（数据经队列交给主线程）"""

    def __init__(self):
        self.sock = None
        self.connected = False
        self.current_cmd = None
        self.running = False
        self.lock = threading.Lock()
        self.rx_queue = collections.deque()
        self._recv_thread = None

    def connect(self, ip, port=8080):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(4)
            sock.connect((ip, port))
            sock.settimeout(None)
            self.sock = sock
            self.connected = True          # 必须先置位，再启动接收线程
            self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
            self._recv_thread.start()
            return True, ""
        except Exception as e:
            self.connected = False
            self.sock = None
            return False, str(e)

    def disconnect(self):
        self.connected = False
        self.current_cmd = None
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _write(self, data):
        if self.connected and self.sock:
            try:
                self.sock.send(data)
                return True
            except Exception:
                self.connected = False
        return False

    def send(self, cmd):
        return self._write(cmd)

    def start_loop(self, cmd):
        with self.lock:
            self.current_cmd = cmd
            if not self.running:
                self.running = True
                threading.Thread(target=self._send_loop, daemon=True).start()

    def _send_loop(self):
        while True:
            with self.lock:
                cmd = self.current_cmd
                if cmd is None:
                    self.running = False
                    break
            self._write(cmd)
            time.sleep(SEND_INTERVAL)

    def stop(self):
        with self.lock:
            self.current_cmd = None
        self._write(CMD_STOP)

    def _recv_loop(self):
        buf = b""
        while self.connected:
            try:
                data = self.sock.recv(1024)
                if not data:
                    break
                buf += data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode(errors="ignore").strip()
                    if line:
                        self.rx_queue.append(line)
                if len(buf) > 4096:            # 对端不发换行符时的兜底
                    line = buf.decode(errors="ignore").strip()
                    buf = b""
                    if line:
                        self.rx_queue.append(line)
            except OSError:
                break
            except Exception:
                break
        self.connected = False

    def drain(self):
        """取出所有待处理的数据行（只在主线程调用）"""
        out = []
        while self.rx_queue:
            try:
                out.append(self.rx_queue.popleft())
            except IndexError:
                break
        return out


# ---------------- 组件 ----------------
class Card(BoxLayout):
    """单个传感器数值卡片"""

    def __init__(self, name, unit, color, **kw):
        super().__init__(orientation="vertical", padding=[dp(8), dp(4)],
                         spacing=dp(0), **kw)
        self.color = color
        self.add_widget(Label(text=name, color=get_color_from_hex("#8b95a1"),
                              font_size=sp(13), size_hint_y=0.3, halign="center"))
        self.val = Label(text="--", color=get_color_from_hex(color),
                         font_size=sp(30), bold=True, size_hint_y=0.5)
        self.unit = Label(text=unit, color=get_color_from_hex("#b0b9c3"),
                          font_size=sp(11), size_hint_y=0.2)
        self.add_widget(self.val)
        self.add_widget(self.unit)
        self.stat = Label(text="↓-- ↑-- 均--", color=get_color_from_hex("#b0b9c3"),
                          font_size=sp(11), size_hint_y=0.2)
        self.add_widget(self.stat)

    def update(self, value, stats, digits):
        if value is None:
            self.val.text = "--"
            self.stat.text = "↓-- ↑-- 均--"
            return
        fmt = "%." + str(digits) + "f"
        self.val.text = fmt % value
        if stats:
            lo, hi, avg = stats
            self.stat.text = "↓%s ↑%s 均%s" % (fmt % lo, fmt % hi, fmt % avg)


class Chart(Widget):
    """三条自适应曲线（各自独立缩放）"""

    def __init__(self, maxlen=120, **kw):
        super().__init__(**kw)
        self.series = [collections.deque(maxlen=maxlen) for _ in range(3)]
        with self.canvas:
            Color(1, 1, 1, 1)
            self.bg = Rectangle(pos=self.pos, size=self.size)
            self.lines = []
            for c in (C_TEMP, C_HUMI, C_LUX):
                Color(*get_color_from_hex(c))
                self.lines.append(Line(points=[], width=dp(1.4)))
        self.bind(pos=self._resize, size=self._resize)

    def _resize(self, *_):
        self.bg.pos, self.bg.size = self.pos, self.size
        self.redraw()

    def add(self, values):
        for i, v in enumerate(values):
            self.series[i].append(v)

    def clear(self):
        for s in self.series:
            s.clear()
        self.redraw()

    def redraw(self):
        w, h = self.size
        if w < dp(20) or h < dp(20):
            return
        for i, dq in enumerate(self.series):
            pts = []
            n = len(dq)
            if n >= 2:
                lo, hi = min(dq), max(dq)
                if hi - lo < 1e-6:
                    hi = lo + 1.0
                for j, v in enumerate(dq):
                    x = self.x + (j / (n - 1)) * w
                    y = self.y + dp(2) + (v - lo) / (hi - lo) * (h - dp(4))
                    pts += [x, y]
            self.lines[i].points = pts


class HoldButton(Button):
    """按下持续发送、松开停止的按钮"""

    def __init__(self, cmd, on_press_cmd, on_release_cb, **kw):
        super().__init__(**kw)
        self.cmd = cmd
        self._press_cb = on_press_cmd
        self._release_cb = on_release_cb
        self.bind(on_press=self._down, on_release=self._up)

    def _down(self, *_):
        self._press_cb(self.cmd)

    def _up(self, *_):
        self._release_cb()


class DPadButton(HoldButton):
    pass


# ---------------- 主界面 ----------------
class MainLayout(BoxLayout):
    def __init__(self, app, **kw):
        super().__init__(orientation="vertical", padding=dp(8), spacing=dp(6), **kw)
        self.app = app
        self.car = app.car
        self.monitor_data = collections.deque(maxlen=300)
        self.rows = []
        self.t0 = None
        self.pkt_count = 0
        self.raw_count = 0
        self.line_on = False
        self._build()

    # ---------- UI ----------
    def _build(self):
        # ① 连接栏
        bar = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(6))
        self.ip_input = TextInput(text=DEFAULT_IP, multiline=False, font_size=sp(15),
                                  size_hint_x=0.46)
        self.port_input = TextInput(text=DEFAULT_PORT, multiline=False, font_size=sp(15),
                                    size_hint_x=0.2)
        self.btn_conn = Button(text="连接", font_size=sp(15), size_hint_x=0.34,
                               background_normal="", background_color=get_color_from_hex("#1e88e5"))
        self.btn_conn.bind(on_release=lambda *_: self.app.toggle_connect())
        bar.add_widget(self.ip_input)
        bar.add_widget(self.port_input)
        bar.add_widget(self.btn_conn)
        self.add_widget(bar)

        self.lbl_status = Label(text="● 未连接", size_hint_y=None, height=dp(22),
                                font_size=sp(13), color=get_color_from_hex("#e74c3c"))
        self.add_widget(self.lbl_status)

        # ② 数值卡片
        cards = BoxLayout(size_hint_y=None, height=dp(96), spacing=dp(6))
        self.card_t = Card("温度", "°C", C_TEMP)
        self.card_h = Card("湿度", "%RH", C_HUMI)
        self.card_l = Card("光照", "lx", C_LUX)
        for c in (self.card_t, self.card_h, self.card_l):
            cards.add_widget(c)
        self.add_widget(cards)

        # ③ 曲线
        self.chart = Chart(size_hint_y=None, height=dp(150))
        self.add_widget(self.chart)

        # ④ 工具条
        tools = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(6))
        self.btn_clear = Button(text="清空数据", font_size=sp(13), background_normal="",
                                background_color=get_color_from_hex("#546e7a"))
        self.btn_clear.bind(on_release=lambda *_: self.clear_data())
        self.btn_export = Button(text="导出CSV", font_size=sp(13), background_normal="",
                                 background_color=get_color_from_hex("#546e7a"))
        self.btn_export.bind(on_release=lambda *_: self.export_csv())
        self.lbl_raw = Label(text="--", font_size=sp(11), halign="left", valign="middle",
                             color=get_color_from_hex("#9aa5b1"), shorten=True,
                             shorten_from="right")
        self.lbl_raw.bind(size=lambda w, *_: setattr(w, "text_size", (w.width, None)))
        tools.add_widget(self.btn_clear)
        tools.add_widget(self.btn_export)
        tools.add_widget(self.lbl_raw)
        self.add_widget(tools)

        # ⑤ 巡线指示灯
        dots = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(4))
        dots.add_widget(Label(text="巡线", font_size=sp(11),
                              color=get_color_from_hex("#8b95a1"), size_hint_x=0.2))
        self.line_dots = []
        self.line_colors = []
        for i in range(5):
            lbl = Label(text="L%d" % (i + 1), font_size=sp(10),
                        color=get_color_from_hex("#b0b9c3"))
            with lbl.canvas.before:
                bg_color = Color(*get_color_from_hex("#e8edf2"))
                rect = Rectangle(pos=lbl.pos, size=lbl.size)
            lbl.bind(pos=lambda w, *_: setattr(rect, "pos", w.pos),
                     size=lambda w, *_: setattr(rect, "size", w.size))
            dots.add_widget(lbl)
            self.line_dots.append(lbl)
            self.line_colors.append(bg_color)
        self.add_widget(dots)

        # ⑥ 方向键盘
        pad = GridLayout(cols=3, spacing=dp(6), size_hint_y=1)
        layout = [
            (("↰ 左弧", CMD_ARC_LEFT, "#00897b"), ("▲ 前进", CMD_FORWARD, "#43a047"), ("右弧 ↱", CMD_ARC_RIGHT, "#00897b")),
            (("⟲ 左转", CMD_LEFT, "#1e88e5"), ("■ 停", None, "#e53935"), ("⟳ 右转", CMD_RIGHT, "#1e88e5")),
            (("⇦ 左移", CMD_SLIDE_LEFT, "#8e24aa"), ("▼ 后退", CMD_BACKWARD, "#fb8c00"), ("右移 ⇨", CMD_SLIDE_RIGHT, "#8e24aa")),
        ]
        for row in layout:
            for text, cmd, color in row:
                if cmd is None:
                    btn = Button(text=text, font_size=sp(14), bold=True,
                                 background_normal="",
                                 background_color=get_color_from_hex(color))
                    btn.bind(on_release=lambda *_: self.car.stop())
                else:
                    btn = HoldButton(cmd, self.car.start_loop, self.car.stop,
                                     text=text, font_size=sp(14), bold=True,
                                     background_normal="",
                                     background_color=get_color_from_hex(color))
                pad.add_widget(btn)
        self.add_widget(pad)

        self.btn_line = Button(text="◎ 巡线", size_hint_y=None, height=dp(44),
                               font_size=sp(15), bold=True, background_normal="",
                               background_color=get_color_from_hex("#6d4c41"))
        self.btn_line.bind(on_release=lambda *_: self.toggle_line())
        self.add_widget(self.btn_line)

    # ---------- 数据 ----------
    def on_line(self, line):
        self.raw_count += 1
        self.lbl_raw.text = "原始数据 %s" % line[:40]
        m = re.search(r"T\s*[:=]\s*(-?[\d.]+)[\s,;，；]+H\s*[:=]\s*(-?[\d.]+)"
                      r"[\s,;，；]+L\s*[:=]\s*(-?[\d.]+)", line, re.IGNORECASE)
        if m:
            t, h, lux = float(m.group(1)), float(m.group(2)), float(m.group(3))
            now = time.time()
            if self.t0 is None:
                self.t0 = now
            self.monitor_data.append((t, h, lux))
            self.rows.append((now - self.t0, t, h, lux))
            self.chart.add((t, h, lux))
            self.pkt_count += 1
        m2 = re.search(r"S\s*[:=]\s*([01]{5})", line)
        if m2:
            self.update_dots(m2.group(1))

    def update_dots(self, bits):
        for i, ch in enumerate(bits):
            on = ch == "1"
            self.line_dots[i].color = get_color_from_hex("#ffffff" if on else "#b0b9c3")
            self.line_colors[i].rgba = get_color_from_hex("#2e7d32" if on else "#e8edf2")

    def refresh(self):
        last = self.monitor_data[-1] if self.monitor_data else None
        for idx, card, digits in ((0, self.card_t, 1), (1, self.card_h, 1), (2, self.card_l, 0)):
            if last is None:
                card.update(None, None, digits)
                continue
            series = [d[idx] for d in self.monitor_data]
            stats = (min(series), max(series), sum(series) / len(series))
            card.update(last[idx], stats, digits)
        self.chart.redraw()

    def clear_data(self):
        self.monitor_data.clear()
        self.rows.clear()
        self.t0 = None
        self.pkt_count = 0
        self.raw_count = 0
        self.lbl_raw.text = "--"
        self.chart.clear()
        self.refresh()

    def export_csv(self):
        if not self.rows:
            self.toast("还没有数据", "还没有收到小车数据，无法导出。")
            return
        name = time.strftime("sensor_%Y%m%d_%H%M%S.csv")
        folder = self.app.get_writable_dir()
        path = os.path.join(folder, name)
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["时间(s)", "温度(°C)", "湿度(%RH)", "光照(lx)"])
                for r in self.rows:
                    w.writerow(["%.2f" % r[0], "%.1f" % r[1], "%.1f" % r[2], "%.0f" % r[3]])
        except Exception as e:
            self.toast("导出失败", str(e))
            return
        self.toast("导出成功", "已导出 %d 条到：\n%s" % (len(self.rows), path))

    def toast(self, title, msg):
        content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        content.add_widget(Label(text=msg, halign="left", valign="middle"))
        btn = Button(text="确定", size_hint_y=None, height=dp(40))
        content.add_widget(btn)
        popup = Popup(title=title, content=content, size_hint=(0.9, 0.4))
        btn.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def toggle_line(self):
        if self.line_on:
            self.car.stop()
            self.line_on = False
            self.btn_line.text = "◎ 巡线"
            self.btn_line.background_color = get_color_from_hex("#6d4c41")
        else:
            self.car.send(CMD_LINEFOLLOW)
            self.line_on = True
            self.btn_line.text = "● 巡线中"
            self.btn_line.background_color = get_color_from_hex("#2e7d32")


class CarApp(App):
    title = "小车遥控"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.car = CarController()
        self.layout = None

    def build(self):
        setup_font()
        Window.clearcolor = get_color_from_hex("#f4f6f8")
        self.layout = MainLayout(self)
        Clock.schedule_interval(self._tick, 0.5)
        return self.layout

    def _tick(self, _dt):
        for line in self.car.drain():
            self.layout.on_line(line)
        self.layout.refresh()
        self.sync_status()

    def sync_status(self):
        if self.car.connected:
            self.layout.lbl_status.text = "● 已连接 %s:%d" % self._target
            self.layout.lbl_status.color = get_color_from_hex("#2e7d32")
            self.layout.btn_conn.text = "断开"
        else:
            if self.layout.btn_conn.text == "断开":
                self.layout.lbl_status.text = "● 连接已断开"
            else:
                self.layout.lbl_status.text = "● 未连接"
            self.layout.lbl_status.color = get_color_from_hex("#e74c3c")
            self.layout.btn_conn.text = "连接"

    def toggle_connect(self):
        if self.car.connected:
            self.car.disconnect()
            self.sync_status()
            return
        ip = self.layout.ip_input.text.strip()
        try:
            port = int(self.layout.port_input.text.strip())
        except ValueError:
            self.layout.toast("端口错误", "端口号必须是数字，例如 8080")
            return
        ok, err = self.car.connect(ip, port)
        if ok:
            self._target = (ip, port)
        else:
            self.layout.toast("连接失败", "无法连接 %s:%d\n%s" % (ip, port, err))
        self.sync_status()

    def get_writable_dir(self):
        """优先写到手机「下载」目录，失败则用应用私有目录"""
        for p in ("/storage/emulated/0/Download", "/sdcard/Download"):
            if os.path.isdir(p) and os.access(p, os.W_OK):
                return p
        return self.user_data_dir

    def on_stop(self):
        self.car.stop()
        self.car.disconnect()

    def on_pause(self):
        """切到后台时先停车，并返回 False 让应用真正退出（触达 on_stop 断开连接）"""
        self.car.stop()
        return False


if __name__ == "__main__":
    CarApp().run()
