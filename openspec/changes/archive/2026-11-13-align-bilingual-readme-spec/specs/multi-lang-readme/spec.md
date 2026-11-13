# multi-lang-readme Delta

## MODIFIED Requirements

### Requirement: Multi-language README with language switcher

The system SHALL provide a `README.md` in English as the default, and a `README.zh-CN.md` in Chinese. Each file SHALL contain a language-switcher banner at the top with links to the other language versions (e.g. `[English](./README.md) · [简体中文](./README.zh-CN.md)`). The content SHALL cover: project overview, features, tech stack, quick start (installation, running backend, running frontend), architecture overview, available commands, and how to contribute.

#### Scenario: English README renders with language switcher
- **WHEN** a user opens `README.md` on GitHub
- **THEN** the top of the file contains a language switcher with links to `README.md` and `README.zh-CN.md`

#### Scenario: Chinese README renders with language switcher
- **WHEN** a user opens `README.zh-CN.md` on GitHub
- **THEN** the top of the file contains a language switcher with links to `README.md` and `README.zh-CN.md`

#### Scenario: README covers all required sections
- **WHEN** a user reads either `README.md` or `README.zh-CN.md`
- **THEN** the document contains sections for: project overview, features, tech stack, quick start, architecture, commands, and contributing

### Requirement: Chinese README is a faithful translation

The `README.zh-CN.md` file SHALL be a Chinese translation of the English `README.md`. **Commands, file paths, and technical identifiers SHALL remain byte-identical between the two files**; comments inside code blocks and labels inside ASCII diagrams MAY be translated, because they are prose about the code rather than the code itself. Section headings and prose SHALL be translated into Simplified Chinese. The enforceable shape: for every `bash` code block, the command lines (trailing `#` comments removed) SHALL match one-for-one across the two files, and both files SHALL carry the same number of fenced code blocks, so that a whole missing section cannot pass unnoticed.

#### Scenario: Code blocks are identical across languages
- **WHEN** comparing code blocks in `README.md` and `README.zh-CN.md`
- **THEN** every command, file path, and technical identifier is byte-identical across the two files (comments and diagram labels may differ by language), and the two files contain the same number of fenced code blocks
