"""`.env.example` 是唯一一份"怎么把它跑起来"的说明——它必须是可执行的。

2026-11-13 实测它有两处**照着做就会坏**：

1. 第 42 行是一行**未注释**的 `MARKET_DATA_PROVIDER=simulated`。
   而 `MARKET_DATA_PROVIDER` 是 `selection.LEGACY_ENV_VARS` 里的旧选择器，
   兼容映射已在 task 6.4 删除 ⇒ 进程里只要还有它就**启动即失败**。
   也就是说：`cp .env.example .env` 这一步就直接把服务启动打断了。
   （这不是假设：`tests/conftest.py` 有一个 session 级 autouse fixture 专门剥掉这几个
   变量，注释写着"本轮实测本机 `.env` 里就有 MARKET_DATA_PROVIDER=polygon`，
   于是 MCP 那批用例全 ERROR"。当时修的是测试，不是文档。）
2. `PER_USER_SYMBOL_LIMIT=20` 同样未注释，而它全仓零执行点（用户限额早已统一到
   组配置）——一个设了不生效、又看起来在生效的旋钮。

还有一处不是"错"而是"过期"：整段 CopilotKit 配置（含一个已不存在的
`web/copilot-runtime/` 目录）在 2026-10-14 commit `d3dec0b` 里被有意移除
（"the platform pack's persona replaces it"），文档没跟着改。

判据分两条：

1. **旧选择器一个都不许出现**（连注释里都不行——这条是硬红线，出现即启动失败）。
2. **每个未注释的键都必须有消费者**：映射到 `Settings` 字段，或在显式声明的
   白名单里（白名单每条都要写清消费者是谁）。

第 2 条刻意不反过来要求"每个 Settings 字段都在文档里"：那会逼着人给内部调参项
写文档，噪声大于收益。这里要防的是相反的方向——**文档教人设的东西没人读**。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / ".env.example"
CONFIG = REPO / "src" / "fd_paper_trading" / "config.py"

#: 会被读者逐行照抄的文档。`.env.example` 是配置模板，README/CLAUDE 里的命令块是
#: 运行指令——三者都是"可执行文档"，同一个判据都适用（2026-11-13：README 那两行
#: `MARKET_DATA_PROVIDER=fd-open-data uv run fd-paper-trading` 与 CLAUDE.md 那条
#: "set MARKET_DATA_PROVIDER=…" 和 .env.example 是同一个错，一起修）。
EXECUTABLE_DOCS = (EXAMPLE, REPO / "README.md", REPO / "CLAUDE.md")

#: 旧选择器：出现即启动失败（selection.LEGACY_ENV_VARS）。
LEGACY_SELECTORS = ("MARKET_DATA_PROVIDER", "ASTOCK_SOURCE", "ASTOCK_CALENDAR_REFS")

#: 未进 `Settings` 但确实有消费者、且消费点在别处的键。
#: 每条都必须写明"谁在读"——这张表存在的意义是让例外成为一条被记录的事实。
CONSUMED_ELSEWHERE = {
    # 由 MCP / 平台侧注入的凭据，本服务只读取它作为 HTTP bearer
    # （mcp/server.py `_resolve_credential`）。
    "PAPER_TRADING_API_TOKEN": "mcp/server.py:46 _resolve_credential",
    "PAPER_TRADING_API_URL": "mcp/server.py 的 API base",
    # 前端构建期由部署注入（不是后端 Settings）。
    "FD_WEB_DIST": "api/app.py 的静态目录解析",
}


def _settings_fields() -> set[str]:
    fields: set[str] = set()
    for node in ast.walk(ast.parse(CONFIG.read_text())):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    fields.add(st.target.id.upper())
    return fields


def _example_lines() -> list[tuple[int, str]]:
    return list(enumerate(EXAMPLE.read_text().splitlines(), 1))


#: 本仓**自己会跑**的脚本/配置：它们的赋值不是给人抄的，是机器照着读的。
#: 2026-11-14 的实历：`web/e2e/run-backend.sh` 里 `export MARKET_DATA_PROVIDER=simulated`
#: 让后端**拒绝启动**，Playwright 的 e2e job 从 6.4 起就没跑成过一次——而这条判据
#: 当时只扫三份文档，脚本在射程之外。判据扫的是"赋值"，注释里提到名字不算。
_RUNTIME_GLOBS = (
    "web/e2e/*.sh",
    ".github/workflows/*.yml",
    "scripts/*.sh",
    "Dockerfile",
    "entrypoint.sh",
    "deploy/**/*.yaml",
    "deploy/**/*.yml",
)


def _runtime_scripts() -> list[Path]:
    files: list[Path] = []
    for pattern in _RUNTIME_GLOBS:
        files.extend(sorted(p for p in REPO.glob(pattern) if p.is_file()))
    return files


def test_no_legacy_selector_appears_as_an_inline_assignment():
    """旧选择器不许以**行内赋值**出现在可执行文档里（注释掉的也算）。

    判据刻意收窄到"赋值"（`NAME=…`、`# NAME=…`、或命令里的 `NAME=value cmd`），
    不是"名字不许出现"：文档必须能说"这几个名字已删除、别再设"，那需要写出名字；
    真正有害的是**可抄的赋值**——读者照抄就得到一个启动即失败的配置。
    第一版写成"名字出现即命中"，当场把我自己那句"旧选择器 X / Y / Z 已删除"也判红：
    那是把判据的松紧搞错了，不是把文档写错。
    """
    # 行首赋值（`NAME=` / `# NAME=`）或行内赋值（`NAME=value` 出现在句子/命令中间）
    assignments = [
        (name, re.compile(rf"(?:^|\s|`)#?\s*{name}\s*="))
        for name in LEGACY_SELECTORS
    ]
    hits = [
        f"{path.relative_to(REPO)}:{lineno} {name}"
        for path in [*EXECUTABLE_DOCS, *_runtime_scripts()]
        for lineno, line in enumerate(path.read_text().splitlines(), 1)
        for name, pat in assignments
        if pat.search(line)
    ]
    assert not hits, (
        f"{hits} —— 这些是已删除的旧选择器，写成赋值就是给人抄的。"
        "选择器只有 MARKET_DATA_SOURCE_ID；把赋值删掉（说明文字可以保留）。"
    )


def test_every_documented_key_has_a_consumer():
    """未注释的键必须真的被读：要么是 Settings 字段，要么在白名单里写明消费者。"""
    fields = _settings_fields()
    live = [
        (lineno, m.group(1))
        for lineno, line in _example_lines()
        if (m := re.match(r"^([A-Z][A-Z0-9_]*)=", line))
    ]
    assert live, "没解析到任何未注释的键——解析规则失效了，这条判据等于没跑"

    unread = [
        f".env.example:{lineno} {key}"
        for lineno, key in live
        if key not in fields and key not in CONSUMED_ELSEWHERE
    ]
    assert not unread, (
        f"{unread} —— 文档让人设这些变量，但本仓没有任何消费者读它们。"
        "要么删掉这一行，要么把它加进 CONSUMED_ELSEWHERE 并写清谁在读。"
    )


def test_the_guards_can_go_red():
    """两条判据都要能红——用照抄来的坏形状验，不碰真文件。"""
    assert "MARKET_DATA_PROVIDER" in "MARKET_DATA_PROVIDER=simulated"
    # 判据 1 的锚点：旧名字只要出现（哪怕在注释里）就该命中
    line = "# MARKET_DATA_PROVIDER=polygon  (旧写法)"
    assert any(name in line for name in LEGACY_SELECTORS)

    # 判据 2 的锚点：未注释的键若既不在 Settings 也不在白名单，必须被点名
    fields = _settings_fields()
    key = "PER_USER_SYMBOL_LIMIT"
    assert key not in fields and key not in CONSUMED_ELSEWHERE
