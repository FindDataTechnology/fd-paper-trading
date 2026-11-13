"""迁移与模型的漂移闸：测试建表走 `create_all`，生产建表走 alembic。

2026-11-13 实测两边**今天一致**（13 张表、列集合与空值约束零差异），但**没有任何判据**
钉住这件事。而这两条路径在代码里是完全分开的：

* `persistence/base.py::init_db` → `Base.metadata.create_all`（测试、首次启动）；
* 部署时 `uv run alembic upgrade head`（生产）。

只要有人加了模型字段却没写迁移，`create_all` 立刻把它建出来，**所有测试照常全绿**，
而线上那张表少一列——启动即 `UndefinedColumn`，或者更糟：查询静默走错分支。
反过来（迁移加了列、模型没加）则更难发现：测试根本不会碰那列。

同类实历在本仓之外的记忆里有过一次更新（库头跑在钉死的基座前面，卷上去 init 直接
CrashLoop），所以这条闸值得有。

判据三条，都只比对"能造出行为差异"的维度：

1. 表集合相等；
2. 每张表的列集合相等；
3. 每列的空值约束相等（`NOT NULL` 加在模型上而迁移没加 ⇒ 生产能写进 NULL，测试写不进）。

刻意不比索引/唯一约束/默认值：SQLite 与 PG 的表达差异大，比出来的多是噪声
（本仓 2026-08 撤过一道噪声大的闸）。这三条已经能抓住"加了字段忘了迁移"这一整类。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory) -> sqlite3.Connection:
    """在一个临时 SQLite 上把 alembic 跑到 head，返回连接。"""
    db = tmp_path_factory.mktemp("mig") / "schema.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    proc = subprocess.run(
        [str(REPO / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"alembic upgrade head 失败：\n{proc.stderr[-2000:]}"
    con = sqlite3.connect(db)
    yield con
    con.close()


def _migrated(con: sqlite3.Connection) -> dict[str, dict[str, bool]]:
    """表名 → {列名: notnull}（跳过 alembic 自己的表与 sqlite 内部表）。"""
    out: dict[str, dict[str, bool]] = {}
    for (table,) in con.execute("select name from sqlite_master where type='table'"):
        if table.startswith("sqlite_") or table == "alembic_version":
            continue
        out[table] = {row[1]: bool(row[3]) for row in con.execute(f"PRAGMA table_info({table})")}
    return out


def _models() -> dict[str, dict[str, bool]]:
    sys.path.insert(0, str(REPO / "src"))
    from fd_paper_trading.persistence import models  # noqa: F401 - 注册全部表
    from fd_paper_trading.persistence.base import Base

    return {
        t.name: {c.name: (not c.nullable) for c in t.columns}
        for t in Base.metadata.tables.values()
    }


def test_migrations_create_the_same_tables_as_the_models(migrated_db):
    live = set(_migrated(migrated_db))
    model = set(_models())
    assert live == model, (
        f"表集合不一致：只在模型里 {sorted(model - live)}；只在迁移里 {sorted(live - model)}。"
        "缺迁移会让测试（create_all）全绿而生产缺表。"
    )


def test_migrations_create_the_same_columns_as_the_models(migrated_db):
    live, model = _migrated(migrated_db), _models()
    drift = {
        t: {"只在模型": sorted(set(model[t]) - set(live[t])),
            "只在迁移": sorted(set(live[t]) - set(model[t]))}
        for t in sorted(set(live) & set(model))
        if set(live[t]) != set(model[t])
    }
    assert not drift, (
        f"列集合不一致：{drift}。"
        "加了模型字段却没写迁移 ⇒ 生产 UndefinedColumn；加了迁移却没加模型 ⇒ 那列永不被读写。"
    )


def test_migrations_match_model_nullability(migrated_db):
    live, model = _migrated(migrated_db), _models()
    drift = [
        f"{t}.{c}（迁移 NOT NULL={live[t][c]}，模型 NOT NULL={model[t][c]}）"
        for t in sorted(set(live) & set(model))
        for c in sorted(set(live[t]) & set(model[t]))
        if live[t][c] != model[t][c]
    ]
    assert not drift, (
        f"空值约束不一致：{drift}。"
        "模型上加了非空而迁移没有 ⇒ 测试写不进、生产能写进（反之亦然），"
        "这类差异只在真实数据到达时才现形。"
    )
