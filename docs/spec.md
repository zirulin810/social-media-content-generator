# 自動化輸出 — Pipeline 規格 v3.0

> 從一篇文章到可上傳的社群貼文。本文件定義**階段之間的資料契約**——只要各模組守住這份契約，就能各自獨立開發、獨立測試。

**v3.0 的 MVP 邊界**：來源只吃**本機 markdown 檔**。影片不在範圍內——現成工具已經能把 YouTube 轉成文章，那一段外包出去，這條 pipeline 只從「已經是文字」的地方開始。

## 四個階段

```
[1] 讀取   article.md                     → article.json
[2] 分析   article.json                   → highlights.json（1–3 則貼文 × 知識卡）
[3] 出圖   highlights.json + templates/   → p<N>/images/*.png
[4] 文案   highlights.json                → p<N>/post.json
```

| 階段 | 模組檔案 | 輸入 | 輸出 | 對應任務筆記 |
| --- | --- | --- | --- | --- |
| 1 讀取 | `src/ingest/read_article.py` | 本機 `.md` 檔路徑 | `out/<slug>/article.json` | [[文章讀取與正規化]] |
| 2 分析 | `src/analyze/extract_highlights.py` | `article.json` | `out/<slug>/highlights.json` | [[重點分析與金句抽取]] |
| 3 出圖 | `src/render/render_cards.py` | `highlights.json` + `templates/` | `out/<slug>/p<N>/images/*.png` | [[社群圖卡版型設計]]、[[圖卡渲染器]] |
| 4 文案 | `src/compose/write_post.py` | `highlights.json` | `out/<slug>/p<N>/post.json` | [[貼文文案產生器]] |
| — 串接 | `src/cli.py` | 本機 `.md` 檔路徑 | 以上全部 | [[端到端 CLI 串接]] |

## 資料契約

三份 JSON Schema 在 `schemas/`，各附一份範例在 `schemas/examples/`。

### v3.0 的核心改動：產出的是可運用的知識，不是金句

v1/v2 假設「一則貼文 = 一串金句」。**那個假設錯了。**
讀者要的不是看完點個頭，是**知道明天可以動手做什麼**。

所以 `highlights.json` 從「金句陣列」改成「**貼文 → 知識卡**」：

```
highlights.json
└── posts[]          1–3 則，依文章的資訊密度切
    ├── angle        這則的單一論點（封面標題）
    ├── hook         副標：讀者能帶走什麼
    └── cards[]      2–6 張知識卡
```

四種卡：

| 卡型 | 內容 | 用途 |
| --- | --- | --- |
| `point` | 標題（24 字）+ 說明（120 字） | **主力**。一個主張／做法 + 為什麼有效 |
| `steps` | 標題 + 2–4 步（每步 60 字） | 可照做的流程 |
| `contrast` | 標題 + 錯法 / 正確（各 60 字） | 常見錯法 vs 正確做法 |
| `quote` | 一句話（40 字） | 點綴，一則最多一張 |

**字數上限是暫定的。** 這些數字是在版型還不存在時訂的——第一次真跑就撞到：
`在 Mac 上 Command + 點擊（Windows 上 Control + 點擊）` 這種**本來就必須講清楚的操作**塞不進 45 字。
限制不合理時要改的是限制，不是把知識閹割掉。版型做出來後要回頭校準這幾個數字。

### schema 的錯誤訊息必須指得到欄位

卡片一開始用 `oneOf` 分派，結果錯誤訊息是 `is not valid under any of the given schemas`——
完全看不出哪個欄位錯了。已改成**按 `type` 用 `if/then` 分派**，現在錯誤會直接說
`posts/1/cards/1/steps/0/text: too long`。

**驗證器的錯誤訊息是使用者介面。** 看不懂的錯誤等於沒有驗證。

### evidence：每一條主張各自指出處

**這是 v3 最重要的一件事。**

「不能超出原文說過的」是紅線。但可運用的知識**通常是跨段落綜合出來的**——原文不會有一句話說「先建 me.md，再建 vault map」。所以定錨的粒度必須從「一張卡」細到「**一條主張**」：

- `point` 卡：整張 1 條主張 → 1 組 evidence
- `steps` 卡：**每一步各自** 1 組 evidence（步驟最容易被腦補）
- `contrast` 卡：**錯法與正確各自** 1 組 evidence（錯法那邊最容易被腦補）
- `quote` 卡：1 組 evidence

每組 evidence = `{para_index, source_text}`，`source_text` 必須**逐字**出現在該段落裡。

### 機器不攔截，判斷交給人（2026-07-13 決定）

`grounding.review()` 逐條對照，把結果標成 ✓／✗，**但不擋下任何東西**。

- 機器回答的是機械問題：「這句 `source_text`，我在第 N 段裡找得到嗎？」
- 機器**不回答**：「你的中文重述有沒有超出這句原文的意思？」

第二個問題只有人能答。所以 `scripts/analyze_all.py` 把「主張 ↔ 原文」**並排印出來**，讓人審的成本降到最低。

**為什麼驗證交給人、evidence 卻要留著？** 正因為交給人。沒有 evidence，要查一句話忠不忠於原文，得回頭重讀兩萬字逐字稿；有 evidence，只要看一張並排的表。機器不判斷，但機器可以把表排好。

要恢復「對不上就擋」：`STRICT_GROUNDING=1`。

### 輸出目錄

一篇文章可能切成多則貼文，所以產物多一層：

```
out/<slug>/
├── article.json
├── highlights.json          （含 1–3 則貼文）
├── p1/
│   ├── post.json
│   └── images/
│       ├── 01_cover.png
│       ├── 02_point_1.png
│       ├── 03_steps_2.png
│       └── 99_outro.png
└── p2/
    ├── post.json
    └── images/…
```

**圖檔命名**：`<兩位序號>_<卡型>[_<卡片序號>].png`。卡型即 `cover` / `point` / `steps` / `contrast` / `quote` / `outro`；卡片序號 1-based，對應 `highlights.posts[].cards` 的順序。封面與結尾由版型自動生成，不在 `cards` 裡。

### 共用欄位

- `schema_version`：`"3.0"`（`article.json` 仍是 `"2.0"`——它沒改）
- `source`：三份檔案都帶同一份（slug / title / author / url / file）

## 錯誤碼

| Code | 意義 | 誰處理 |
| --- | --- | --- |
| `SOURCE_NOT_FOUND` | md 檔不存在 | 人給對路徑 |
| `SOURCE_UNPARSEABLE` | 編碼壞掉，或 frontmatter 缺 author／url | 人補 frontmatter |
| `ARTICLE_TOO_SHORT` | 正規化後不足 3 段或 300 字 | 人換一篇 |
| `QUOTE_NOT_GROUNDED` | 主張對不回原文（**只在 `STRICT_GROUNDING=1` 時才擋**） | 重跑，或人自己判斷 |
| `SCHEMA_INVALID` | 產物不符 schema | 修模組 |
| `RENDER_OVERFLOW` | 文字溢出圖卡 | 縮字級或改文案 |
| `MISSING_INPUT` | 上一階段產物不存在／缺 API key | 先跑上一階段 |

## 執行慣例

- **schema 不合就把錯誤餵回模型請它修**（最多 2 輪），不整批重想——它已經讀完文章了，只是某個欄位超字數
- **可續跑**：階段輸出已存在就跳過，`--force` 才重跑
- **可分段**：`--only ingest|analyze|render|compose`
- **失敗即停**：不往下跑產生垃圾
- LLM 供應商見 `src/llm.py`（預設 Gemini）。暫時性錯誤（503/429）自動重試

## 版本

| 版本 | 日期 | 變更 |
| --- | --- | --- |
| 3.0 | 2026-07-13 | **產出從「金句」改為「可運用的知識」**。`highlights.json` 改成 貼文 → 知識卡（point/steps/contrast/quote）；每條主張各自帶 evidence；依資訊密度切 1–3 則貼文；幻覺防線改為「對照但不攔截」，判斷交給人 |
| 2.0 | 2026-07-13 | MVP 收斂：砍掉影音來源，改吃本機 markdown；金句定錨從時間戳改為段落 index |
| 1.0 | 2026-07-12 | 初版：影片來源、五階段、時間戳定錨 |
