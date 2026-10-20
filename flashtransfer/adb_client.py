from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath


class ADBError(RuntimeError):
    """An ADB or Android filesystem operation failed."""


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    state: str
    model: str


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None


def shell_quote(value: str) -> str:
    """Quote one argument for Android's POSIX shell."""
    return "'" + value.replace("'", "'\\''") + "'"


def parse_ls_output(output: str, current_path: str) -> list[RemoteEntry]:
    """Parse `ls -1Ap` output (one item per line, directories end in /)."""
    entries: list[RemoteEntry] = []
    for raw in output.splitlines():
        name = raw.rstrip("\r")
        if not name or name in {".", "../", "./", ".."}:
            continue
        is_dir = name.endswith("/")
        if is_dir:
            name = name[:-1]
        if not name or name in {".", ".."}:
            continue
        # ls -p marks directories with '/', but a symlink to a directory may
        # also be marked this way. ADB pull will still report inaccessible items.
        child_path = str(PurePosixPath(current_path) / name)
        entries.append(RemoteEntry(name=name, path=child_path, is_dir=is_dir, size=None))
    entries.sort(key=lambda item: (not item.is_dir, item.name.casefold()))
    return entries


class ADBClient:
    def __init__(self, adb_path: str = "") -> None:
        self.adb_path = (adb_path or "").strip().strip('"')

    def set_path(self, adb_path: str) -> None:
        self.adb_path = (adb_path or "").strip().strip('"')

    def resolve(self) -> str:
        if self.adb_path and os.path.isfile(self.adb_path):
            return self.adb_path
        found = shutil.which("adb")
        if found:
            self.adb_path = found
            return found
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for candidate in (os.path.join(base, "platform-tools", "adb.exe"), os.path.join(base, "adb.exe")):
            if os.path.isfile(candidate):
                self.adb_path = candidate
                return candidate
        raise ADBError("ADB was not found. Click ‘Locate ADB’ and choose adb.exe from Android Platform-Tools.")

    def run(self, args: list[str], timeout: int = 25) -> str:
        try:
            result = subprocess.run(
                [self.resolve(), *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise ADBError("ADB timed out. Check the USB connection and try again.") from exc
        except OSError as exc:
            raise ADBError(f"Could not start ADB: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            low = detail.lower()
            if "unauthorized" in low:
                raise ADBError("The phone has not authorized this computer. Unlock it and approve the USB debugging prompt.")
            if "no devices/emulators found" in low:
                raise ADBError("No ADB device is available. Check the cable and USB debugging authorization.")
            raise ADBError(detail or f"ADB command failed (exit code {result.returncode}).")
        return result.stdout.strip("\r\n")

    def devices(self) -> list[DeviceInfo]:
        output = self.run(["devices", "-l"])
        found: list[DeviceInfo] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] not in {"device", "unauthorized", "offline"}:
                continue
            match = re.search(r"\bmodel:(\S+)", line)
            model = match.group(1).replace("_", " ") if match else "Android device"
            found.append(DeviceInfo(parts[0], parts[1], model))
        return found

    def shell(self, serial: str, command: str, timeout: int = 30) -> str:
        # Pass the remote shell command as one argument. Do not use shell=True
        # on Windows; that would introduce a second, unnecessary shell parser.
        return self.run(["-s", serial, "shell", command], timeout=timeout)

    def list_dir(self, serial: str, path: str) -> list[RemoteEntry]:
        path = path.rstrip("/") or "/"
        quoted = shell_quote(path)
        # Check the directory first so inaccessible folders are not presented as empty.
        check = self.shell(serial, f"if [ -d {quoted} ]; then echo FT_DIR_OK; else echo FT_DIR_ERROR; fi", timeout=15)
        if "FT_DIR_OK" not in check.splitlines():
            raise ADBError(f"Android cannot access this folder:\n{path}\n\nTry the parent folder and check that the phone is unlocked.")

        # Long listing includes file sizes. The previous `ls -1Ap` output only
        # returned names, so every file appeared with an unknown size in the UI.
        try:
            output = self.shell(serial, f"ls -la {quoted}", timeout=45)
            entries = self._parse_long_listing(output, path)
        except ADBError:
            entries = []

        # Compatibility fallback for devices with an unusual `ls -l` format.
        if not entries:
            output = self.shell(serial, f"ls -1Ap {quoted}", timeout=45)
            entries = parse_ls_output(output, path)

        return entries

    @staticmethod
    def _parse_long_listing(output: str, path: str) -> list[RemoteEntry]:
        entries: list[RemoteEntry] = []
        for line in output.splitlines():
            # Android toybox ls normally emits: mode links owner group size date time name
            parts = line.split(None, 7)
            if len(parts) < 8 or not parts[0] or parts[0][0] not in "-dlcbps":
                continue
            name = parts[7].strip()
            if not name or name in {".", ".."}:
                continue
            # If a legacy ls format is encountered, ignore ambiguous timestamps
            # rather than silently inventing a name.
            if name.startswith((".",)) and name in {".", ".."}:
                continue
            try:
                size = int(parts[4])
            except ValueError:
                size = None
            is_dir = parts[0][0] == "d"
            child = str(PurePosixPath(path) / name)
            entries.append(RemoteEntry(name, child, is_dir, size))
        entries.sort(key=lambda item: (not item.is_dir, item.name.casefold()))
        return entries

    def file_size(self, serial: str, path: str) -> int | None:
        try:
            output = self.shell(serial, f"stat -c %s {shell_quote(path)}", timeout=12).strip()
            if output.isdigit():
                return int(output)
        except ADBError:
            pass
        return None
