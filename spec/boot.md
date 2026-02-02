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

## 进一步说明
- 详细同步流程与规范见：`spec/branch_sync_playbook.md`
- 基础服务启动见：`spec/docker_base_startup.md`
