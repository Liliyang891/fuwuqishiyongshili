#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
远程指令 GUI 客户端
通过 tkinter 桌面窗口与远程服务器交互
"""

import json
import tkinter as tk
from tkinter import scrolledtext
import urllib.request
import urllib.error
import threading


class RemoteClient:
    """远程指令 GUI 客户端"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("远程指令客户端")
        self.root.geometry("600x500")
        self.root.minsize(400, 350)
        self.root.configure(bg="#1e1e2e")

        self.server_host = tk.StringVar(value="47.250.59.60")
        self.server_port = tk.StringVar(value="8888")
        self.selected_model = None
        self.session_id = ''

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(1000, lambda: threading.Thread(target=self._load_models, daemon=True).start())

    def _build_ui(self):
        """构建界面"""
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_rowconfigure(3, weight=0)
        self.root.grid_columnconfigure(0, weight=1)

        # ---- 顶部：连接设置 ----
        top_frame = tk.Frame(self.root, bg="#1e1e2e")
        top_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))

        tk.Label(
            top_frame, text="服务器地址:", fg="#a0a0c0", bg="#1e1e2e",
            font=("Microsoft YaHei", 9)
        ).pack(side=tk.LEFT)

        host_entry = tk.Entry(
            top_frame, textvariable=self.server_host, width=16,
            bg="#2a2a3e", fg="#ffffff", insertbackground="#ffffff",
            relief=tk.FLAT, font=("Consolas", 10), bd=6
        )
        host_entry.pack(side=tk.LEFT, padx=(6, 4))

        tk.Label(
            top_frame, text="端口:", fg="#a0a0c0", bg="#1e1e2e",
            font=("Microsoft YaHei", 9)
        ).pack(side=tk.LEFT)

        port_entry = tk.Entry(
            top_frame, textvariable=self.server_port, width=6,
            bg="#2a2a3e", fg="#ffffff", insertbackground="#ffffff",
            relief=tk.FLAT, font=("Consolas", 10), bd=6
        )
        port_entry.pack(side=tk.LEFT, padx=(6, 10))

        self.status_label = tk.Label(
            top_frame, text="⚫ 未连接", fg="#8888aa", bg="#1e1e2e",
            font=("Microsoft YaHei", 9)
        )
        self.status_label.pack(side=tk.LEFT, padx=(5, 0))

        # ---- 中间：聊天显示区域 ----
        self.chat_area = scrolledtext.ScrolledText(
            self.root,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#141425",
            fg="#d0d0e0",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            font=("Microsoft YaHei", 10),
            highlightthickness=0,
            selectbackground="#3b82f6",
        )
        self.chat_area.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)

        self.chat_area.tag_config("user", foreground="#60a5fa", font=("Microsoft YaHei", 10, "bold"))
        self.chat_area.tag_config("label_user", foreground="#60a5fa", font=("Microsoft YaHei", 9, "bold"))
        self.chat_area.tag_config("label_server", foreground="#4ade80", font=("Microsoft YaHei", 9, "bold"))
        self.chat_area.tag_config("server", foreground="#d0d0e0", font=("Microsoft YaHei", 10))
        self.chat_area.tag_config("time", foreground="#5a5a7a", font=("Microsoft YaHei", 8))
        self.chat_area.tag_config("error", foreground="#ef4444", font=("Microsoft YaHei", 10))
        self.chat_area.tag_config("info", foreground="#8888aa", font=("Microsoft YaHei", 9))
        self._append_line("💬  欢迎使用远程指令客户端\n", "info")
        self._append_line("输入文字发送消息，AI 大模型智能回复\n\n", "info")

        # ---- 模型选择器 + 清空按钮 ----
        model_frame = tk.Frame(self.root, bg="#1e1e2e")
        model_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 0))

        tk.Label(
            model_frame, text="🤖 模型:", fg="#a0a0c0", bg="#1e1e2e",
            font=("Microsoft YaHei", 9)
        ).pack(side=tk.LEFT)

        self.model_var = tk.StringVar(value="加载中...")
        self.model_menu = tk.OptionMenu(
            model_frame, self.model_var, "加载中...",
            command=self._on_model_change
        )
        self.model_menu.configure(
            bg="#2a2a3e", fg="#ffffff", activebackground="#3b82f6",
            activeforeground="#ffffff", relief=tk.FLAT, font=("Microsoft YaHei", 9),
            highlightthickness=0
        )
        self.model_menu["menu"].configure(
            bg="#1a1a3e", fg="#ffffff", activebackground="#3b82f6",
            activeforeground="#ffffff", font=("Microsoft YaHei", 9)
        )
        self.model_menu.pack(side=tk.LEFT, padx=(6, 0))

        clear_btn = tk.Button(
            model_frame, text="🗑 清空对话", command=self._clear_chat,
            bg="#2a2a3e", fg="#ef4444", activebackground="#3a2a2e",
            activeforeground="#ef4444", relief=tk.FLAT,
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2", bd=0,
        )
        clear_btn.pack(side=tk.RIGHT, padx=(0, 0))

        # ---- 底部：输入和按钮 ----
        bottom_frame = tk.Frame(self.root, bg="#1e1e2e")
        bottom_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(2, 12))

        self.send_btn = tk.Button(
            bottom_frame,
            text="发  送",
            command=self._on_send,
            bg="#3b82f6",
            fg="#ffffff",
            activebackground="#2563eb",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Microsoft YaHei", 10, "bold"),
            padx=18,
            pady=4,
            cursor="hand2",
            bd=0,
        )
        self.send_btn.pack(side=tk.RIGHT, padx=(8, 0))

        self.input_box = tk.Entry(
            bottom_frame,
            bg="#2a2a3e",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            font=("Microsoft YaHei", 11),
            bd=6,
        )
        self.input_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_box.bind("<Return>", self._on_send)
        self.input_box.focus_set()

    def _append_line(self, text, tag=None):
        self.chat_area.configure(state=tk.NORMAL)
        if tag:
            self.chat_area.insert(tk.END, text, tag)
        else:
            self.chat_area.insert(tk.END, text)
        self.chat_area.see(tk.END)
        self.chat_area.configure(state=tk.DISABLED)

    def _get_time_str(self):
        from datetime import datetime
        return datetime.now().strftime("%H:%M:%S")

    def _on_send(self, event=None):
        text = self.input_box.get().strip()
        if not text:
            return

        self.input_box.delete(0, tk.END)
        self.input_box.configure(state=tk.DISABLED)
        self.send_btn.configure(state=tk.DISABLED, text="发送中...")

        time_str = self._get_time_str()
        self._append_line(f"\n👤 你 ", "label_user")
        self._append_line(f"{time_str}\n", "time")
        self._append_line(f"  {text}\n", "user")

        thread = threading.Thread(target=self._do_send, args=(text,), daemon=True)
        thread.start()

    def _do_send(self, text):
        host = self.server_host.get().strip()
        port = self.server_port.get().strip()

        url = f"http://{host}:{port}/api/command"
        payload = {"text": text, "session_id": self.session_id}
        if self.selected_model:
            payload["model"] = self.selected_model
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        try:
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=180)
            result = json.loads(resp.read().decode("utf-8"))
            time_str = self._get_time_str()

            if result.get("session_id"):
                self.session_id = result["session_id"]

            if result.get("success"):
                self.root.after(0, self._show_server_reply, result["reply"], time_str)
                self.root.after(0, self._set_status, "🟢 在线", "#4ade80")
            else:
                self.root.after(0, self._show_error, "服务器返回失败")
                self.root.after(0, self._set_status, "🔴 异常", "#ef4444")

        except urllib.error.URLError:
            self.root.after(0, self._show_error, f"无法连接到服务器 {host}:{port}")
            self.root.after(0, self._set_status, "🔴 离线", "#ef4444")
        except Exception as e:
            self.root.after(0, self._show_error, f"错误: {e}")
            self.root.after(0, self._set_status, "🔴 异常", "#ef4444")
        finally:
            self.root.after(0, self._enable_input)

    def _show_server_reply(self, reply_text, time_str):
        self._append_line(f"\n🖥️ 服务器 ", "label_server")
        self._append_line(f"{time_str}\n", "time")
        self._append_line(f"  {reply_text}\n", "server")

    def _show_error(self, msg):
        time_str = self._get_time_str()
        self._append_line(f"\n⚠️ {msg} ", "error")
        self._append_line(f"{time_str}\n", "time")

    def _set_status(self, text, color):
        self.status_label.configure(text=text, fg=color)

    def _enable_input(self):
        self.input_box.configure(state=tk.NORMAL)
        self.send_btn.configure(state=tk.NORMAL, text="发  送")
        self.input_box.focus_set()

    def _clear_chat(self):
        """清空对话历史"""
        host = self.server_host.get().strip()
        port = self.server_port.get().strip()

        def do_clear():
            try:
                url = f"http://{host}:{port}/api/clear"
                data = json.dumps({"session_id": self.session_id}).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass

        threading.Thread(target=do_clear, daemon=True).start()

        self.chat_area.configure(state=tk.NORMAL)
        self.chat_area.delete("1.0", tk.END)
        self.chat_area.configure(state=tk.DISABLED)
        self._append_line("💬  对话已清空，输入消息开始新对话\n\n", "info")

    def _on_close(self):
        self.root.destroy()

    def run(self):
        self.root.after(500, self._check_connection)
        self.root.mainloop()

    def _check_connection(self):
        def do_check():
            host = self.server_host.get().strip()
            port = self.server_port.get().strip()
            url = f"http://{host}:{port}/api/status"
            try:
                req = urllib.request.Request(url, method="GET")
                urllib.request.urlopen(req, timeout=3)
                self.root.after(0, self._set_status, "🟢 在线", "#4ade80")
                if self.selected_model is None:
                    self._load_models()
            except Exception:
                self.root.after(0, self._set_status, "🔴 离线", "#ef4444")
            finally:
                self.root.after(15000, self._check_connection)

        threading.Thread(target=do_check, daemon=True).start()

    def _load_models(self):
        """从服务器加载可用模型列表（在后台线程中执行）"""
        host = self.server_host.get().strip()
        port = self.server_port.get().strip()
        url = f"http://{host}:{port}/api/models"

        try:
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get('models', [])
            if models:
                self.root.after(0, self._update_model_menu, models)
            else:
                self.root.after(0, self.model_var.set, "无模型")
        except Exception:
            self.root.after(0, self.model_var.set, "获取失败")

    def _update_model_menu(self, models):
        menu = self.model_menu["menu"]
        menu.delete(0, "end")
        for m in models:
            menu.add_command(
                label=m,
                command=tk._setit(self.model_var, m, self._on_model_change)
            )
        if models:
            self.model_var.set(models[0])
            self.selected_model = models[0]

    def _on_model_change(self, value):
        if value and value not in ("加载中...", "无模型", "获取失败"):
            self.selected_model = value


def main():
    app = RemoteClient()
    app.run()


if __name__ == "__main__":
    main()
