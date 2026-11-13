## Why

`multi-lang-readme` 这份规格两处与事实相反，且整份**零判据**（`grep README tests/` 只命中
`test_env_example_is_executable.py` 的注释）。

1. **文件名不存在**：规格 7 处要求中文文件为 `README_zh.md`，而仓里是
   `README.zh-CN.md`——规格**自己的 Purpose** 写的也是 `README.zh-CN.md`。三份 README 互相
   链接用的是真名。照规格实现或校验的人会去找一个不存在的文件。
2. **"逐字相同"不成立且不该成立**：规格写「Code blocks, command names, file paths, and
   technical identifiers SHALL remain unchanged」，场景写「all code blocks … identical
   verbatim」。而实测（2026-11-13）两份 README 的 7 个代码块**没有一对逐字相同**：
   块内 shell 注释被译成中文，那张框线架构/界面示意图里的标签也被译了。
   但同时——**38 条命令完全一致**（含路径与标识符）。

   也就是说：实现的"精神"是对的，"字面"是错的。真正会伤人的是**命令漂移**（`npm run build`
   被译成 `npm run 构建`、路径被改），而不是块内注释用哪种语言。把注释翻译判成缺陷，
   等于为了让规格字面成立而降低中文文档质量。

## What Changes

- **MODIFIED**（multi-lang-readme / Multi-language README with language switcher）：
  `README_zh.md` → `README.zh-CN.md`（正文与场景条件里的文件名纠正；**场景名逐字保留**）。
- **MODIFIED**（multi-lang-readme / Chinese README is a faithful translation）：
  把"代码块逐字相同"改成可执行且更准确的口径——**命令、文件路径、技术标识符 SHALL 逐字一致；
  块内注释与示意图标签 MAY 翻译**；并写明判据形状（```bash 块的命令行逐个比对，
  去掉行尾注释；围栏块数量相等）。
- 新增判据 `tests/unit/test_bilingual_readme.py`（13 条）：真文件名存在、四份文档不再提
  `README_zh.md`、顶部切换器指向两个真名、围栏块数量相等、```bash 命令行逐个一致、
  六个必需章节双语齐备，并以纯函数自证"翻译注释不红、翻译命令必红"。

## 不做什么

不把中文 README 的注释/示意图回译成英文。那是译者有意为之的本地化，判据不该逼它退化；
要修的是规格的字面，让它描述真正重要的那条不变式。
