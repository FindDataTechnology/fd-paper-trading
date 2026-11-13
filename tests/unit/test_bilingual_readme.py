"""`multi-lang-readme` 的判据：双语 README 的文件名、互链、结构对等、命令一致。

2026-11-13 实测这份规格**零判据**，而它有两处与事实相反：

1. 规格在 7 处要求中文文件名为 `README_zh.md`，仓里是 `README.zh-CN.md`
   （规格自己的 Purpose 写的也是后者）——照规格实现/校验的人会去找一个不存在的文件。
2. 「Code blocks, command names, file paths, and technical identifiers SHALL remain
   unchanged」+ 场景「all code blocks … identical verbatim」——而两份文档的代码块
   **一个都不逐字相同**：块内 shell 注释被翻译、那张框线示意图里的标签也被翻译。

第 2 条我按"它想守的是什么"改了规格（见 change `align-bilingual-readme-spec`）：
真正会伤人的是**命令漂移**（`npm run build` 被译成 `npm run 构建`、路径被改），
而不是块内注释用哪种语言。所以判据定成——

* ```bash 块**逐条命令**（去掉行尾注释后）必须完全一致；
* 两侧围栏块**数量**必须一致（结构对等，防止一边多/少一整节）；
* 每份文件顶部有指向**两个真实文件名**的切换器；
* 双语必需章节都在（用成对的关键词判定，不硬编码标题字数）。

刻意不要求"逐字相同"：那会把译者的注释与示意图翻译判成缺陷，而它们不是。
判据要盯**可执行的那一层**——这与 `.env.example` 那条的取舍是同一个道理。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EN = REPO / "README.md"
ZH = REPO / "README.zh-CN.md"

#: 规格曾写成 README_zh.md；真名是 README.zh-CN.md（规格 Purpose 亦然）。
WRONG_NAME = "README_zh.md"


def _bash_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```bash[^\n]*\n(.*?)```", text, re.S)


def _commands(path: Path) -> Counter[str]:
    """所有 ```bash 块里的命令行（去空行、去注释、去行尾注释）。"""
    out: list[str] = []
    for block in _bash_blocks(path):
        for line in block.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            s = re.split(r"\s{2,}#", s)[0].strip()  # 去掉「命令   # 注释」
            out.append(s)
    return Counter(out)


def _fence_count(path: Path) -> int:
    return len(re.findall(r"^```", path.read_text(encoding="utf-8"), re.M))


def test_both_readmes_exist_under_the_real_names():
    assert EN.is_file(), "缺英文 README.md"
    assert ZH.is_file(), f"缺中文 README（应为 {ZH.name}）"


def test_no_doc_points_at_the_nonexistent_filename():
    """`README_zh.md` 这个文件名不存在，任何文档都不该再提它。

    规格侧已改（`align-bilingual-readme-spec`）；这一条守的是"别再被写回来"。
    """
    offenders = [
        f"{p.name}"
        for p in [EN, ZH, REPO / "CLAUDE.md", REPO / ".env.example"]
        if p.is_file() and WRONG_NAME in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"{offenders} 仍在用不存在的文件名 {WRONG_NAME}"


@pytest.mark.parametrize("path", [EN, ZH], ids=["en", "zh"])
def test_language_switcher_links_to_both_real_files(path: Path):
    head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
    assert "./README.md" in head, f"{path.name} 顶部没有指向 README.md 的链接"
    assert "./README.zh-CN.md" in head, f"{path.name} 顶部没有指向 README.zh-CN.md 的链接"


def test_structure_parity_fenced_block_count():
    """一侧多一整节会让块数不等——这是"内容对等"最省事也最可靠的代理判据。"""
    en, zh = _fence_count(EN), _fence_count(ZH)
    assert en == zh, f"围栏块数不等：README.md={en}, README.zh-CN.md={zh}（有一侧漏了整节？）"


def test_commands_are_identical_across_languages():
    """命令、路径、标识符必须一致——块内注释可以翻译，命令不行。"""
    en, zh = _commands(EN), _commands(ZH)
    only_en = en - zh
    only_zh = zh - en
    assert not only_en and not only_zh, (
        "两份 README 的 ```bash 命令不一致：\n"
        f"  只在英文：{dict(only_en)}\n"
        f"  只在中文：{dict(only_zh)}\n"
        "命令与路径必须逐字一致（可以翻译的是注释）。"
    )


#: 规格要求覆盖的章节，用成对关键词判定（不锁死标题原文，允许标题润色）。
REQUIRED_SECTIONS = [
    (("feature", "功能"), "features / 功能特性"),
    (("tech stack", "技术栈"), "tech stack / 技术栈"),
    (("quick start", "快速开始"), "quick start / 快速开始"),
    (("architecture", "架构"), "architecture / 架构"),
    (("command", "命令"), "commands / 常用命令"),
    (("contribut", "贡献"), "contributing / 参与贡献"),
]


@pytest.mark.parametrize("keywords,label", REQUIRED_SECTIONS, ids=[s[1] for s in REQUIRED_SECTIONS])
def test_required_sections_present_in_both(keywords: tuple[str, str], label: str):
    en = EN.read_text(encoding="utf-8").lower()
    zh = ZH.read_text(encoding="utf-8")
    en_hit = keywords[0] in en
    zh_hit = keywords[1] in zh
    assert en_hit and zh_hit, f"章节缺失（{label}）：英文={en_hit}, 中文={zh_hit}"


def test_the_guards_can_go_red():
    """纯函数自证：命令比较对"命令被翻译/被改写"敏感，对"注释被翻译"不敏感。"""
    def cmds(text: str) -> Counter[str]:
        out = []
        for block in re.findall(r"```bash[^\n]*\n(.*?)```", text, re.S):
            for line in block.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                out.append(re.split(r"\s{2,}#", s)[0].strip())
        return Counter(out)

    en = "```bash\nuv run pytest   # run tests\n```\n"
    translated_comment = "```bash\nuv run pytest   # 运行测试\n```\n"
    translated_command = "```bash\nuv 运行 pytest\n```\n"

    assert not (cmds(en) - cmds(translated_comment)), "翻译注释不该被判成不一致"
    assert cmds(en) - cmds(translated_command), "命令被翻译必须被判成不一致"
