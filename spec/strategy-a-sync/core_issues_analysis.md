# 3U MEL 核心问题诊断报告

## 问题汇总

| 问题 | 状态 | 根本原因 | 优先级 |
|------|------|----------|--------|
| 1. MinerU 接入失败 | 🔴 待修复 | by_three_u 找不到 MinerU 模型，回退到 DeepDoc | P0 |
| 2. pdf_parser.py 越界错误 | ✅ 已修复 | DeepDoc 回退路径的副作用，已添加边界检查 | P0 |
| 3. 前端闪退 | 🔴 待修复 | ChunkMethodForm 组件导入错误 | P1 |

---

## 问题1: MinerU 接入失败 (P0)

### 症状
- 错误堆栈显示：`naive.py:636 → pdf_parser.py:1164`
- 这是 **DeepDoc 路径**，不是预期的 MinerU 路径
- 预期路径应该是：`three_u → mineru_parser.py`

### 调用链分析

```
three_u_mel_navigation_parser.py:663
  ↓ parser_entry() 
  ↓ (_resolve_layout_parser 返回)
  ↓
naive.py:by_three_u (L221-280)
  ↓ (尝试获取 MinerU)
  ├─ tenant_id 存在 → 查询 MinerU 模型
  ├─ 找到模型 → LLMBundle 创建 MinerU parser
  └─ 【失败】→ 返回 (None, None, None)  ← 🔴 问题在这里
  ↓
three_u_mel_navigation_parser.py:678
  ↓ if pdf_parser is None: 
  ↓ → 应该跳过或回退
  ↓ 但实际上继续使用了 by_deepdoc  ← 🔴 导致走错路径
  ↓
by_deepdoc (PDF Parser - DeepDoc)
  ↓
pdf_parser.py (_extract_table_figure)
  ↓
IndexError: index -312 out of bounds  ← 🔴 越界错误是副作用
```

### 根本原因

#### 1. MinerU 未正确配置

`by_three_u` (naive.py:246-277) 的逻辑：

```python
if tenant_id:
    if not mineru_llm_name:
        # 尝试从数据库查询 MinerU 模型
        candidates = TenantLLMService.query(
            tenant_id=tenant_id, 
            llm_factory="MinerU",  # 🔴 关键: 查询 llm_factory="MinerU"
            model_type=LLMType.OCR
        )
        if candidates:
            mineru_llm_name = candidates[0].llm_name
```

**失败点**：
- 数据库中可能没有 `llm_factory="MinerU"` 的记录
- 或者 tenant_id 不正确
- 或者 TenantLLMService 配置不完整

#### 2. 回退逻辑不完善

```python
# by_three_u 的回退 (L278-280)
if callback:
    callback(-1, "MinerU not found for 3U MEL parsing.")
return None, None, None  # 🔴 返回 None
```

然后在 `three_u_mel_navigation_parser.py`:
```python
ocr_sections, _, pdf_parser = parser_entry(...)  # 返回 None

if pdf_parser is None:  # L678
    logger.error("...")
    continue  # 🔴 应该跳过，但之前可能有回退到 by_deepdoc 的逻辑
```

### 修复方案

#### 方案A: 确保 MinerU 正确配置 (推荐)

1. **检查数据库配置**:
   ```sql
   SELECT * FROM llm WHERE llm_factory = 'MinerU';
   SELECT * FROM tenant_llm WHERE llm_factory = 'MinerU';
   ```

2. **检查环境变量**:
   ```bash
   echo $MINERU_MODEL_NAME
   echo $MINERU_API_BASE
   ```

3. **添加 MinerU 到数据库**:
   - 通过 RAGFlow UI 添加 MinerU OCR 模型
   - 或者通过 `TenantLLMService.ensure_mineru_from_env()` 自动配置

#### 方案B: 改进回退逻辑

在 `by_three_u` 中添加更详细的日志和诊断信息。

#### 方案C: 检查 layout_recognizer 配置

`_resolve_layout_parser` 的逻辑（L104-119）：

```python
layout = cfg.get("layout_recognize", "DeepDOC")  # 🔴 默认是 DeepDOC!

if layout_lower == "mineru":
    parser_func = by_three_u
else:
    parser_func = PARSERS.get(layout_lower, by_deepdoc)  # 🔴 回退到 by_deepdoc
```

**问题**: 如果 `parser_config` 中 `layout_recognize` 不是 `"mineru"`，就会使用 DeepDOC！

---

## 问题2: pdf_parser.py 越界错误 (已修复 ✅)

### 原因
DeepDoc 的 `cropout` 函数使用绝对页码访问 `page_cum_height` 数组，但 3U MEL 分段处理导致数组size 远小于绝对页码。

### 修复
已添加边界检查和过滤逻辑，跳过越界的 boxes。

---

## 问题3: 前端闪退 (P1)

### 错误信息
```
Element type is invalid: expected a string ... but got: undefined
Check the render method of `ChunkMethodForm`
```

### 可能原因
1. `chunk-method-dialog` 相关组件导入了不存在的组件
2. Named import vs Default import 混淆
3. 循环依赖

### 调查步骤
1. 检查 `chunk-method-dialog/index.tsx` 的导入语句
2. 检查是否有未定义的组件引用
3. 检查 TypeScript 编译错误

---

## 立即行动项

### 🔴 P0: 修复 MinerU 接入

1. **诊断 MinerU 配置**:
   ```python
   # 创建诊断脚本
   from api.db.services.tenant_llm_service import TenantLLMService
   tenant_id = "your_tenant_id"
   candidates = TenantLLMService.query(tenant_id=tenant_id, llm_factory="MinerU", model_type=3)
   print(f"Found {len(candidates)} MinerU models")
   for c in candidates:
       print(f"  - {c.llm_name}")
   ```

2. **检查 parser_config**:
   - 在 dataset 设置中，`layout_recognize` 字段应该设置为 `"mineru"`
   - 或者在代码中强制使用 MinerU

3. **添加调试日志**:
   ```python
   # 在 by_three_u 中添加
   logging.info(f"by_three_u called: tenant_id={tenant_id}, mineru_llm_name={mineru_llm_name}")
   logging.info(f"MinerU candidates: {candidates}")
   ```

### 🟡 P1: 修复前端闪退

检查前端组件导入，确保所有组件都正确导出。
