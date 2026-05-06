#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
远程指令 GUI 客户端 — AI Agent 版
支持：文字消息回复、多模型切换、文件上传、语音输入、对话历史
"""

import json
import os
import base64
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox
import urllib.request
import urllib.error
import threading
import subprocess
import tempfile

# 尝试导入语音识别库
try:
    import pyaudio
    import wave
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

try:
    import speech_recognition as sr
    HAS_SPEECH_RECOGNITION = True
except ImportError:
    HAS_SPEECH_RECOGNITION = False


class RemoteClient:
    """远程指令 GUI 客户端"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Agent 客户端")
        self.root.geometry("650x580")
        self.root.minsize(450, 400)
        self.root.configure(bg="#1e1e2e")

        self.server_host = tk.StringVar(value="47.250.59.60")
        self.server_port = tk.StringVar(value="8888")
        self.selected_model = None
        self.session_id = ''
        self.pending_files = []  # 待上传文件路径列表
        self.is_recording = False
        self.recorder = None  # pyaudio 录音线程

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

        # ---- 文件预览标签 ----
        self.file_preview_label = tk.Label(
            self.root, text="", fg="#60a5fa", bg="#1e1e2e",
            font=("Microsoft YaHei", 9), anchor=tk.W
        )
        self.file_preview_label.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 0))

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
        self.chat_area.tag_config("tool", foreground="#fbbf24", font=("Microsoft YaHei", 9))
        self._append_line("💬  欢迎使用 AI Agent 客户端\n", "info")
        self._append_line("📁 支持文件/数据库操作 · 📎 文件上传 · 🎤 语音输入\n\n", "info")

        # ---- 模型选择器 + 清空按钮 ----
        model_frame = tk.Frame(self.root, bg="#1e1e2e")
        model_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(4, 0))

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
            model_frame, text="🗑 清空", command=self._clear_chat,
            bg="#2a2a3e", fg="#ef4444", activebackground="#3a2a2e",
            activeforeground="#ef4444", relief=tk.FLAT,
            font=("Microsoft YaHei", 9), padx=8, cursor="hand2", bd=0,
        )
        clear_btn.pack(side=tk.RIGHT, padx=(0, 0))

        # ---- 底部：输入和按钮 ----
        bottom_frame = tk.Frame(self.root, bg="#1e1e2e")
        bottom_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(2, 12))

        # 发送按钮（右侧）
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

        # 语音按钮
        self.voice_btn = tk.Button(
            bottom_frame,
            text="🎤",
            command=self._toggle_voice,
            bg="#2a2a3e",
            fg="#a0a0c0",
            activebackground="#3a2a2e",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI Emoji", 14),
            padx=8,
            pady=2,
            cursor="hand2",
            bd=0,
        )
        self.voice_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # 上传按钮
        self.upload_btn = tk.Button(
            bottom_frame,
            text="📎",
            command=self._select_files,
            bg="#2a2a3e",
            fg="#a0a0c0",
            activebackground="#2a3a2e",
            activeforeground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI Emoji", 14),
            padx=8,
            pady=2,
            cursor="hand2",
            bd=0,
        )
        self.upload_btn.pack(side=tk.RIGHT, padx=(4, 0))

        # 输入框
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

    # ---- 文件选择 ----

    def _select_files(self):
        """打开文件选择对话框"""
        filenames = filedialog.askopenfilenames(
            title="选择要上传的文件",
            filetypes=[("所有文件", "*.*")],
        )
        if filenames:
            self.pending_files = list(filenames)
            count = len(filenames)
            names = ", ".join(os.path.basename(f) for f in filenames[:3])
            if count > 3:
                names += f" 等 {count} 个文件"
            self.file_preview_label.configure(text=f"📎 已选择: {names}")
            # 如果输入框为空，自动填入提示
            if not self.input_box.get().strip():
                self.input_box.insert(0, f"请帮我处理上传的文件: {names}")
                self.input_box.focus_set()

    def _upload_files(self):
        """上传待发送的文件，返回路径列表"""
        if not self.pending_files:
            return []

        host = self.server_host.get().strip()
        port = self.server_port.get().strip()
        uploaded_paths = []

        for filepath in self.pending_files:
            try:
                filename = os.path.basename(filepath)
                # 读取文件内容
                with open(filepath, 'rb') as f:
                    file_data = f.read()

                # 使用 JSON base64 方式上传
                payload = {
                    "filename": filename,
                    "data": base64.b64encode(file_data).decode('ascii'),
                }
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

                url = f"http://{host}:{port}/api/upload"
                req = urllib.request.Request(
                    url, data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                resp = urllib.request.urlopen(req, timeout=60)
                result = json.loads(resp.read().decode("utf-8"))

                if result.get("success"):
                    uploaded_paths.append(result.get("path", filename))
                else:
                    uploaded_paths.append(f"[上传失败: {result.get('error', '未知错误')}]")
            except Exception as e:
                uploaded_paths.append(f"[上传失败: {e}]")

        self.pending_files = []
        self.file_preview_label.configure(text="")
        return uploaded_paths

    # ---- 语音输入 ----
    def _toggle_voice(self):
        """切换语音输入状态"""
        if self.is_recording:
            self._stop_recording()
            return

        if HAS_SPEECH_RECOGNITION:
            self._start_recording_sr()
        elif HAS_PYAUDIO:
            self._start_recording_pyaudio()
        else:
            messagebox.showinfo(
                "语音输入",
                "语音输入需要安装以下库之一：\n\n"
                "1. SpeechRecognition + PyAudio (推荐)\n"
                "   命令: pip install SpeechRecognition pyaudio\n\n"
                "2. 仅 PyAudio (录制后手动处理)\n"
                "   命令: pip install pyaudio"
            )

    def _start_recording_sr(self):
        """使用 speech_recognition 库录音并识别"""
        self.is_recording = True
        self.voice_btn.configure(text="⏹", fg="#ef4444", bg="#3a2a2e")
        self.input_box.delete(0, tk.END)
        self.input_box.insert(0, "🎤 正在聆听...")
        self.input_box.configure(state=tk.DISABLED)

        def do_record():
            try:
                r = sr.Recognizer()
                with sr.Microphone() as source:
                    r.adjust_for_ambient_noise(source, duration=0.5)
                    audio = r.listen(source, timeout=10, phrase_time_limit=15)

                # 先尝试在线识别 (Google)
                text = ""
                try:
                    text = r.recognize_google(audio, language='zh-CN')
                except Exception:
                    # 尝试上传到服务器识别
                    try:
                        audio_data = audio.get_wav_data()
                        text = self._send_speech_to_server(audio_data)
                    except Exception:
                        pass

                self.root.after(0, self._on_speech_result, text)
            except Exception as e:
                self.root.after(0, self._on_speech_error, str(e))

        threading.Thread(target=do_record, daemon=True).start()

    def _start_recording_pyaudio(self):
        """使用 pyaudio 录音"""
        self.is_recording = True
        self.voice_btn.configure(text="⏹", fg="#ef4444", bg="#3a2a2e")
        self.input_box.delete(0, tk.END)
        self.input_box.insert(0, "🎤 正在录音...")
        self.input_box.configure(state=tk.DISABLED)

        frames = []
        is_done = threading.Event()
        stream = None
        p = None

        def record_loop():
            nonlocal stream, p
            try:
                p = pyaudio.PyAudio()
                stream = p.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=16000,
                    input=True,
                    frames_per_buffer=1024,
                )
                while not is_done.is_set():
                    data = stream.read(1024, exception_on_overflow=False)
                    frames.append(data)
            except Exception as e:
                self.root.after(0, self._on_speech_error, str(e))
                return

        self.recorder = threading.Thread(target=record_loop, daemon=True)
        self.recorder.start()

        # 8 秒后自动停止
        self.root.after(8000, self._stop_recording)

        # 保存 stream/p 引用以便停止
        self._pyaudio_stream = stream
        self._pyaudio_p = p
        self._pyaudio_frames = frames
        self._pyaudio_done = is_done

    def _stop_recording(self):
        """停止录音"""
        if not self.is_recording:
            return

        self.is_recording = False
        self.voice_btn.configure(text="🎤", fg="#a0a0c0", bg="#2a2a3e")

        if HAS_SPEECH_RECOGNITION and hasattr(self, '_sr_audio'):
            pass  # 由线程自行完成
        elif HAS_PYAUDIO and hasattr(self, '_pyaudio_done'):
            self._pyaudio_done.set()
            if self.recorder:
                self.recorder.join(timeout=1)

            # 保存录音文件并上传
            try:
                if self._pyaudio_stream:
                    self._pyaudio_stream.stop_stream()
                    self._pyaudio_stream.close()
                if self._pyaudio_p:
                    self._pyaudio_p.terminate()

                frames = getattr(self, '_pyaudio_frames', [])
                if frames:
                    audio_data = b''.join(frames)
                    # 保存为临时文件
                    tmp_path = os.path.join(tempfile.gettempdir(), f"voice_{os.getpid()}.wav")
                    wf = wave.open(tmp_path, 'wb')
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(audio_data)
                    wf.close()

                    # 尝试上传到服务器
                    text = self._send_speech_to_server(audio_data)
                    self.root.after(0, self._on_speech_result, text)
            except Exception as e:
                self.root.after(0, self._on_speech_error, str(e))

    def _send_speech_to_server(self, audio_data):
        """将音频数据发送到服务器进行语音识别"""
        host = self.server_host.get().strip()
        port = self.server_port.get().strip()
        url = f"http://{host}:{port}/api/speech"

        payload = {
            "audio": base64.b64encode(audio_data).decode('ascii'),
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=30)
        result = json.loads(resp.read().decode("utf-8"))
        return result.get("text", "")

    def _on_speech_result(self, text):
        """语音识别结果回调"""
        self.input_box.configure(state=tk.NORMAL)
        self.input_box.delete(0, tk.END)
        if text:
            self.input_box.insert(0, text)
        else:
            self.input_box.insert(0, "")
            self.input_box.focus_set()
            self._append_line("🎤 语音识别未获取到文字\n", "info")
        # 自动发送
        if text and text.strip():
            self._on_send()

    def _on_speech_error(self, error_msg):
        """语音识别错误回调"""
        self.input_box.configure(state=tk.NORMAL)
        self.input_box.delete(0, tk.END)
        self.input_box.insert(0, "")
        self.input_box.focus_set()
        self._append_line(f"🎤 语音识别错误: {error_msg}\n", "error")

    # ---- 发送消息 ----
    def _on_send(self, event=None):
        text = self.input_box.get().strip()
        if not text and not self.pending_files:
            return

        self.input_box.delete(0, tk.END)
        self.input_box.configure(state=tk.DISABLED)
        self.send_btn.configure(state=tk.DISABLED, text="发送中...")
        self.upload_btn.configure(state=tk.DISABLED)
        self.voice_btn.configure(state=tk.DISABLED)

        time_str = self._get_time_str()
        self._append_line(f"\n👤 你 ", "label_user")
        self._append_line(f"{time_str}\n", "time")
        self._append_line(f"  {text or '(文件上传请求)'}\n", "user")

        thread = threading.Thread(target=self._do_send, args=(text,), daemon=True)
        thread.start()

    def _do_send(self, text):
        host = self.server_host.get().strip()
        port = self.server_port.get().strip()

        # 先上传文件
        uploaded_paths = self._upload_files()
        full_text = text
        if uploaded_paths:
            file_info = "\n\n[已上传文件: " + ", ".join(uploaded_paths) + "]"
            full_text = (text or "请帮我看看上传的文件") + file_info
            # 显示上传结果
            self.root.after(0, self._append_line, f"📎 文件已上传: {', '.join(uploaded_paths)}\n", "tool")

        url = f"http://{host}:{port}/api/command"
        payload = {"text": full_text, "session_id": self.session_id}
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
                # 显示工具调用信息
                tool_calls = result.get("tool_calls", [])
                if tool_calls:
                    tool_names = [tc.get("tool", "?" ) for tc in tool_calls]
                    self.root.after(0, self._append_line, f"🔧 执行了 {len(tool_calls)} 个工具: {', '.join(tool_names)}\n", "tool")

                self.root.after(0, self._show_server_reply, result["reply"], time_str)
                self.root.after(0, self._set_status, "🟢 在线", "#4ade80")
            else:
                self.root.after(0, self._show_error, "服务器返回失败")
                self.root.after(0, self._set_status, "🔴 异常", "#ef4444")

        except urllib.error.URLError:
            self.root.after(0, self._show_error, f"无法连接到服务器 {host}:{port}")
            self.root.after(0, self._set_status, "🔴 离线", "#ef4444")
        except Exception as e:
            self.root.after(0, self._show_error, f"错误: {type(e).__name__}: {e}")
            self.root.after(0, self._set_status, "🔴 异常", "#ef4444")
        finally:
            self.root.after(0, self._enable_input)

    def _show_server_reply(self, reply_text, time_str):
        self._append_line(f"\n🖥️ AI ", "label_server")
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
        self.upload_btn.configure(state=tk.NORMAL)
        self.voice_btn.configure(state=tk.NORMAL)
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
        self._append_line("💬  对话已清空，输入消息开始新对话\n", "info")

    def _on_close(self):
        """关闭窗口时停止录音"""
        if self.is_recording:
            self._stop_recording()
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