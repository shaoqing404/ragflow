# 3U MEL Migration to RAGFlow v0.23.1

## Migration Summary

**Date**: 2026-01-19  
**Source**: `d:\element_WorkSpeac\ragflow_worktrees\strategy-a-sync` (trio-based)  
**Target**: `E:\element_workspace\ragflow` (v0.23.1, asyncio-based)

---

## Files Copied

### Backend Parser Files
| File | Source Size | Target Path |
|------|-------------|-------------|
| `three_u_mel_navigation_parser.py` | 69KB | `rag/app/` |
| `three_u_link_hierarchy_extractor.py` | 30KB | `rag/app/` |
| `three_u_metadata.py` | 12KB | `rag/app/` |
| `three_u_semantic_merge.py` | 8KB | `rag/app/` |
| `three_u_splitting.py` | 27KB | `rag/app/` |
| `three_u_storage_utils.py` | 7KB | `rag/app/` |

### VLM Enhancer
| File | Target Path |
|------|-------------|
| `mel_chunking/__init__.py` | `rag/app/mel_chunking/` |
| `mel_chunking/vlm_enhancer.py` | `rag/app/mel_chunking/` |

### Prompts
| File | Target Path |
|------|-------------|
| `three_u_mel_vlm_enhance_prompt.md` | `rag/prompts/` |

---

## Files Modified

### Backend
- **`rag/svr/task_executor.py`**
  - Added imports: `mel_navigation_parser`, `three_u_mel_navigation_parser`
  - Added FACTORY entries: `"mel"`, `"3u_mel"`

- **`deepdoc/parser/mineru_parser.py`**
  - Added enum: `MinerUContentType.HEADER`, `MinerUContentType.PAGE_NUMBER`
  - Added method: `by_three_u_line_tag()`
  - Added method: `by_three_u_transfer_to_sections()`

### Frontend
- **`web/src/constants/knowledge.ts`**
  - Added enum: `Mel = 'mel'`, `ThreeUMel = '3u_mel'`

- **`web/src/components/chunk-method-dialog/hooks.ts`**
  - Added to PDF parser list: `'mel'`, `'3u_mel'`
  - Added to hideAutoKeywords: `'mel'`, `'3u_mel'`

---

## Concurrency Model Adaptation

**Finding**: No code changes needed in 3U parsers themselves.

The 3U MEL parsers are **synchronous** Python code (`def chunk(...)`). In v0.23.1's asyncio framework, they are automatically wrapped via `asyncio.to_thread()` (see `task_executor.py` L265-276).

**Original (trio)**:
```python
async with chunk_limiter:
    cks = await trio.to_thread.run_sync(lambda: chunker.chunk(...))
```

**Target (asyncio)**:
```python
async with chunk_limiter:
    cks = await asyncio.to_thread(chunker.chunk, ...)
```

---

## Pending User Actions

See: [`env_diff_report.md`](env_diff_report.md) for environment variable migration.
