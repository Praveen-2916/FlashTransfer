from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from queue import Queue

from .adb_client import ADBClient, ADBError


class TransferJob:
    """Runs adb pull off the UI thread and reports measured file-growth progress."""

    def __init__(
        self,
        client: ADBClient,
        serial: str,
        remote_path: str,
        destination_folder: str,
        events: Queue,
        overwrite_existing: bool = False,
    ) -> None:
        self.client = client
        self.serial = serial
        self.remote_path = remote_path
        self.destination_folder = destination_folder
        self.events = events
        self.overwrite_existing = overwrite_existing
        self.cancel_event = threading.Event()
        self.process: subprocess.Popen | None = None
        self.thread = threading.Thread(target=self._run, name="FlashTransferWorker", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _emit(self, kind: str, **data: object) -> None:
        self.events.put((kind, data))

    def _run(self) -> None:
        source_name = self.remote_path.rstrip("/").split("/")[-1] or "android-file"
        final_path = Path(self.destination_folder) / source_name
        temp_path: Path | None = None
        log_path: Path | None = None
        log_handle = None
        started = time.perf_counter()

        try:
            if not os.path.isdir(self.destination_folder):
                raise ADBError("The selected Windows destination folder no longer exists.")

            total_bytes = self.client.file_size(self.serial, self.remote_path)
            self._emit("status", text="Preparing transfer…")

            # Create a unique temporary filename in the destination folder. It is only
            # renamed to the final filename after ADB succeeds and size validation passes.
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{source_name}.", suffix=".flashtransfer.part", dir=self.destination_folder
            )
            os.close(fd)
            os.unlink(temp_name)
            temp_path = Path(temp_name)

            log_fd, log_name = tempfile.mkstemp(prefix="flashtransfer-adb-", suffix=".log")
            log_path = Path(log_name)
            log_handle = os.fdopen(log_fd, "w+b")

            command = [self.client.resolve(), "-s", self.serial, "pull", self.remote_path, str(temp_path)]
            self.process = subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            samples: deque[tuple[float, int]] = deque()
            last_emit = 0.0
            last_sample = 0.0
            current_bytes = 0
            while self.process.poll() is None:
                if self.cancel_event.is_set():
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        try:
                            self.process.kill()
                            self.process.wait(timeout=2)
                        except OSError:
                            pass
                    break

                now = time.perf_counter()
                # Poll the destination at 5 Hz instead of ~8 Hz. This is enough for
                # a smooth progress display while reducing filesystem metadata checks.
                if now - last_sample >= 0.20 or last_sample == 0.0:
                    try:
                        current_bytes = temp_path.stat().st_size
                    except OSError:
                        current_bytes = 0
                    samples.append((now, current_bytes))
                    last_sample = now
                    while len(samples) > 1 and now - samples[0][0] > 2.0:
                        samples.popleft()
                if len(samples) >= 2:
                    duration = samples[-1][0] - samples[0][0]
                    speed_mib = max(0.0, samples[-1][1] - samples[0][1]) / (1024 * 1024) / max(duration, 0.001)
                else:
                    speed_mib = 0.0

                if now - last_emit >= 0.25:
                    percent = min(99.0, current_bytes * 100.0 / total_bytes) if total_bytes and total_bytes > 0 else None
                    self._emit(
                        "progress",
                        percent=percent,
                        transferred=current_bytes,
                        total=total_bytes,
                        speed_mib=speed_mib,
                    )
                    last_emit = now
                time.sleep(0.05)

            return_code = self.process.wait()
            if log_handle:
                log_handle.flush()
                log_handle.close()
                log_handle = None
            output = ""
            if log_path.exists():
                output = log_path.read_text(encoding="utf-8", errors="replace")[-6000:]

            if self.cancel_event.is_set():
                raise InterruptedError("Transfer cancelled.")
            if return_code != 0:
                lowered = output.lower()
                if "unauthorized" in lowered:
                    raise ADBError("The phone is not authorized. Unlock it and approve the USB debugging prompt.")
                if "device" in lowered and ("not found" in lowered or "offline" in lowered):
                    raise ADBError("The phone disconnected during transfer. Reconnect it and try again.")
                if "permission denied" in lowered:
                    raise ADBError("Android denied access to this file. Select a file in accessible shared storage.")
                raise ADBError(output.strip() or "Transfer failed. Check the phone connection and file path.")

            if not temp_path.exists():
                raise ADBError("ADB reported success, but the destination file was not created.")
            actual_size = temp_path.stat().st_size
            if total_bytes is not None and actual_size != total_bytes:
                raise ADBError(
                    f"The size check failed. Expected {total_bytes:,} bytes, but received {actual_size:,} bytes."
                )

            # Recheck the destination to avoid unexpectedly replacing a file created
            # while the transfer was running.
            if final_path.exists() and not self.overwrite_existing:
                raise ADBError(f"A file named '{source_name}' appeared in the destination during transfer. Rename or move it, then retry.")
            os.replace(temp_path, final_path)
            temp_path = None
            elapsed = max(time.perf_counter() - started, 0.001)
            average_speed = actual_size / (1024 * 1024) / elapsed
            self._emit(
                "progress", percent=100.0, transferred=actual_size,
                total=total_bytes if total_bytes is not None else actual_size,
                speed_mib=average_speed,
            )
            self._emit("complete", path=str(final_path), elapsed=elapsed, speed_mib=average_speed)

        except InterruptedError as exc:
            self._emit("cancelled", text=str(exc))
        except Exception as exc:
            self._emit("failed", text=str(exc))
        finally:
            if log_handle:
                try:
                    log_handle.close()
                except OSError:
                    pass
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if log_path:
                try:
                    log_path.unlink(missing_ok=True)
                except OSError:
                    pass
