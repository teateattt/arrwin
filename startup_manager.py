"""Manage the per-user Windows startup registration."""

import subprocess
import sys
import winreg
from pathlib import Path


STARTUP_ARGUMENT = "--startup"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED_KEY_PATH = (
    r"Software\Microsoft\Windows\CurrentVersion"
    r"\Explorer\StartupApproved\Run"
)
VALUE_NAME = "ArrWin"
DISABLED_APPROVAL_STATE = 3


def startup_requested(argv=None):
    """Return whether this process was launched by the startup entry."""
    arguments = sys.argv[1:] if argv is None else argv
    return STARTUP_ARGUMENT in arguments


def build_startup_command():
    """Build the command written to the current user's Run key."""
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        command_parts = [str(executable), STARTUP_ARGUMENT]
    else:
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
        command_parts = [
            str(executable),
            str(Path(sys.argv[0]).resolve()),
            STARTUP_ARGUMENT,
        ]
    return subprocess.list2cmdline(command_parts)


class StartupManager:
    """Read and update the current user's HKCU startup registration."""

    def __init__(
        self,
        command=None,
        registry=winreg,
        run_key_path=RUN_KEY_PATH,
        approval_key_path=STARTUP_APPROVED_KEY_PATH,
        value_name=VALUE_NAME,
    ):
        self.command = command or build_startup_command()
        self.registry = registry
        self.run_key_path = run_key_path
        self.approval_key_path = approval_key_path
        self.value_name = value_name

    def _query_value(self, key_path, value_name):
        try:
            with self.registry.OpenKey(
                self.registry.HKEY_CURRENT_USER,
                key_path,
                0,
                self.registry.KEY_QUERY_VALUE,
            ) as key:
                return self.registry.QueryValueEx(key, value_name)[0]
        except FileNotFoundError:
            return None

    def _write_run_command(self):
        with self.registry.CreateKeyEx(
            self.registry.HKEY_CURRENT_USER,
            self.run_key_path,
            0,
            self.registry.KEY_SET_VALUE,
        ) as key:
            self.registry.SetValueEx(
                key,
                self.value_name,
                0,
                self.registry.REG_SZ,
                self.command,
            )

    def _delete_value(self, key_path, value_name):
        try:
            with self.registry.OpenKey(
                self.registry.HKEY_CURRENT_USER,
                key_path,
                0,
                self.registry.KEY_SET_VALUE,
            ) as key:
                self.registry.DeleteValue(key, value_name)
        except FileNotFoundError:
            pass

    def get_registered_command(self):
        """Return the configured command, or None when no entry exists."""
        return self._query_value(self.run_key_path, self.value_name)

    def is_enabled(self):
        """Return the effective enabled state, respecting Task Manager."""
        if self.get_registered_command() is None:
            return False
        approval = self._query_value(
            self.approval_key_path,
            self.value_name,
        )
        return not (
            approval
            and approval[0] == DISABLED_APPROVAL_STATE
        )

    def sync_path(self):
        """Update an existing startup entry without re-enabling it."""
        registered_command = self.get_registered_command()
        if registered_command is None or registered_command == self.command:
            return False
        self._write_run_command()
        return True

    def set_enabled(self, enabled):
        """Enable or disable startup for the current Windows user."""
        if enabled:
            self._write_run_command()
            self._delete_value(self.approval_key_path, self.value_name)
        else:
            self._delete_value(self.run_key_path, self.value_name)
            self._delete_value(self.approval_key_path, self.value_name)
