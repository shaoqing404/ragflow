# sync-upstream: 从官方上游合并更新到 three_u_0231

## 前置条件
执行前必须阅读 `spec/branch_sync_playbook.md` 获取完整规范。

## 规则（不可违反）
- **绝不 push 到 origin**（origin 是 READ-ONLY 的 GitHub fork）
- **绝不 force-push**，除非用户明确批准
- 冲突解决时**优先保留 3U 逻辑**；不确定时必须人工确认
- 每步完成后用 `git status -sb` 和 `git log --oneline -5` 验证

## 执行步骤

### 1. 预检
```bash
git checkout three_u_0231
git status -sb
```
- 工作区必须干净。如有未提交改动，提示用户先 commit 或 stash。

### 2. 拉取上游
```bash
git fetch origin
```

### 3. 检查差异
```bash
git log --oneline three_u_0231..origin/main | head -20
```
- 如果无输出，说明已是最新，报告后结束。
- 如果有输出，列出更新摘要，继续下一步。

### 4. 合并
```bash
git merge origin/main
```

### 5. 冲突处理（如有）
1. `git status` 列出冲突文件
2. 逐文件解决，保留 3U 改动
3. 如涉及 `docker/.env`、`service_conf.yaml`、`rag/app/naive.py`、`rag/svr/task_executor.py` 等 3U 关键文件 → 参考 `spec/strategy-a-sync/config_merge_guide.md`
4. 不确定的冲突 → 暂停并询问用户
5. `git add <resolved-files>` → `git commit`

### 6. 验证
```bash
git status -sb
git log --oneline -5
```

### 7. 记录合并日志
在 `spec/branch_sync_logs/` 下按日期创建 `YYYY-MM-DD/merge_log.md`，记录：
- 合并日期、分支、源、merge commit hash
- 冲突文件及解决方式
- 变更文件列表

### 8. 推送到 Gitee（需用户确认）
```bash
git push gitee three_u_0231
```
- 此步骤必须等用户明确批准后才执行。
