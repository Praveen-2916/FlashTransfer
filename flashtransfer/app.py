from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .adb_client import ADBClient, ADBError, DeviceInfo, RemoteEntry
from .transfer import TransferJob

APP_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = Path.home() / "flashtransfer.log"
logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
CONFIG_PATH = Path.home() / ".flashtransfer.json"
START_PATH = "/storage/emulated/0"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} TiB"



class FlashTransferApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("FlashTransfer")
        self.root.geometry("1240x760")
        self.root.minsize(1000, 650)
        self.root.configure(bg="#f4f6fa")

        self.events: queue.Queue = queue.Queue()
        self.client = ADBClient()
        self.devices: list[DeviceInfo] = []
        self.current_path = START_PATH
        self.entries: list[RemoteEntry] = []
        self.selected_entry: RemoteEntry | None = None
        self.destination = tk.StringVar(value=str(Path.home() / "Downloads"))
        self.device_var = tk.StringVar(value="Checking for devices…")
        self.device_status = tk.StringVar(value="Not connected")
        self.path_var = tk.StringVar(value=START_PATH)
        self.selected_var = tk.StringVar(value="Select a file from your phone")
        self.status_var = tk.StringVar(value="Connect your Android phone to get started.")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value="Ready")
        self.transfer_details = tk.StringVar(value="0 B transferred")
        self.speed_text = tk.StringVar(value="— MiB/s")
        self.job: TransferJob | None = None
        self.refreshing = False
        self.browsing = False
        self.transfer_active = False
        self.progress_indeterminate = False
        self._load_config()
        self._setup_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(150, self._poll_events)
        self.refresh_devices()

    def _load_config(self) -> None:
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self.client.set_path(str(config.get("adb_path", "")))
            saved_destination = config.get("destination")
            if saved_destination and os.path.isdir(saved_destination):
                self.destination.set(saved_destination)
        except (OSError, ValueError, TypeError):
            pass

    def _save_config(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps({"adb_path": self.client.adb_path, "destination": self.destination.get()}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _setup_style(self) -> None:
        style = ttk.Style(self.root)
        # clam gives consistent foreground/background rendering for custom buttons
        # across normal and maximized windows on Windows.
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#f4f6fa")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f4f6fa", foreground="#172033", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 9))
        style.configure("Title.TLabel", background="#f4f6fa", foreground="#172033", font=("Segoe UI Semibold", 22))
        style.configure("Section.TLabel", background="#ffffff", foreground="#172033", font=("Segoe UI Semibold", 11))
        style.configure("Status.TLabel", background="#ffffff", foreground="#475467", font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 9), padding=(10, 6), foreground="#172033", background="#edf1f7")
        style.map("TButton", foreground=[("disabled", "#667085"), ("active", "#101828")], background=[("active", "#dce5f3")])
        style.configure("Accent.TButton", font=("Segoe UI Semibold", 10), padding=(12, 9), foreground="#ffffff", background="#2458c6")
        style.map("Accent.TButton", foreground=[("disabled", "#ffffff"), ("active", "#ffffff")], background=[("disabled", "#8ba5df"), ("active", "#1746a2")])
        style.configure("Cancel.TButton", font=("Segoe UI Semibold", 9), padding=(10, 9), foreground="#172033", background="#e4e7ec")
        style.map("Cancel.TButton", foreground=[("disabled", "#667085"), ("active", "#101828")], background=[("active", "#d0d5dd")])
        style.configure("Treeview", rowheight=29, font=("Segoe UI", 10), background="#ffffff", fieldbackground="#ffffff")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), padding=7)
        style.configure("Horizontal.TProgressbar", troughcolor="#e8edf5", background="#3769d7", thickness=12)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 14))
        title_box = ttk.Frame(header)
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="FlashTransfer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="Simple, direct Android-to-Windows transfers", foreground="#667085").pack(anchor="w", pady=(2, 0))
        ttk.Button(header, text="Locate ADB", command=self.choose_adb).pack(side="right", padx=(8, 0), anchor="center")
        ttk.Button(header, text="Refresh device", command=self.refresh_devices).pack(side="right", anchor="center")

        device_card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 11))
        device_card.pack(fill="x", pady=(0, 12))
        top_line = ttk.Frame(device_card, style="Card.TFrame")
        top_line.pack(fill="x")
        ttk.Label(top_line, text="DEVICE", style="Muted.TLabel").pack(side="left")
        self.status_badge = ttk.Label(top_line, textvariable=self.device_status, style="Status.TLabel")
        self.status_badge.pack(side="right")
        device_line = ttk.Frame(device_card, style="Card.TFrame")
        device_line.pack(fill="x", pady=(5, 0))
        self.device_combo = ttk.Combobox(device_line, textvariable=self.device_var, state="readonly", width=45)
        self.device_combo.pack(side="left", fill="x", expand=True)
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        ttk.Label(device_line, text="USB debugging must be enabled and authorized", style="Muted.TLabel").pack(side="right", padx=(12, 0))

        # Main work area: phone browser on the left, transfer destination/status on the right.
        work_area = ttk.Frame(outer)
        work_area.pack(fill="both", expand=True)
        work_area.columnconfigure(0, weight=3, minsize=560)
        work_area.columnconfigure(1, weight=2, minsize=340)
        work_area.rowconfigure(0, weight=1)

        browser_card = ttk.Frame(work_area, style="Card.TFrame", padding=14)
        browser_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        browser_head = ttk.Frame(browser_card, style="Card.TFrame")
        browser_head.pack(fill="x", pady=(0, 8))
        ttk.Label(browser_head, text="Phone files", style="Section.TLabel").pack(side="left")
        ttk.Button(browser_head, text="Up one folder", command=self.go_up).pack(side="right")
        ttk.Button(browser_head, text="Refresh", command=self.browse_current).pack(side="right", padx=(0, 7))
        path_row = ttk.Frame(browser_card, style="Card.TFrame")
        path_row.pack(fill="x", pady=(0, 8))
        ttk.Label(path_row, text="Location", style="Muted.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(path_row, textvariable=self.path_var, background="#f2f5fb", foreground="#344054", padding=(8, 5)).pack(side="left", fill="x", expand=True)

        columns = ("name", "kind", "size")
        self.tree = ttk.Treeview(browser_card, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("name", text="Name")
        self.tree.heading("kind", text="Type")
        self.tree.heading("size", text="Size")
        self.tree.column("name", width=400, minwidth=180, anchor="w", stretch=True)
        self.tree.column("kind", width=80, minwidth=65, anchor="w", stretch=False)
        self.tree.column("size", width=110, minwidth=90, anchor="e", stretch=False)
        scrollbar = ttk.Scrollbar(browser_card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        transfer_card = ttk.Frame(work_area, style="Card.TFrame", padding=14)
        transfer_card.grid(row=0, column=1, sticky="nsew")
        ttk.Label(transfer_card, text="Transfer to this PC", style="Section.TLabel").pack(anchor="w")
        ttk.Label(transfer_card, textvariable=self.selected_var, style="Muted.TLabel", wraplength=360, justify="left").pack(anchor="w", pady=(5, 12), fill="x")
        ttk.Label(transfer_card, text="Destination folder", style="Muted.TLabel").pack(anchor="w", pady=(0, 4))
        dest_row = ttk.Frame(transfer_card, style="Card.TFrame")
        dest_row.pack(fill="x")
        self.dest_entry = ttk.Entry(dest_row, textvariable=self.destination)
        self.dest_entry.pack(fill="x", expand=True)
        ttk.Button(transfer_card, text="Choose folder…", command=self.choose_destination).pack(anchor="e", pady=(7, 0))

        ttk.Separator(transfer_card).pack(fill="x", pady=14)
        progress_head = ttk.Frame(transfer_card, style="Card.TFrame")
        progress_head.pack(fill="x", pady=(0, 6))
        ttk.Label(progress_head, textvariable=self.progress_text, style="Status.TLabel").pack(side="left")
        ttk.Label(progress_head, textvariable=self.speed_text, style="Status.TLabel").pack(side="right")
        self.progress_bar = ttk.Progressbar(transfer_card, variable=self.progress_var, maximum=100, mode="determinate")
        self.progress_bar.pack(fill="x")
        ttk.Label(transfer_card, textvariable=self.transfer_details, style="Muted.TLabel", wraplength=360).pack(anchor="w", pady=(7, 2))

        button_row = ttk.Frame(transfer_card, style="Card.TFrame")
        button_row.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(button_row, text="START TRANSFER", style="Accent.TButton", command=self.start_transfer)
        self.start_button.pack(side="left", fill="x", expand=True)
        self.cancel_button = ttk.Button(button_row, text="CANCEL TRANSFER", style="Cancel.TButton", command=self.cancel_transfer, state="disabled")
        self.cancel_button.pack(side="left", padx=(8, 0), fill="x", expand=True)
        ttk.Label(transfer_card, textvariable=self.status_var, style="Muted.TLabel", wraplength=360, justify="left").pack(anchor="w", fill="x")

    def choose_adb(self) -> None:
        path = filedialog.askopenfilename(
            title="Select adb.exe from Android Platform-Tools",
            filetypes=[("ADB executable", "adb.exe"), ("Executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.client.set_path(path)
            self._save_config()
            self.refresh_devices()

    def _selected_device(self) -> DeviceInfo | None:
        index = self.device_combo.current()
        if index < 0 or index >= len(self.devices):
            return None
        device = self.devices[index]
        return device if device.state == "device" else None

    def refresh_devices(self) -> None:
        if self.refreshing or self.transfer_active:
            return
        self.refreshing = True
        self.device_status.set("Checking…")
        self.status_var.set("Checking for Android devices…")

        def task() -> None:
            try:
                result = self.client.devices()
                self.events.put(("devices", {"devices": result}))
            except Exception as exc:
                self.events.put(("device_error", {"text": str(exc)}))
        threading.Thread(target=task, name="FlashTransferDeviceCheck", daemon=True).start()

    def _on_device_selected(self, _event: object = None) -> None:
        device = self._selected_device()
        if device:
            self.device_status.set("Connected")
            self.status_var.set(f"Connected to {device.model}.")
            self.current_path = START_PATH
            self.browse_current()
        elif self.devices:
            selected = self.devices[self.device_combo.current()]
            if selected.state == "unauthorized":
                self.device_status.set("Authorization needed")
                self.status_var.set("Unlock your phone and approve the USB debugging prompt.")
            else:
                self.device_status.set("Device offline")
                self.status_var.set("Reconnect the phone and check the USB cable.")

    def browse_current(self) -> None:
        device = self._selected_device()
        if not device or self.browsing or self.transfer_active:
            if not device:
                self.status_var.set("Connect and authorize an Android device first.")
            return
        self.browsing = True
        self.path_var.set(self.current_path)
        self.status_var.set("Loading phone files…")

        def task() -> None:
            try:
                entries = self.client.list_dir(device.serial, self.current_path)
                logging.info("Listed %s: %d entries from device %s", self.current_path, len(entries), device.serial)
                self.events.put(("listing", {"path": self.current_path, "entries": entries}))
            except Exception as exc:
                logging.exception("Directory listing failed for %s", self.current_path)
                self.events.put(("listing_error", {"text": f"{exc}\n\nDiagnostic log: {LOG_PATH}"}))
        threading.Thread(target=task, name="FlashTransferBrowser", daemon=True).start()

    def go_up(self) -> None:
        if self.current_path.rstrip("/") in {"", "/", "/storage/emulated/0"}:
            if self.current_path != "/storage/emulated/0":
                self.current_path = "/storage/emulated/0"
                self.browse_current()
            else:
                self.status_var.set("You are at the internal storage root.")
            return
        from pathlib import PurePosixPath
        parent = str(PurePosixPath(self.current_path).parent)
        self.current_path = parent if parent else "/"
        self.browse_current()

    def _on_double_click(self, _event: object = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except (TypeError, ValueError):
            return
        if not 0 <= index < len(self.entries):
            return

        entry = self.entries[index]
        if entry.is_dir:
            self.current_path = entry.path
            self.selected_entry = None
            self.selected_var.set("Select a file from your phone")
            self.browse_current()
        else:
            # Double-clicking a file selects it and opens the transfer workflow.
            self.selected_entry = entry
            self.selected_var.set(f"Selected: {entry.path}  ·  {format_bytes(entry.size)}")
            self.status_var.set("File selected. Choose a destination, then click ‘Start transfer’.")

    def _on_tree_select(self, _event: object = None) -> None:
        selection = self.tree.selection()
        if not selection:
            self.selected_entry = None
            return
        index = int(selection[0])
        if 0 <= index < len(self.entries):
            entry = self.entries[index]
            if not entry.is_dir:
                self.selected_entry = entry
                self.selected_var.set(f"Selected: {entry.path}  ·  {format_bytes(entry.size)}")
                self.status_var.set("File selected. Choose a destination, then click START TRANSFER.")
            else:
                self.selected_entry = None
                self.selected_var.set("Double-click a folder to open it, or select a file to transfer.")

    def choose_destination(self) -> None:
        path = filedialog.askdirectory(title="Choose where to save the file", initialdir=self.destination.get() or str(Path.home()))
        if path:
            self.destination.set(path)
            self._save_config()

    def start_transfer(self) -> None:
        device = self._selected_device()
        entry = self.selected_entry
        destination = self.destination.get().strip().strip('"')
        if not device:
            messagebox.showwarning("No connected phone", "Connect and authorize your Android phone first.", parent=self.root)
            return
        if not entry or entry.is_dir:
            messagebox.showwarning("Select a file", "Select a file in the phone browser before starting a transfer.", parent=self.root)
            return
        if not os.path.isdir(destination):
            messagebox.showerror("Choose a destination", "Choose an existing Windows folder for the transferred file.", parent=self.root)
            return
        final_path = Path(destination) / entry.name
        overwrite_existing = final_path.exists()
        if overwrite_existing:
            overwrite = messagebox.askyesno(
                "File already exists",
                f"{entry.name} already exists in the destination. Replace it only after the new transfer completes?",
                parent=self.root,
            )
            if not overwrite:
                return

        self.transfer_active = True
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_bar.stop()
        self.progress_indeterminate = False
        self.progress_bar.configure(mode="determinate")
        self.progress_var.set(0)
        self.progress_text.set("Starting transfer… 0.0%")
        self.transfer_details.set(f"0 B transferred / {format_bytes(entry.size) if entry.size is not None else 'size unknown'}")
        self.speed_text.set("Waiting for data…")
        self.status_var.set("Starting ADB transfer. Progress will update as data reaches this PC.")
        self.root.update_idletasks()
        self._save_config()
        self.job = TransferJob(self.client, device.serial, entry.path, destination, self.events, overwrite_existing=overwrite_existing)
        self.job.start()

    def cancel_transfer(self) -> None:
        if self.job and self.transfer_active:
            self.cancel_button.configure(state="disabled")
            self.status_var.set("Cancelling transfer safely…")
            self.job.cancel()

    def _finish_transfer_ui(self) -> None:
        self.transfer_active = False
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.job = None

    def _poll_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "devices":
                    self.refreshing = False
                    self.devices = data["devices"]
                    labels = []
                    for device in self.devices:
                        state_text = {"device": "Connected", "unauthorized": "Authorization needed", "offline": "Offline"}.get(device.state, device.state)
                        labels.append(f"{device.model}  ·  {device.serial}  ·  {state_text}")
                    self.device_combo["values"] = labels
                    authorized_indices = [i for i, device in enumerate(self.devices) if device.state == "device"]
                    if authorized_indices:
                        self.device_combo.current(authorized_indices[0])
                        self.device_status.set("Connected")
                        self.status_var.set(f"Connected to {self.devices[authorized_indices[0]].model}.")
                        if not self.browsing:
                            self.current_path = START_PATH
                            self.browse_current()
                    elif self.devices:
                        self.device_combo.current(0)
                        self._on_device_selected()
                    else:
                        self.device_combo.set("No Android device detected")
                        self.device_status.set("Not connected")
                        self.status_var.set("Connect your phone, unlock it, and check USB debugging.")
                        self._clear_tree()
                elif kind == "device_error":
                    self.refreshing = False
                    self.device_status.set("ADB unavailable")
                    self.status_var.set(data["text"])
                    self.device_combo.set("ADB not configured")
                elif kind == "listing":
                    self.browsing = False
                    self.current_path = data["path"]
                    self.entries = data["entries"]
                    self.path_var.set(self.current_path)
                    self._clear_tree(clear_entries=False)
                    for i, entry in enumerate(self.entries):
                        name = ("📁  " if entry.is_dir else "     ") + entry.name
                        self.tree.insert("", "end", iid=str(i), values=(name, "Folder" if entry.is_dir else "File", "—" if entry.is_dir else format_bytes(entry.size)))
                    if self.entries:
                        self.status_var.set(f"{len(self.entries)} items · Double-click a folder to open it.")
                    else:
                        self.status_var.set("This folder is accessible but contains no visible entries. Try Refresh or the parent folder.")
                elif kind == "listing_error":
                    self.browsing = False
                    self.status_var.set(data["text"])
                    messagebox.showerror("Could not browse folder", data["text"], parent=self.root)
                elif kind == "progress":
                    percent = data["percent"]
                    if percent is None:
                        if not self.progress_indeterminate:
                            self.progress_bar.configure(mode="indeterminate")
                            self.progress_bar.start(10)
                            self.progress_indeterminate = True
                        self.progress_text.set("Transferring…")
                    else:
                        if self.progress_indeterminate:
                            self.progress_bar.stop()
                            self.progress_indeterminate = False
                        self.progress_bar.configure(mode="determinate")
                        self.progress_var.set(float(percent))
                        self.progress_text.set(f"{percent:.1f}% transferred")
                    total = data["total"]
                    transferred = data["transferred"]
                    self.transfer_details.set(f"{format_bytes(transferred)} transferred" + (f" / {format_bytes(total)}" if total else ""))
                    self.speed_text.set(f"{data['speed_mib']:.2f} MiB/s")
                elif kind == "status":
                    self.status_var.set(data["text"])
                elif kind == "complete":
                    self.progress_bar.stop()
                    self.progress_indeterminate = False
                    self.progress_bar.configure(mode="determinate")
                    self.progress_var.set(100)
                    self.progress_text.set("Transfer complete")
                    self.status_var.set(f"Saved to {data['path']}")
                    self._finish_transfer_ui()
                    messagebox.showinfo("Transfer complete", f"File saved successfully:\n\n{data['path']}\n\nTime: {data['elapsed']:.1f} seconds", parent=self.root)
                elif kind in {"failed", "cancelled"}:
                    self.progress_bar.stop()
                    self.progress_indeterminate = False
                    self.progress_bar.configure(mode="determinate")
                    self._finish_transfer_ui()
                    self.status_var.set(data["text"])
                    if kind == "failed":
                        messagebox.showerror("Transfer failed", data["text"], parent=self.root)
                    else:
                        self.progress_text.set("Cancelled")
                        messagebox.showinfo("Transfer cancelled", data["text"], parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _clear_tree(self, clear_entries: bool = True) -> None:
        """Clear visible rows; optionally discard the backing entry list too."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        if clear_entries:
            self.entries = []
        self.selected_entry = None
        self.selected_var.set("Select a file from your phone")

    def _on_close(self) -> None:
        if self.transfer_active and self.job:
            if not messagebox.askyesno("Transfer in progress", "Cancel the transfer and close FlashTransfer?", parent=self.root):
                return
            self.job.cancel()
            # Don't kill the app while the worker is still cleaning its partial file.
            self.root.after(250, self._wait_for_worker_close)
            return
        self._save_config()
        self.root.destroy()

    def _wait_for_worker_close(self) -> None:
        if self.job and self.job.thread.is_alive():
            self.root.after(150, self._wait_for_worker_close)
        else:
            self._save_config()
            self.root.destroy()


def main() -> None:
    root = tk.Tk()
    FlashTransferApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
