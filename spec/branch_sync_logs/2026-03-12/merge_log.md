# Branch Sync Log - 2026-03-12

## Summary
- Merge date: 2026-03-12
- Branch: three_u_0231
- Source: origin/main
- Merge commit: a1d6c1129
- three_u_0231 parent: 00af05bbd
- origin/main parent: e78938c72
- Upstream commits merged: 114
- Conflicts: none (auto-merged)

## Merge Command
```
git fetch origin
git merge origin/main
```

## 3U Key Files Verification
All 3U customizations preserved after auto-merge:
- `rag/app/naive.py` — `by_three_u()` function and `PARSERS["three_u"]` intact
- `rag/svr/task_executor.py` — `mel` / `3u_mel` FACTORY entries intact
- `deepdoc/parser/pdf_parser.py` — page_number logic intact
- `deepdoc/vision/layout_recognizer.py` — auto-merged, no conflict

## Notable Upstream Changes (highlights from 114 commits)
- Admin CLI & user management system (login, permissions, license)
- Arabic language support
- vLLM reasoning field support
- MinerU coordinate fix
- UI refactoring (settings, knowledge graph, metadata)
- Confluence performance optimization
- Deadlock retry fix
- Go admin server port update (9383)

## Notes
- No config snapshots needed (no conflicts)
- Pending: push to gitee after user approval
