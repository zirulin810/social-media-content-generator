# 自動化輸出 — Pipeline 規格 v3.1

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

- `schema_version`：`highlights.json` 是 `"3.1"`（`article.json` 仍是 `"2.0"`、`post.json` 仍是 `"3.0"`——它們沒改）
- `source`：三份檔案都帶同一份（slug / title / author / url / file）

## 字數上限是編輯判斷，不是技術極限（v3.1 校準）

原本的上限是**在還沒有版型的時候猜的**。版型做出來後實測（`scripts/calibrate.py`，真的瀏覽器、真的二分搜尋）：

| 卡型 | 版面實際塞得下（字級仍在 44px 舒適線上） | 契約上限 |
| --- | --- | --- |
| `point.body` | **548 字** | 180 |
| `steps` 每步（4 步時） | 129 字 | 50 |
| `quote.text` | 110 字 | 40 |
| `contrast` 每邊 | 266 字 | 60 |

**版面根本不是限制。** 它能塞的量遠超過任何一張合理的圖卡——548 字技術上「讀得到」，但那是一面文字牆。

所以字數上限的性質必須講清楚：**它是編輯判斷（一張卡放多少，人才願意讀完），不是技術極限。**
這個判斷量尺答不出來，只有人的眼睛能答。`scripts/density_demo.py` 用真內容出了三種密度的圖，
Human 2026-07-14 看圖選定：**步驟＝精簡（目標 25 字）、重點＝中等（目標 80 字）**。兩者都否決了「充分」。

推論：

- **可拆的卡型（point / steps）**：上限＝編輯密度 × 拆卡預算。超過單張不是錯，渲染器會拆成多張，**每張仍維持選定的密度**
- **不可拆的卡型（quote / contrast / cover）**：上限就是硬牆，結構切不開，超過只能回頭改文案
- **步數由流程決定（2–6）**：作者講幾步就寫幾步。**砍掉第 5 步，讀者會照著做卻失敗**

### 三種上限，住在三個地方（v3.1 修正）

| 性質 | 住在哪 | 違反時 |
| --- | --- | --- |
| **編輯目標**（步驟 25 字、重點 80 字） | `prompts/highlights.md` | 沒事——那是傾向，不是規則 |
| **物理極限**（版面真的印不出來） | `schemas/*.json` | 硬拒 `SCHEMA_INVALID` |
| **密度**（會不會變成文字牆） | 渲染器（autofit + `layout.plan()`） | 自動拆卡 |

**把編輯偏好寫進 schema，就是給它一把它不該有的刀。**

實例（2026-07-14，`分析全部素材.bat`）：我把 `steps` 每步上限訂在 **50**（依據是**我自己寫的**一句 44 字的範例）。
模型把同一件事寫成 **52 字**——「Mac (Command + 點擊) / Win (Control + 點擊) 未建立連結即可建立新筆記」——
於是**兩篇文章整份被丟掉，各燒掉 3 次 LLM 呼叫、約 60 秒，死在兩個字上**。
而版面實際吃得下 129 字。修復迴圈連修兩輪都改不短，**代表那句話本來就需要那些字，不是模型不聽話**。

修正：schema 的上限一律訂在**物理極限**（`steps` 每步 100、`contrast` 每邊 120），編輯偏好只留在 prompt 的「目標」欄。

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
| 3.1 | 2026-07-14 | **字數上限用真實版面校準**（原本是猜的）。實測版面塞得下 548 字 → 上限是編輯判斷不是技術極限。`steps` 2–6 步（原 4）、每步 ≤50 字（原 60，但目標 25）；`point.body` ≤180（原 120，目標 80）。密度由 Human 看圖選定 |
| 3.0 | 2026-07-13 | **產出從「金句」改為「可運用的知識」**。`highlights.json` 改成 貼文 → 知識卡（point/steps/contrast/quote）；每條主張各自帶 evidence；依資訊密度切 1–3 則貼文；幻覺防線改為「對照但不攔截」，判斷交給人 |
| 2.0 | 2026-07-13 | MVP 收斂：砍掉影音來源，改吃本機 markdown；金句定錨從時間戳改為段落 index |
| 1.0 | 2026-07-12 | 初版：影片來源、五階段、時間戳定錨 |
