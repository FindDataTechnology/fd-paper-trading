"""打包完整性：镜像层的非 .py 文件必须进 wheel。

## 为什么需要这条

实测过的失败形态（fd-open-data-mcp）：setuptools 的 `include-package-data` 默认只收
**被 git 追踪**的文件。新建但未提交的 `declarations.yaml` 因此被静默丢出 wheel ——
本机 editable 安装读源码树，完全看不出问题；装到干净环境才 `FileNotFoundError`，
而且是在发版之后。

这条测试把"靠 VCS 追踪"换成"显式声明"，双保险：
1. `pyproject.toml` 必须显式声明镜像数据文件（删掉即红）；
2. 这些文件必须真的在模块旁边（`load_schema` 按 `__file__` 相对定位）。
"""
from __future__ import annotations

import pathlib
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _pkg_data() -> dict:
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return cfg.get("tool", {}).get("setuptools", {}).get("package-data", {})


def test_vendor_package_data_is_declared():
    data = _pkg_data()
    schemas = data.get("fd_paper_trading._vendor.schemas") or []
    assert any(p.endswith(".schema.json") for p in schemas), (
        "pyproject.toml 必须为 fd_paper_trading._vendor.schemas 声明 *.schema.json，"
        f"否则 wheel 不含契约 schema，load_schema 在干净环境必然失败。当前：{schemas}"
    )
    vendor = data.get("fd_paper_trading._vendor") or []
    assert "VENDOR.json" in vendor, (
        f"VENDOR.json 必须随包分发（镜像自解释）。当前：{vendor}"
    )


@pytest.mark.parametrize("name", ["rating.schema.json", "target-weights.schema.json",
                                  "price-series-ref.schema.json",
                                  "portfolio-intent.schema.json"])
def test_schema_files_sit_next_to_the_module(name):
    import fd_paper_trading._vendor  # noqa: F401

    import fd_paper_trading._vendor.schemas  # type: ignore[import-not-found]  # noqa: F401

    base = pathlib.Path(fd_paper_trading._vendor.__file__).parent / "schemas"
    assert (base / name).is_file(), f"{name} 不在 {base}（wheel 里会缺失）"


def test_vendor_pin_file_is_present():
    import fd_paper_trading._vendor  # noqa: F401

    base = pathlib.Path(fd_paper_trading._vendor.__file__).parent
    assert (base / "VENDOR.json").is_file()
