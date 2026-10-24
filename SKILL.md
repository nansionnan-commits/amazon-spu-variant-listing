---
name: amazon-spu-variant-listing
description: 从多维表格的 SKU 出发，反查 ERP 拿 SPU 和它下面全部变体，把整个 SPU 的所有子体一次性建成一个完整父子体 listing（1 父 + N 子），副图用多维表格的、价格用 ERP 算。当老板说「把这个 SPU 的变体都上」「多维表格里的 SKU 拿去 ERP 查完一起上」「不要只上单变体了」「按 SPU 上架」时触发。
---

# SPU 全变体上架

> **核心区别：以前是一个 SKU 上一个 listing；现在是拿一个 SKU 找到它所在的 SPU，把 SPU 下所有变体一次性建成「1 父 + N 子」。**

---

## 何时用

| 场景 | 示例 |
|---|---|
| ✅ **用** | "多维表格这几个 SKU 拿去 ERP 查，把 SPU 的变体都上了" |
| ✅ **用** | "不要只上单变体，整个 SPU 一起上" |
| ❌ **不用** | 单款上架（用 `amazon-us-listing`）、跨站复制（用 `amazon-cross-market-listing`）|

---

## 🔴 老板定的流程（2026-10-24，逐字对照）

> 「多维表格里的品，都带 sku，带 sku 的先去 erp 里搜索，搜出来的对应的产品，对应的 spu，把这个 spu 下的变体全部算上，去做链接，然后副图就用多维表里的副图，懂了不？价格的话用 erp 上的信息去算价格。然后去上架。这次要把 spu 下的子体都上架，不要上单变体那种了。」

### 数据来源分工（关键）

| 数据 | 来源 | 说明 |
|---|---|---|
| **入口** | **多维表格** | 每条记录带一个 SKU |
| **SPU + 全部变体** | **ERP** | 用 SKU 反查 |
| **变体价格** | **ERP** | `purchasePrice`（采购价 CNY）→ 算售价 |
| **变体重/尺寸** | **ERP** | `weight` / `length` / `width` / `height` |
| **副图** | **多维表格** | 老板明确要求 |
| 主图 | ⚠️ **待确认**（见下）| |
| 标题 / 五点 | ⚠️ **待确认**（见下）| |

### ⚠️ 两个待老板确认的点

**1. 主图用哪的？**
- 选项 A：ERP 的 `skuList[].imageList[0]`（每个变体自己的主图）
- 选项 B：多维表格的主图（一张图给所有子体）
- **当前假设：A**（每个变体用自己的主图才合理）

**2. 标题 / 五点用哪的？**
- 选项 A：复用 `amazon-us-listing` 的文案生成（竞品逆向 → 差异化文案）
- 选项 B：多维表格的核心词 + 模板
- **当前假设：A**（保持文案质量）

---

## 执行流程

### 步骤 1：读多维表格，拿 SKU + 副图

```python
# 用 lark-cli 拉表
lark-cli base +record-list --base-token JTZIbgyxHahXjHs73c3cbvxcn1e --table-id tblrNzaaloATlTOS
```

**从每条记录提取**：
- `SKU登记`（入口 SKU）
- `附图+视频文件包`（**副图来源**）
- `主图`（备用）
- `核心词`（写文案用）
- `价格`（价格图，参考）

### 步骤 2：SKU → ERP 反查 SPU

**⚠️ ERP 没有「按 SKU 查 SPU」的接口**，两条路：

#### 路 A：拉全量列表本地匹配（推荐）
```python
POST http://api.hzcwin.com/hzkj-product/product/productLibrary/spu/list
body: {"pageNo":1,"pageSize":100,"productStatus":5}
# 逐页拉完（约 22,660 个），本地按 skuList[].skuCode 匹配
```

#### 路 B：试列表接口的关键词过滤
```python
body: {"pageNo":1,"pageSize":20,"productStatus":5,"keyword":"<SKU>"}
# 不确定支不支持，先试
```

**鉴权**：
```python
X-Access-Token: <localStorage["pro__Access-Token"] 解析出的 .value>
```
**必须在浏览器上下文内发请求**（bsk `/eval` 或 CDP），token 有时效。

### 步骤 3：拿 SPU 详情

```python
GET http://api.hzcwin.com/hzkj-product/product/productLibrary/spu/queryById?id={spuId}
```

**关键字段**：
| 字段 | 用途 |
|---|---|
| `spuCode` | 父体 SKU 基础（`{spuCode}-PARENT`）|
| `skuList[].skuCode` | **子体 SKU** |
| `skuList[].attributes` | 变体属性（颜色/尺寸）|
| `skuList[].purchasePrice` | 采购价（CNY）|
| `skuList[].weight` | 重量（kg）|
| `skuList[].length/width/height` | 包装尺寸（cm）|
| `skuList[].imageList[]` | 图片（0=主图，1~6=副图）|

### 步骤 4：构建父子体

**结构**：
```
父体：{spuCode}-PARENT
  ├── 无价格、无库存、无图片
  ├── parentage_level = "parent"
  └── variation_theme = "COLOR"（或 COLOR/SIZE）

子体（SPU 下每个 skuCode）：
  ├── 有价格、库存、图片
  ├── parentage_level = "child"
  ├── child_parent_sku_relationship → 指向父体
  └── variation_theme = "COLOR"
```

**副图**：用多维表格的 `附图+视频文件包`
**主图**：ERP `imageList[0]`（待确认）
**价格**：`purchasePrice` → `calc_price.py` 算（云途美国 50% 利润 + .99 结尾）

### 步骤 5：上架（先父后子）

```python
# 1. 父体先上
PUT /listings/2021-08-01/items/{sellerId}/{parent_sku}
# 2. 等父体 ACCEPTED
# 3. 子体逐个上
PUT /listings/2021-08-01/items/{sellerId}/{child_sku}
```

**走 guard 防撞车**：
```python
import listing_guard as lg
lg.claim(spu_code, "base")   # 整个 SPU 占一个号
```

⚠️ **父体先上，子体后上**。反了会报 `90253 缺 parent_sku`。

### 步骤 6：回验 + 回填

```python
# 回验
python verify_listings.py --store zhangaodi --market US --skus <全部子体SKU>
```

**回填飞书**：
- `上传了吗 = 已传`
- `上架时间 = MM-DD HH:SS`

---

## 可复用现有能力

| 能力 | 从哪复用 |
|---|---|
| **ERP SPU 解析 + 父子体构建** | `amazon-us-listing/scripts/erp_listing.py` 的 `parse_variants()` / `build()` |
| **算价** | `amazon-us-listing/scripts/calc_price.py` |
| **防撞车** | `amazon-us-listing/scripts/listing_guard.py` |
| **payload 构建** | `amazon-us-listing/scripts/build_listing.py` |
| **上架后体检** | `amazon-us-listing/scripts/verify_listings.py` |
| **飞书表读写** | `lark-cli` |
| **图床** | `amazon-us-listing/scripts/image_host.py` |
| **ERP 接口鉴权** | `amazon-us-listing/docs/erp-source.md` |

**不需要重写，直接 import 或复制。**

---

## 与现有 skill 的区别

| | `amazon-us-listing` | **本 skill** |
|---|---|---|
| 输入 | 竞品 ASIN + 图片 | **多维表格 SKU** |
| 数据源 | 手工收集 | **ERP 反查** |
| 变体 | 单款 / 简单父子 | **SPU 下全部变体** |
| 副图 | 竞品图 / 自己图 | **多维表格** |
| 价格 | 手填 | **ERP 采购价算** |

---

## 待解决问题（跑第一单前必须确认）

- [ ] **主图用 ERP 还是多维表格**（老板确认）
- [ ] **标题/五点用哪套文案生成**（老板确认）
- [ ] **ERP 列表接口是否支持按 SKU 过滤**
- [ ] **ERP token 怎么稳定拿**（浏览器 localStorage，会过期）
- [ ] **变体属性怎么映射成 Amazon variation theme**（颜色/尺寸/组合）
- [ ] **父体 SKU 命名**（`{spuCode}-PARENT` 还是别的）

---

## 踩坑预警

| 坑 | 说明 |
|---|---|
| **父体不能有价格/库存/图片** | 带了会报错，`build()` 已处理 |
| **子体必须带 parent_sku** | 否则报 `90253` |
| **父体先上，子体后上** | 反了报错 |
| **同款不同规格必须合一个父体** | 拆多条报 `100730 identical listing` |
| **ERP token 会过期** | 每次任务前重新从浏览器拿 |
| **ERP 必须在浏览器上下文发请求** | 直接 requests 会 401 |
