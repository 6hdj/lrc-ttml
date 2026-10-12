"""用户配置（``settings.json``）。

设计约束：

* **原子写** —— 配置写坏 = 软件打不开，不能忍。
* **读坏文件不崩** —— 手改坏了 / 断电写了一半，回退到默认值并**留一份 .bak**，
  绝不让一次 JSON 解析失败把整个程序拦住。
* **没有未知字段就丢** —— 不在白名单里的键原样保留（用户在别处加了注释字段，
  不该被我们静默删掉）。
* **不导入 Qt**。

字段命名与 CLI / Web 参数保持一致（见 ``desktop.services.convert_params``），
这样"界面设置"和"命令行长参数"是同一套名字，不会出现两套词汇。
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, Optional

try:
    from desktop import paths
except ImportError:                                   # 单文件直接跑
    import paths                                      # type: ignore

# 默认值（也是"哪些键是我们管的"的白名单）
DEFAULTS: Dict[str, Any] = {
    # ---- 界面 ----
    "theme": "dark",                 # dark / light
    "first_run_done": False,
    "open_editor_in_browser": True,  # 编辑台用系统浏览器打开（零额外体积）
    # ---- 上次用过的转换参数（记住用户的选择）----
    "language": "auto",
    "model": "large-v3",
    "mode": "amll",
    "markers": "agent",
    "singer_mode": "off",
    "line_id": "both",
    "time_format": "clock",
    "translation_style": "head",
    "style": "keep",
    "explicit_policy": "keep",
    "separate_vocals": False,
    "refine": True,
    "chunked": True,
    "skip_credits": True,
    "extend_tails": True,
    "verify_amll": True,
    "demucs_model": "htdemucs",
    "pause_space": 0.0,
    # ---- 界面里填过、下次还想要的元数据 ----
    "artist": "",
    "album": "",
    "author": "",
    "author_id": "",
    "songwriters": "",
    # ---- 目录 ----
    "workspace": "",                 # 空 = 用 paths.default_workspace()
    "last_input_dir": "",
    "last_output_dir": "",
    # ---- 模型 ----
    "asr_model": "large-v3",
    "download_policy": "on_demand",  # on_demand / first_run
}


class Config:
    """配置对象。``get``/``set`` 都是浅层的（够用且好懂）。"""

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        self.path = path or paths.config_path()
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self.loaded_from: str = "defaults"
        self.load_error: str = ""
        if data is not None:
            self._data.update(data)
            self.loaded_from = "memory"
        else:
            self.load()

    # ------------------------------------------------------------- 读
    def load(self) -> "Config":
        if not os.path.isfile(self.path):
            self.loaded_from = "defaults"
            return self
        try:
            with open(self.path, encoding="utf-8") as fh:
                disk = json.load(fh)
            if not isinstance(disk, dict):
                raise ValueError("配置文件根节点不是对象")
            # 未知键保留：用户/未来版本写的字段不该被我们抹掉
            self._data.update(disk)
            self.loaded_from = "disk"
        except Exception as e:                        # noqa: BLE001
            self.load_error = f"{type(e).__name__}: {e}"
            self.loaded_from = "defaults(after-error)"
            # 留一份坏文件，用户/我们都能查
            try:
                shutil.copy2(self.path, self.path + ".bak")
            except OSError:
                pass
        return self

    # ------------------------------------------------------------- 写
    def as_dict(self) -> dict:
        return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, mapping: dict) -> None:
        self._data.update(mapping or {})

    def save(self) -> str:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        paths.ensure_dir(os.path.dirname(os.path.abspath(self.path)))
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        os.replace(tmp, self.path)
        return self.path

    # ------------------------------------------------------------- 便捷
    def workspace(self) -> str:
        return paths.workspace_dir(self._data)

    def outputs_dir(self) -> str:
        return paths.outputs_dir(self._data)

    def uploads_dir(self) -> str:
        return paths.uploads_dir(self._data)

    def to_convert_params(self, **overrides) -> dict:
        """把配置里的转换参数抽成一份 params 字典（给 ConvertOptions 用）。"""
        keys = [
            "language", "model", "mode", "markers", "singer_mode", "line_id",
            "time_format", "translation_style", "style", "explicit_policy",
            "separate_vocals", "refine", "chunked", "skip_credits",
            "extend_tails", "verify_amll", "demucs_model", "pause_space",
        ]
        p = {k: self._data.get(k, DEFAULTS.get(k)) for k in keys}
        p.update(overrides or {})
        return p


_SINGLETON: Optional[Config] = None


def current() -> Config:
    """进程内共享的配置对象（懒加载 + 单例）。"""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = Config()
    return _SINGLETON


def reload() -> Config:
    global _SINGLETON
    _SINGLETON = Config()
    return _SINGLETON


def reset_for_tests(path: Optional[str] = None) -> Config:
    global _SINGLETON
    _SINGLETON = Config(path=path, data={} if path is None else None)
    return _SINGLETON
