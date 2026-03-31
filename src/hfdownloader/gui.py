from __future__ import annotations

import os
import re
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .hf_downloader import DownloadCancelledError, download_model, suggest_models


INVALID_FOLDER_CHARS = r'[<>:"/\\|?*]+'


def format_size(byte_count):
    value = float(max(byte_count, 0))
    units = ["B", "KB", "MB", "GB", "TB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{byte_count} B"


def format_percent(downloaded, total):
    if total <= 0:
        return "0.0%"
    return f"{(downloaded / total) * 100:.1f}%"


class HFDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("HFDownloader")
        self.root.geometry("860x660")
        self.root.minsize(820, 600)

        self.model_var = tk.StringVar()
        self.destination_var = tk.StringVar(
            value=str((Path.home() / "Downloads" / "HF Models").resolve())
        )
        self.token_var = tk.StringVar()
        self.revision_var = tk.StringVar()
        self.target_var = tk.StringVar(value="Target folder will appear here.")
        self.status_var = tk.StringVar(value="Enter a model id, choose a base folder, then click Download.")
        self.progress_var = tk.StringVar(value="No download running.")
        self.percent_var = tk.StringVar(value="0.0%")
        self.auto_open_var = tk.BooleanVar(value=True)

        self.result_queue = Queue()
        self.last_download_path = None
        self.active_target = None
        self.total_bytes = 0
        self.downloaded_bytes = 0
        self.expected_file_count = 0
        self.current_file = ""
        self.is_downloading = False
        self.is_paused = False
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.pause_event.set()
        self.download_thread = None

        self._build_ui()
        self.model_var.trace_add("write", self._refresh_target_preview)
        self.destination_var.trace_add("write", self._refresh_target_preview)
        self._refresh_target_preview()
        self.root.after(200, self._poll_queue)

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(12, weight=1)

        ttk.Label(frame, text="Model ID").grid(row=0, column=0, sticky="w", pady=(0, 10))
        ttk.Entry(frame, textvariable=self.model_var, width=66).grid(
            row=0, column=1, columnspan=3, sticky="ew", pady=(0, 10)
        )

        ttk.Label(frame, text="Base Folder").grid(row=1, column=0, sticky="w", pady=(0, 10))
        ttk.Entry(frame, textvariable=self.destination_var, width=66).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(0, 10)
        )
        ttk.Button(frame, text="Browse", command=self._choose_destination).grid(
            row=1, column=3, padx=(10, 0), pady=(0, 10)
        )

        ttk.Label(frame, text="Target Folder").grid(row=2, column=0, sticky="nw", pady=(0, 10))
        ttk.Label(frame, textvariable=self.target_var, wraplength=650, justify="left").grid(
            row=2, column=1, columnspan=3, sticky="w", pady=(0, 10)
        )

        ttk.Label(frame, text="HF Token").grid(row=3, column=0, sticky="w", pady=(0, 10))
        ttk.Entry(frame, textvariable=self.token_var, show="*", width=66).grid(
            row=3, column=1, columnspan=3, sticky="ew", pady=(0, 10)
        )

        ttk.Label(frame, text="Revision").grid(row=4, column=0, sticky="w", pady=(0, 10))
        ttk.Entry(frame, textvariable=self.revision_var, width=66).grid(
            row=4, column=1, columnspan=3, sticky="ew", pady=(0, 10)
        )

        note = (
            "Tip: most public Hugging Face model ids look like owner/model-name. "
            "The app creates a visible model folder inside the base folder."
        )
        ttk.Label(frame, text=note, wraplength=820, justify="left").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )

        ttk.Checkbutton(
            frame,
            text="Open the target folder while downloading",
            variable=self.auto_open_var,
        ).grid(row=6, column=0, columnspan=4, sticky="w", pady=(0, 10))

        ttk.Label(frame, textvariable=self.progress_var).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(frame, textvariable=self.percent_var).grid(
            row=7, column=3, sticky="e", pady=(0, 6)
        )

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1, value=0)
        self.progress.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(0, 12))

        button_row = ttk.Frame(frame)
        button_row.grid(row=9, column=0, columnspan=4, sticky="w")

        self.download_button = ttk.Button(button_row, text="Download", command=self._start_download)
        self.download_button.pack(side="left")

        self.pause_button = ttk.Button(
            button_row,
            text="Pause",
            command=self._toggle_pause,
            state="disabled",
        )
        self.pause_button.pack(side="left", padx=(10, 0))

        self.cancel_button = ttk.Button(
            button_row,
            text="Cancel",
            command=self._cancel_download,
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=(10, 0))

        self.open_button = ttk.Button(
            button_row,
            text="Open Folder",
            command=self._open_folder,
            state="disabled",
        )
        self.open_button.pack(side="left", padx=(10, 0))

        ttk.Label(frame, textvariable=self.status_var, wraplength=820, justify="left").grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(14, 10)
        )

        ttk.Label(frame, text="Activity").grid(row=11, column=0, columnspan=4, sticky="w")
        self.log = ScrolledText(frame, height=16, wrap="word", state="disabled")
        self.log.grid(row=12, column=0, columnspan=4, sticky="nsew", pady=(6, 0))

    def _choose_destination(self):
        folder = filedialog.askdirectory(
            title="Choose where to save the Hugging Face model",
            initialdir=self.destination_var.get() or str(Path.home()),
        )
        if folder:
            self.destination_var.set(folder)

    def _refresh_target_preview(self, *_):
        model_id = self.model_var.get().strip()
        base = self.destination_var.get().strip()
        if not base:
            self.target_var.set("Choose a base folder first.")
            return
        if not model_id:
            self.target_var.set(str(Path(base).expanduser().resolve()))
            return
        self.target_var.set(str((Path(base).expanduser() / self._sanitize_folder_name(model_id)).resolve()))

    def _sanitize_folder_name(self, model_id):
        cleaned = re.sub(INVALID_FOLDER_CHARS, " - ", model_id).strip(" .")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned or "downloaded-model"

    def _build_unique_target_path(self, base_folder, model_id):
        base_path = Path(base_folder).expanduser().resolve()
        stem = self._sanitize_folder_name(model_id)
        candidate = base_path / stem
        index = 2
        while candidate.exists() and any(candidate.iterdir()):
            candidate = base_path / f"{stem} ({index})"
            index += 1
        return candidate

    def _append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _reset_buttons_after_download(self):
        self.download_button.config(state="normal")
        self.pause_button.config(state="disabled", text="Pause")
        self.cancel_button.config(state="disabled")
        self.open_button.config(state="normal" if self.last_download_path else "disabled")

    def _start_download(self):
        if self.is_downloading:
            return

        model_id = self.model_var.get().strip()
        destination = self.destination_var.get().strip()

        if not model_id:
            messagebox.showerror("Missing Model ID", "Enter a Hugging Face model id first.")
            return
        if not destination:
            messagebox.showerror("Missing Folder", "Choose a base folder first.")
            return

        self.active_target = self._build_unique_target_path(destination, model_id)
        self.active_target.mkdir(parents=True, exist_ok=True)
        self.target_var.set(str(self.active_target))
        self.last_download_path = self.active_target
        self.total_bytes = 0
        self.downloaded_bytes = 0
        self.expected_file_count = 0
        self.current_file = ""
        self.is_downloading = True
        self.is_paused = False
        self.pause_event.set()
        self.cancel_event.clear()

        self._clear_log()
        self._append_log(f"Target folder: {self.active_target}")
        self._append_log(f"Checking access to '{model_id}'...")

        self.download_button.config(state="disabled")
        self.pause_button.config(state="normal", text="Pause")
        self.cancel_button.config(state="normal")
        self.open_button.config(state="normal")

        self.status_var.set("Checking the model and preparing the download...")
        self.progress_var.set("Preparing download...")
        self.percent_var.set("0.0%")
        self.progress.configure(mode="indeterminate", maximum=1, value=0)
        self.progress.start(10)

        if self.auto_open_var.get():
            try:
                os.startfile(self.active_target)
                self._append_log("Opened the target folder so you can watch files appear.")
            except OSError as exc:
                self._append_log(f"Could not open the folder automatically: {exc}")

        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(
                model_id,
                str(self.active_target),
                self.token_var.get().strip(),
                self.revision_var.get().strip(),
            ),
            daemon=True,
        )
        self.download_thread.start()

    def _toggle_pause(self):
        if not self.is_downloading:
            return

        if self.is_paused:
            self.pause_event.set()
            self.is_paused = False
            self.pause_button.config(text="Pause")
            self.status_var.set("Resuming download...")
            self._append_log("Resuming download.")
        else:
            self.pause_event.clear()
            self.is_paused = True
            self.pause_button.config(text="Resume")
            self.status_var.set("Download paused.")
            self._append_log("Paused download.")

    def _cancel_download(self):
        if not self.is_downloading:
            return
        self.cancel_event.set()
        self.pause_event.set()
        self.pause_button.config(state="disabled")
        self.cancel_button.config(state="disabled")
        self.status_var.set("Cancelling download...")
        self._append_log("Cancelling download...")

    def _friendly_error_message(self, model_id, exc, suggestions):
        base_message = str(exc).strip()
        hint = ""
        if "Repository Not Found" in base_message or "401" in base_message or "404" in base_message:
            hint = (
                "Could not access that model id. This usually means the id is wrong, "
                "or the model is private/gated and needs a token."
            )
        elif "403" in base_message or "gated" in base_message.lower():
            hint = "This model looks gated or private. Paste a Hugging Face token and try again."

        parts = []
        if hint:
            parts.append(hint)
        parts.append(f"Model id: {model_id}")
        if suggestions:
            parts.append("Closest matches I found:")
            parts.extend(f"- {item}" for item in suggestions)
        parts.append("")
        parts.append("Technical details:")
        parts.append(base_message)
        return "\n".join(parts)

    def _download_worker(self, model_id, target, token, revision):
        def emit(event_type, payload):
            self.result_queue.put((event_type, payload))

        try:
            download_model(
                model_id,
                target,
                token=token or None,
                revision=revision or None,
                progress_callback=emit,
                pause_event=self.pause_event,
                cancel_event=self.cancel_event,
            )
        except DownloadCancelledError:
            self.result_queue.put(
                (
                    "cancelled",
                    {
                        "path": target,
                        "file_count": len(self._list_visible_files(Path(target))),
                    },
                )
            )
            return
        except Exception as exc:
            try:
                suggestions = suggest_models(model_id, limit=5)
            except Exception:
                suggestions = []
            self.result_queue.put(("error", self._friendly_error_message(model_id, exc, suggestions)))
            return

        visible_files = self._list_visible_files(Path(target))
        self.result_queue.put(
            (
                "success",
                {"path": target, "file_count": len(visible_files)},
            )
        )

    def _list_visible_files(self, folder):
        if not folder.exists():
            return []
        return sorted(path for path in folder.rglob("*") if path.is_file())

    def _update_byte_progress(self, downloaded, total, speed_bytes_per_sec=None, current_file=None):
        self.downloaded_bytes = downloaded
        self.total_bytes = max(total, self.total_bytes)
        max_value = max(self.total_bytes, 1)
        self.progress.configure(mode="determinate", maximum=max_value)
        self.progress["value"] = min(downloaded, max_value)
        self.percent_var.set(format_percent(downloaded, self.total_bytes))

        pieces = [f"{format_size(downloaded)} of {format_size(self.total_bytes)}"]
        if speed_bytes_per_sec is not None and speed_bytes_per_sec > 0:
            pieces.append(f"{format_size(speed_bytes_per_sec)}/s")
        if current_file:
            pieces.append(current_file)
        self.progress_var.set(" | ".join(pieces))

    def _poll_queue(self):
        try:
            result_type, payload = self.result_queue.get_nowait()
        except Empty:
            self.root.after(200, self._poll_queue)
            return

        if result_type == "prepared":
            self.expected_file_count = payload["file_count"]
            self.total_bytes = payload["total_bytes"]
            self.downloaded_bytes = 0
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(self.total_bytes, 1), value=0)
            self.percent_var.set("0.0%")
            self.progress_var.set(
                f"0 B of {format_size(self.total_bytes)} | {self.expected_file_count} files"
            )
            self.status_var.set(f"Downloading into {payload['target']}")
            self._append_log(
                f"Found {payload['file_count']} files totaling {format_size(payload['total_bytes'])}."
            )
            for remote_file in payload["files"][:12]:
                self._append_log(f"Queued {remote_file.filename}")
            if payload["file_count"] > 12:
                self._append_log("More files are queued and will be logged as they start.")

        elif result_type == "file_start":
            self.current_file = payload["filename"]
            self.status_var.set(
                f"Downloading file {payload['file_index']} of {payload['file_count']}: {payload['filename']}"
            )
            self._append_log(f"Starting {payload['filename']}")

        elif result_type == "progress":
            self.current_file = payload["filename"]
            self._update_byte_progress(
                payload["overall_downloaded"],
                payload["total_bytes"],
                speed_bytes_per_sec=payload["speed_bytes_per_sec"],
                current_file=payload["filename"],
            )
            if not self.is_paused:
                self.status_var.set(
                    f"Downloading file {payload['file_index']} of {payload['file_count']}: {payload['filename']}"
                )

        elif result_type == "file_complete":
            self.current_file = payload["filename"]
            self._update_byte_progress(
                payload["overall_downloaded"],
                payload["total_bytes"],
                current_file=payload["filename"],
            )
            if payload["skipped"]:
                self._append_log(f"Already had {payload['filename']}")
            else:
                self._append_log(f"Saved {payload['filename']}")
            self.status_var.set(
                f"Finished file {payload['file_index']} of {payload['file_count']}: {payload['filename']}"
            )

        elif result_type == "success":
            self.is_downloading = False
            self.is_paused = False
            self.pause_event.set()
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(self.total_bytes, 1), value=max(self.total_bytes, 1))
            self.percent_var.set("100.0%")
            self.progress_var.set(
                f"Download complete | {format_size(self.total_bytes)} downloaded"
            )
            self.status_var.set(f"Download finished. Files are visibly saved in: {payload['path']}")
            self._append_log(f"Download complete. Visible files saved: {payload['file_count']}")
            self.last_download_path = payload["path"]
            self._reset_buttons_after_download()
            messagebox.showinfo("Download Complete", f"Model files are in:\n{payload['path']}")

        elif result_type == "cancelled":
            self.is_downloading = False
            self.is_paused = False
            self.pause_event.set()
            self.progress.stop()
            self.percent_var.set(format_percent(self.downloaded_bytes, self.total_bytes))
            self.progress_var.set(
                f"Cancelled at {format_size(self.downloaded_bytes)} of {format_size(self.total_bytes)}"
            )
            self.status_var.set(f"Download cancelled. Completed files remain in: {payload['path']}")
            self._append_log(
                f"Download cancelled. Visible files kept: {payload['file_count']}"
            )
            self.last_download_path = payload["path"]
            self._reset_buttons_after_download()

        elif result_type == "error":
            self.is_downloading = False
            self.is_paused = False
            self.pause_event.set()
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=max(self.total_bytes, 1), value=self.downloaded_bytes)
            self.percent_var.set(format_percent(self.downloaded_bytes, self.total_bytes))
            self.progress_var.set("Download failed.")
            self.status_var.set("Download failed. Read the message below and try again.")
            self._append_log("Download failed.")
            for line in payload.splitlines():
                self._append_log(line)
            self._reset_buttons_after_download()
            messagebox.showerror("Download Failed", payload)

        self.root.after(200, self._poll_queue)

    def _open_folder(self):
        target = self.last_download_path or self.active_target
        if not target:
            return
        os.startfile(target)


def main():
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    HFDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
