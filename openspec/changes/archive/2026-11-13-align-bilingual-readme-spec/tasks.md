## 1. 实测（先有读数）

- [x] 1.1 规格 7 处 `README_zh.md` vs 仓内 `README.zh-CN.md`（规格 Purpose 亦为后者）
- [x] 1.2 两份 README 的 7 个代码块逐字对比：**零对相同**（块内注释与示意图被翻译）
- [x] 1.3 只比 ```bash 命令行（去行尾注释）：**38 条全部一致** ⇒ 实现无误，规格字面过严
- [x] 1.4 `grep README tests/` 证明该 capability 此前零判据

## 2. 文档

- [x] 2.1 中文 README 补上缺失的「真实行情数据」一节（英文版在 8d39201 重写过，中文漏了；
      补齐后两侧围栏块数量相等 14/14）

## 3. 规格

- [x] 3.1 MODIFIED `Multi-language README with language switcher`：文件名改 `README.zh-CN.md`
- [x] 3.2 MODIFIED `Chinese README is a faithful translation`：命令/路径/标识符逐字一致，
      注释与示意图 MAY 翻译；写明判据形状
- [x] 3.3 `openspec validate align-bilingual-readme-spec --strict`
- [x] 3.4 归档并对账；归档后 `openspec validate multi-lang-readme --type spec --strict`

## 4. 判据

- [x] 4.1 `tests/unit/test_bilingual_readme.py`（13 条）
- [x] 4.2 注入对照四项（命令被翻译 / 删一节 / 切换器写错名 / 篇末提旧名）均红，逐字节还原
