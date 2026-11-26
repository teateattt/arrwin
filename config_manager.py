"""JSON-backed application settings with editable defaults."""

import json
import os
import sys
from pathlib import Path


DEFAULT_CONFIG = {
    "window_width": 700,
    "window_height": 680,
    "window_offset": 35,
    "auto_raise_enabled": False,
    "auto_raise_nudge_overlap": False,
    "auto_raise_exclude_self": True,
    "ctrl_move_raise_enabled": False,
    "ctrl_move_required": True,
    "ctrl_move_raise_activate": False,
    "ctrl_move_raise_exclude_self": True,
}


def default_config_path():
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent
    return base_dir / "config.json"


class ConfigManager:
    """Read and persist all settings in one editable JSON file."""

    def __init__(self, config_path=None):
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else default_config_path()
        )
        self.config = self.load_config()

    def _normalize_config(self, loaded):
        config = dict(DEFAULT_CONFIG)
        config.update(loaded)
        for name, default_value in DEFAULT_CONFIG.items():
            value = config.get(name, default_value)
            if isinstance(default_value, bool):
                if isinstance(value, bool):
                    config[name] = value
                elif value in (0, 1):
                    config[name] = bool(value)
                else:
                    config[name] = default_value
            elif isinstance(default_value, int):
                try:
                    config[name] = int(value)
                except (TypeError, ValueError):
                    config[name] = default_value
        return config

    def load_config(self):
        if not self.config_path.exists():
            config = dict(DEFAULT_CONFIG)
            self.config = config
            self.save_config()
            return config

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("配置根节点必须是 JSON 对象")
            return self._normalize_config(loaded)
        except Exception as exc:
            print("读取配置文件失败，使用默认值: {0}".format(exc))
            return dict(DEFAULT_CONFIG)

    def save_config(self):
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.config_path.with_suffix(
                self.config_path.suffix + ".tmp"
            )
            with temporary_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    self.config,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
            os.replace(temporary_path, self.config_path)
        except Exception as exc:
            print("保存配置文件失败: {0}".format(exc))

    def _get_bool(self, name):
        return bool(self.config.get(name, DEFAULT_CONFIG[name]))

    def _set_bool(self, name, enabled):
        self.config[name] = bool(enabled)
        self.save_config()

    def get_window_width(self):
        return int(self.config.get(
            "window_width",
            DEFAULT_CONFIG["window_width"],
        ))

    def get_window_height(self):
        return int(self.config.get(
            "window_height",
            DEFAULT_CONFIG["window_height"],
        ))

    def set_window_size(self, width, height):
        self.config["window_width"] = int(width)
        self.config["window_height"] = int(height)
        self.save_config()

    def get_window_offset(self):
        return int(self.config.get(
            "window_offset",
            DEFAULT_CONFIG["window_offset"],
        ))

    def set_window_offset(self, offset):
        self.config["window_offset"] = int(offset)
        self.save_config()

    def get_auto_raise_enabled(self):
        return self._get_bool("auto_raise_enabled")

    def set_auto_raise_enabled(self, enabled):
        self._set_bool("auto_raise_enabled", enabled)

    def get_auto_raise_nudge_overlap(self):
        return self._get_bool("auto_raise_nudge_overlap")

    def set_auto_raise_nudge_overlap(self, enabled):
        self._set_bool("auto_raise_nudge_overlap", enabled)

    def get_auto_raise_exclude_self(self):
        return self._get_bool("auto_raise_exclude_self")

    def set_auto_raise_exclude_self(self, enabled):
        self._set_bool("auto_raise_exclude_self", enabled)

    def get_ctrl_move_raise_enabled(self):
        return self._get_bool("ctrl_move_raise_enabled")

    def set_ctrl_move_raise_enabled(self, enabled):
        self._set_bool("ctrl_move_raise_enabled", enabled)

    def get_ctrl_move_required(self):
        return self._get_bool("ctrl_move_required")

    def set_ctrl_move_required(self, required):
        self._set_bool("ctrl_move_required", required)

    def get_ctrl_move_raise_activate(self):
        return self._get_bool("ctrl_move_raise_activate")

    def set_ctrl_move_raise_activate(self, enabled):
        self._set_bool("ctrl_move_raise_activate", enabled)

    def get_ctrl_move_raise_exclude_self(self):
        return self._get_bool("ctrl_move_raise_exclude_self")

    def set_ctrl_move_raise_exclude_self(self, enabled):
        self._set_bool("ctrl_move_raise_exclude_self", enabled)
