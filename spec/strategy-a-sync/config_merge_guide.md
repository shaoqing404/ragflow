# 配置文件合并指南

## 1. service_conf.yaml 合并

**关键差异**：源文件有 `parsers` 字段，目标文件没有。

### 需要在目标文件添加（第48行 `user_default_llm:` 下方）：

```yaml
user_default_llm:
  parsers: "naive:General,qa:Q&A,resume:Resume,manual:Manual,table:Table,paper:Paper,book:Book,laws:Laws,presentation:Presentation,picture:Picture,one:One,audio:Audio,email:Email,tag:Tag,mel:CA_MEL,3u_mel:3U_MEL"
  default_models:
    ...
```

### 密码差异（按需更新）：
| 服务 | 目标(.v0.23.1) | 源(strategy-a-sync) |
|------|---------------|---------------------|
| mysql | infini_rag_flow | Wintelia_zxq123_mysql |
| minio | infini_rag_flow | Wintelia_zxq123_minio |
| es | infini_rag_flow | Wintelia_zxq123_es |
| redis | infini_rag_flow | Wintelia_zxq123_redis |

---

## 2. docker/.env 合并

### 需要添加的 3U MEL 配置块（建议放在 L180 后）：

```bash
# ============================================================
#  FTMS 3U MEL Enhancement Configuration
# ============================================================
ENABLE_FULL_TEST=true
THREE_U_MEL_PAGE_RANGE=314,320
THREE_U_MEL_CHUNK_TOKEN_NUM=768

# ============================================================
#  Task Executor Performance Configuration
# ============================================================
WS=1
MAX_CONCURRENT_TASKS=2
MAX_CONCURRENT_CHUNK_BUILDERS=2
MAX_CONCURRENT_MINIO=20
MAX_CONCURRENT_CHATS=200

# ============================================================
#  MinerU VLM Enhancement Configuration
# ============================================================
USE_MINERU=true
MINERU_APISERVER="http://120.236.119.10:27300"
MINERU_EXECUTABLE=".venv/bins/tsdmineru"
MINERU_BACKEND="vlm-http-client"

# ============================================================
#  FTMS VLM Enhancement Configuration
# ============================================================
FTMS_VLM_ENABLED=false
FTMS_VLM_MODEL_NAME=qwen-vl-max
FTMS_VLM_MAX_CONCURRENCY=2
FTMS_VLM_MAX_PAGE_IMAGES=3
FTMS_VLM_PAGE_GAP=24
FTMS_VLM_MAX_WIDTH=2048
FTMS_VLM_MAX_TOKENS=12000
FTMS_VLM_FALLBACK_ENABLED=true
FTMS_VLM_FALLBACK_API_KEY=sk-xxx
FTMS_VLM_FALLBACK_MODEL=qwen-vl-max
FTMS_VLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
FTMS_VLM_FALLBACK_PROVIDER=dashscope
```

### v0.23.1 新增变量（保留）：
- OceanBase 相关配置 (L74-93)
- Crypto 配置 (L254-257)
- 安全警告注释 (L1-6)

### 密码差异（按需更新）：
源文件使用自定义密码，目标使用默认密码。请根据实际环境决定使用哪个。

---

## Commit Message

```
feat(3u-mel): migrate 3U MEL parsing pipeline to RAGFlow v0.23.1

Migration from strategy-a-sync branch with trio→asyncio conversion:

Backend changes:
- Add by_three_u() function to naive.py with LLMBundle pattern
- Add three_u_mel_vlm_enhance_prompt to generator.py
- Add parse_method="three_u" handling to mineru_parser.py
- Convert vlm_enhancer.py from trio to asyncio
- Add mel/3u_mel entries to task_executor FACTORY

Files copied:
- three_u_mel_navigation_parser.py
- three_u_link_hierarchy_extractor.py  
- three_u_metadata.py
- three_u_semantic_merge.py
- three_u_splitting.py
- three_u_storage_utils.py
- mel_chunking/vlm_enhancer.py
- link_hierarchy_extractor.py
- three_u_mel_vlm_enhance_prompt.md

Frontend changes:
- Add Mel/ThreeUMel to DocumentParserType enum
- Add mel/3u_mel to PDF parser list in hooks.ts

Config required (manual):
- Add parsers field to service_conf.yaml
- Add 3U MEL env vars to docker/.env
```
