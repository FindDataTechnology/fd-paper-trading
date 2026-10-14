# multi-lang-readme Specification

## Purpose

约束双语 README 的文件命名（README.md + README.zh-CN.md）、互链与内容对等，保证公开仓的中英文门面对等。
## Requirements
### Requirement: Multi-language README with language switcher

The system SHALL provide a `README.md` in English as the default, and a `README_zh.md` in Chinese. Each file SHALL contain a language-switcher banner at the top with links to the other language versions (e.g. `[English](./README.md) | [中文](./README_zh.md)`). The content SHALL cover: project overview, features, tech stack, quick start (installation, running backend, running frontend), architecture overview, available commands, and how to contribute.

#### Scenario: English README renders with language switcher
- **WHEN** a user opens `README.md` on GitHub
- **THEN** the top of the file contains a language switcher with links to `README.md` and `README_zh.md`

#### Scenario: Chinese README renders with language switcher
- **WHEN** a user opens `README_zh.md` on GitHub
- **THEN** the top of the file contains a language switcher with links to `README.md` and `README_zh.md`

#### Scenario: README covers all required sections
- **WHEN** a user reads either `README.md` or `README_zh.md`
- **THEN** the document contains sections for: project overview, features, tech stack, quick start, architecture, commands, and contributing

### Requirement: Chinese README is a faithful translation

The `README_zh.md` file SHALL be a Chinese translation of the English `README.md`. Code blocks, command names, file paths, and technical identifiers SHALL remain unchanged. Section headings and prose SHALL be translated into Simplified Chinese.

#### Scenario: Code blocks are identical across languages
- **WHEN** comparing code blocks in `README.md` and `README_zh.md`
- **THEN** all code blocks, commands, and file paths are identical verbatim
