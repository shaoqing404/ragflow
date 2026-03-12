# BOOT: RAGFlow + three_u_0231

本文件用于“先读这一页再看其他文档/代码”。
请注意：本仓库不是官方原版，而是官方 fork 的**中间改造分支**，用于 3U 魔改后发布到 Gitee 的部署链路。

## 项目是什么
RAGFlow 是一个开源 RAG（检索增强生成）引擎，包含：
- 后端（Python 3.10+，Flask/Quart）
- 前端（React + TypeScript + UmiJS）
- 多服务架构（Docker 组件与依赖）

## 本分支的定位（非常重要）
- **官方上游**：`github.com/infiniflow/ragflow`
- **GitHub fork**：`github.com/shaoqing404/ragflow`（自动跟随官方）
- **本地开发分支**：`three_u_0231`
  - 承载 3U 相关的定制与魔改
  - 以“合并上游更新 + 保留 3U 改动”为目标
- **部署目标**：`gitee.com/GFCM/ragflow`
  - 只向 Gitee 推送，用于服务器部署

## 给 AI/新维护者的提醒
- 不要把当前分支当作“官方原版”。
- 阅读/修改时必须考虑 3U 改动与上游差异。
- 同步上游时优先保留 3U 逻辑；冲突不确定时必须人工确认。
- 本分支是“官方 fork → 3U 魔改 → Gitee 部署”的**中间分支**。

## 阅读路径

读完本文件后，按以下顺序阅读：

### 必读
1. **`spec/branch_sync_playbook.md`** — 分支同步工作流、远程配置、冲突解决规范（**核心运维文档**）
2. **`spec/docker_base_startup.md`** — 本地开发环境的 Docker 基础服务启动

### 选读（按需查阅）
`spec/strategy-a-sync/` 目录是 3U MEL 迁移的历史存档，**仅在处理相关问题时再读**，不要一次性全部加载：

| 文件 | 何时读 |
|------|--------|
| `migration_summary.md` | 需要了解 3U MEL 迁移全貌时 |
| `env_diff_report.md` | 需要对照环境变量差异时 |
| `config_merge_guide.md` | 需要合并 service_conf / docker/.env 时 |
| `core_issues_analysis.md` | 诊断 MinerU 接入或 MEL 解析故障时 |
| `page_index_bug_report.md` | 遇到 DeepDoc 页码越界错误时 |
| `task_deepdoc_bounds_fix.md` | 跟踪 DeepDoc 边界修复进展时 |
| `task_mineru_integration_comparison.md` | 对比 MinerU 跨版本接入机制时 |

### 参考
- **`spec/branch_sync_logs/`** — 历史合并日志（按日期归档）
- **`spec/tmp/`** — Agent 提示词模版草稿，与主流程无关，无需主动阅读

## Skills（围绕本项目的常用任务）

以下是本项目中反复出现的操作模式，AI 或维护者可直接按名称调用：

### skill: sync-upstream
**目的**：从 origin/main 合并官方更新到 three_u_0231
**前置**：阅读 `spec/branch_sync_playbook.md`
**步骤摘要**：
1. `git status -sb` 确认工作区干净
2. `git fetch origin` → `git merge origin/main`
3. 冲突解决（优先保留 3U 逻辑）
4. `git push gitee three_u_0231`
**注意**：绝不 push 到 origin；force-push 需人工批准

### skill: diagnose-3u-mel
**目的**：诊断 3U MEL 解析管线故障
**前置**：阅读 `spec/strategy-a-sync/core_issues_analysis.md`
**检查清单**：
1. MinerU 是否在数据库注册（`llm_factory='MinerU'`）
2. `parser_config.layout_recognize` 是否为 `"mineru"`
3. DeepDoc 回退路径是否触发页码越界
4. 前端 ChunkMethodForm 组件是否正常加载

### skill: migrate-config
**目的**：将 3U 专属配置同步到新版本的 docker/.env 和 service_conf.yaml
**前置**：阅读 `spec/strategy-a-sync/env_diff_report.md` + `config_merge_guide.md`
**关键项**：3U MEL 环境变量、MinerU VLM 配置、parsers 字段注册、init_data.py 的 parser_ids

### skill: project-report
**目的**：生成项目完整活动报告
**前置**：按上方"阅读路径"顺序读完所有 spec 文件
**输出**：项目定位、技术栈、改造内容、已知问题、待办事项的结构化汇报
