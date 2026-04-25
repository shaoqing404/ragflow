# Merge Log - 2026-04-25

- **Branch**: three_u_0240
- **Source**: origin/main (via HTTPS: https://github.com/shaoqing404/ragflow.git)
- **Merge commit**: 75a2ae209
- **Upstream HEAD**: fb95136f3 (Fix: validate URL scheme and resolved IP before crawling to prevent SSRF #14090)

## Conflicts Resolved (5 files)

| File | Resolution Strategy |
|------|---------------------|
| `api/apps/sdk/session.py` | Kept HEAD — 3U trace support in agent_completions, defensive checks in list_agent_session/delete |
| `api/db/db_models.py` | Used upstream — more complete email index fix (drops non-unique index before recreating) |
| `api/db/services/dialog_service.py` | Merged — kept 3U trace_turn_id + **kwargs, adopted upstream _should_use_web_search |
| `docker/.env` | Kept HEAD — 3U MYSQL_PORT, GRAPH_DOCSTORE, ANTHROPIC_PROMPT_CACHING settings |
| `web/src/hooks/use-chat-request.ts` | Kept HEAD — 3U sanitizeDialogPayload + setDialog logic |

## Notable Upstream Changes (30+ commits)

- SSRF prevention in URL crawling
- OpenDataLoader PDF parser backend
- DeepSeek v4 model support
- Docling parsing via native chunking endpoints
- REST API refactoring (agent webhooks, user, chunk, system APIs)
- Agent API additions
- Langfuse integration migration (start_generation → start_observation)
- Minimum type check for pipeline
- Azure blob put method fix
- Title chunk optimization
- Go: gitee and siliconflow model providers

## Notes

- No force-push used
- Frontend ESLint errors pre-existing (upstream), not introduced by merge
- SSH to GitHub was down; fetched via HTTPS instead
