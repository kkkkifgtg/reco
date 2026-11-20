"""工单 01 验收测试（TDD 先行）：依赖声明、版本下限、torch CRT 对齐。

- reco-core 必须声明 numpy / scipy / scikit-learn 并带版本下限（架构 §12 F1 修订）。
- 当前 .venv 实际 import 到的版本必须满足声明下限。
- torch 为可插拔备选（ADR-0004）：装上时 torch/lib 必须齐备 12 个 CRT DLL
  （System32 缺 vcruntime140_1 / vcruntime140_threads / msvcp140_atomic_wait，
  实测根因——重建 venv 必须重放 CRT 对齐步骤）。
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

# 版本下限（架构 §12/F-8 评审实测版本；只设下限，不设上限）。
REQUIRED_FLOORS: dict[str, tuple[int, ...]] = {
    "numpy": (2, 5),
    "scipy": (1, 18),
    "scikit-learn": (1, 9),
}

# ADR-0004 CRT 重放清单：torch/lib 必须齐备的 12 个 CRT DLL。
CRT_DLLS = (
    "concrt140.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "vccorlib140.dll",
    "vcamp140.dll",
    "vcomp140.dll",
)


def _declared_dependencies() -> list[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def _declaration_for(deps: list[str], name: str) -> str:
    matches = [d for d in deps if d.lower().startswith(name.lower())]
    assert matches, f"reco-core 必须声明 {name}（架构 §12，工单 01）"
    assert len(matches) == 1, f"{name} 只允许声明一次，实际 {matches!r}"
    return matches[0]


def _floor_tuple(declaration: str) -> tuple[int, ...]:
    assert ">=" in declaration, f"声明必须带版本下限（>=）：{declaration!r}"
    spec = declaration.split(">=", 1)[1].strip()
    parts = []
    for chunk in spec.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    assert parts, f"无法从声明解析版本下限：{declaration!r}"
    return tuple(parts)


def _installed_version(name: str) -> tuple[int, ...]:
    module = importlib.import_module(name if name != "scikit-learn" else "sklearn")
    version = getattr(module, "__version__")
    return tuple(int(p) for p in version.split(".")[:3])


def test_reco_core_declares_scientific_stack_with_floors():
    deps = _declared_dependencies()
    for name in REQUIRED_FLOORS:
        declaration = _declaration_for(deps, name)
        floor = _floor_tuple(declaration)
        assert floor >= REQUIRED_FLOORS[name], (
            f"{name} 版本下限 {floor} 不得低于架构要求的 {REQUIRED_FLOORS[name]}"
        )


def test_declared_floors_do_not_exceed_installed_versions():
    deps = _declared_dependencies()
    for name in REQUIRED_FLOORS:
        declaration = _declaration_for(deps, name)
        floor = _floor_tuple(declaration)
        installed = _installed_version(name)
        assert installed >= floor, (
            f"{name} 实装版本 {installed} 低于声明下限 {floor}（.venv 与 pyproject 失同步）"
        )


def test_torch_optional_crt_alignment():
    try:
        torch = importlib.import_module("torch")
    except (ImportError, OSError) as exc:
        pytest.skip(f"torch 为可插拔备选，未安装或未对齐 CRT：{exc}")
    version = torch.__version__
    assert version.startswith("2.15"), f"torch 版本 {version} 不在工单 01 指定的 2.15.0+cpu 线"
    torch_lib = Path(torch.__file__).resolve().parent / "lib"
    missing = [dll for dll in CRT_DLLS if not (torch_lib / dll).is_file()]
    assert not missing, f"torch/lib 缺 CRT DLL（重建 venv 未重放 CRT 对齐）：{missing}"
