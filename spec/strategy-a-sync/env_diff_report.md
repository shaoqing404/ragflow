# Environment Variable Diff Report

## Overview

This document compares environment variables between:
- **Source**: `d:\element_WorkSpeac\ragflow_worktrees\strategy-a-sync\docker\.env` (291 lines)
- **Target**: `E:\element_workspace\ragflow\docker\.env` (258 lines)

---

## 1. 3U MEL Configuration (New in Source)

Add these variables to target `docker/.env`:

```bash
# ============================================================
#  FTMS 3U MEL Enhancement Configuration
#  模式说明（依据 three_u_mel_navigation_parser.py 实际逻辑）:
#  - 生产全量：ENABLE_FULL_TEST=true，留空 THREE_U_MEL_PAGE_RANGE
#  - 小范围测试：ENABLE_FULL_TEST=false；设置 THREE_U_MEL_TEST_LIMIT=N
#  - 定向全量：ENABLE_FULL_TEST=true；设置 THREE_U_MEL_PAGE_RANGE=start,end
# ============================================================
ENABLE_FULL_TEST=true
THREE_U_MEL_PAGE_RANGE=
# THREE_U_MEL_TEST_LIMIT=300
THREE_U_MEL_CHUNK_TOKEN_NUM=768
```

---

## 2. Task Executor Performance Configuration (New in Source)

Add these variables for performance tuning:

```bash
# ============================================================
#  Task Executor Performance Configuration
# ============================================================
WS=1
MAX_CONCURRENT_TASKS=2
MAX_CONCURRENT_CHUNK_BUILDERS=2
MAX_CONCURRENT_MINIO=20
MAX_CONCURRENT_CHATS=200
```

---

## 3. MinerU VLM Configuration (New in Source)

Add these variables for MinerU integration:

```bash
# ============================================================
#  MinerU VLM Enhancement Configuration
# ============================================================
USE_MINERU=true
MINERU_APISERVER="http://your-mineru-server:8000"
MINERU_BACKEND="vlm-http-client"
# MINERU_EXECUTABLE=".venv/bins/tsdmineru"  # Optional: local executable path
```

---

## 4. FTMS VLM Configuration (New in Source)

Add these variables for FTMS VLM enhancement (optional, disabled by default):

```bash
# ============================================================
#  FTMS VLM Enhancement Configuration
# ============================================================
FTMS_VLM_ENABLED=false
FTMS_QWEN_MODEL_API_KEY=your-api-key
FTMS_QWEN_MODEL_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
FTMS_VLM_MODEL_NAME=qwen-vl-max
FTMS_VLM_MAX_CONCURRENCY=2
FTMS_VLM_MAX_PAGE_IMAGES=3
FTMS_VLM_PAGE_GAP=24
FTMS_VLM_MAX_WIDTH=2048
FTMS_VLM_MAX_TOKENS=12000
FTMS_VLM_FALLBACK_ENABLED=true
FTMS_VLM_FALLBACK_API_KEY=your-fallback-api-key
FTMS_VLM_FALLBACK_MODEL=qwen-vl-max
FTMS_VLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
FTMS_VLM_FALLBACK_PROVIDER=dashscope
FTMS_VLM_FALLBACK_MAX_TOKENS=12000
```

---

## 5. Variables Present in Target but NOT in Source

These are **new in v0.23.1** and should be retained:

| Variable | Purpose |
|----------|---------|
| `OCEANBASE_*` | OceanBase vector DB support |
| `OB_*` | OceanBase container config |
| `SVR_MCP_PORT` | MCP server port |
| `USE_DOCLING` | DocLing parser support |
| `RAGFLOW_CRYPTO_*` | Encryption configuration |

---

## 6. service_conf.yaml Diff

### Source has (add to target under `user_default_llm`):
```yaml
user_default_llm:
  parsers: "naive:General,qa:Q&A,resume:Resume,manual:Manual,table:Table,paper:Paper,book:Book,laws:Laws,presentation:Presentation,picture:Picture,one:One,audio:Audio,email:Email,tag:Tag,mel:CA_MEL,3u_mel:3U_MEL"
```

> [!IMPORTANT]
> **Frontend Parser Registration**
> The parser dropdown list in the RAGFlow UI comes from the `parsers` string in `service_conf.yaml`, NOT from frontend code. Add `mel:CA_MEL,3u_mel:3U_MEL` to your `parsers` string for the 3U MEL options to appear in the UI.

### Target has but source doesn't:
```yaml
minio:
  bucket: ''
  prefix_path: ''
redis:
  username: ''
```

---

## Recommended Action

1. **Copy the 3U MEL, Task Executor, MinerU, and FTMS VLM blocks** from section 1-4 above to your target `docker/.env`
2. **Add the `parsers` field** to `conf/service_conf.yaml` under `user_default_llm` as shown in section 6
3. **Keep all v0.23.1 new variables** (OceanBase, MCP, Crypto, etc.)
4. **Update password values** as appropriate for your environment

---

**Generated**: 2026-01-19
**Updated**: 2026-01-19 (fixed import errors)
