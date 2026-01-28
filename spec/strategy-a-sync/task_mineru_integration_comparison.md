# Task: Investigate MinerU Integration Mechanism Changes Between Versions

## Objective

Compare MinerU integration mechanism between `feature/strategy-a-sync` branch and RAGFlow v0.23.1 to identify why `by_three_u` cannot find MinerU models.

## Key Investigation Points

### 1. MinerU Model Registration

**v0.23.1 (`rag/app/naive.py:246-277`):**
```python
# Query MinerU model from database
candidates = TenantLLMService.query(
    tenant_id=tenant_id,
    llm_factory="MinerU",     # 🔍 Key: searches for llm_factory="MinerU"
    model_type=LLMType.OCR    # LLMType.OCR = 3
)
```

**Question**: How was MinerU registered in `feature/strategy-a-sync` branch?
- Was `llm_factory` field different?
- Was there an automatic registration mechanism?
- Check database schema differences for `llm` and `tenant_llm` tables

### 2. MinerU Initialization

**v0.23.1:**
```python
ocr_model = LLMBundle(
    tenant_id=tenant_id,
    llm_type=LLMType.OCR,
    llm_name=mineru_llm_name,
    lang=lang
)
pdf_parser = ocr_model.mdl
sections, tables = pdf_parser.parse_pdf(
    filepath=filename,
    binary=binary,
    callback=callback,
    parse_method="three_u",  # 🔍 Key: parse_method parameter
    lang=lang,
    **kwargs,
)
```

**Question**: How was `parse_pdf` called in the old version?
- Was `parse_method` parameter supported?
- Was there a different entry point?
- Check `LLMBundle` and `parse_pdf` signature changes

### 3. TenantLLMService Changes

**v0.23.1:**
```python
from api.db.services.tenant_llm_service import TenantLLMService

env_name = TenantLLMService.ensure_mineru_from_env(tenant_id)
```

**Question**: Did `TenantLLMService` exist in the old version?
- Was there a different service for managing OCR models?
- Check `api/db/services/tenant_llm_service.py` creation date
- Look for migration scripts related to tenant_llm table

### 4. Environment Variable Handling

**v0.23.1:**
```python
# Falls back to environment variable if database lookup fails
if candidates:
    mineru_llm_name = candidates[0].llm_name
elif env_name:
    mineru_llm_name = env_name
```

**Question**: What environment variables were used in the old version?
- Check for `MINERU_MODEL_NAME`, `MINERU_API_BASE`, etc.
- Compare `.env` file structure between versions

## Files to Compare

### In `feature/strategy-a-sync`:
1. `rag/app/naive.py` - Look for `by_three_u` or MinerU integration
2. `api/db/services/tenant_llm_service.py` - Check if exists
3. `api/db/services/llm_service.py` - Check LLMBundle implementation
4. `deepdoc/parser/mineru_parser.py` - Check `parse_pdf` method signature
5. Database init scripts in `api/db/init_data.py` or similar

### In `v0.23.1`:
(Same files as above for comparison)

## Expected Deliverable

Please provide:

1. **Key differences** in MinerU registration and initialization
2. **Migration path**: What changes are needed to make MinerU work in v0.23.1
3. **Configuration requirements**: 
   - What database entries need to be created?
   - What environment variables are needed?
   - Does UI need to be used to register MinerU?

## Quick Diagnostic Commands

```python
# Check database for MinerU models
SELECT * FROM llm WHERE llm_factory = 'MinerU' OR model_type = 3;
SELECT * FROM tenant_llm WHERE llm_factory = 'MinerU';

# Check environment
echo $MINERU_MODEL_NAME
echo $MINERU_API_BASE
echo $MINERU_BACKEND
```

## Context

Currently, `by_three_u` always returns `(None, None, None)`, causing fallback to DeepDoc, which leads to page index errors. We need MinerU to work correctly for 3U MEL parsing.
