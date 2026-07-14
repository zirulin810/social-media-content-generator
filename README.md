# 自動化輸出

一篇文章（本機 markdown）→ **知識卡** → 社群圖卡 → 貼文文案。目標平台：Instagram、Threads。

**MVP 邊界**：只吃文字。影片不在範圍——現成工具已經能把 YouTube 轉成文章，那一段外包出去，
這條 pipeline 從「已經是文字」的地方開始。若來源是影片轉出的文章，`source.url` 仍指回原影片。

任務佇列在 Obsidian：`02_Project/自動化輸出/`。
**資料契約是這個專案的核心**，先讀 [`docs/spec.md`](docs/spec.md)（v3.1）。

## 現況：四個階段全部跑通

| 階段 | 狀態 |
| --- | --- |
| 1 ingest（`read_article.py`） | ✅ 5 篇真實 Clippings 跑通 |
| 2 analyze（`extract_highlights.py`） | ✅ Gemini 跑通，4/4 篇產出知識卡 |
| 3 render（`render_cards.py` + `templates/`） | ✅ 4 篇 × 3 則 = 79 張 PNG，零溢出 |
| 4 compose（`write_post.py`） | ✅ 12 則文案，IG 與 Threads 共用一份 |
| 端到端 CLI（`cli.py`） | ⬜ **還是空殼**——目前靠四個 .bat 串起來 |

## 怎麼跑（雙擊就好）

| | |
| --- | --- |
| `安裝.bat` | 建 `.venv` + 裝套件（**不會下載瀏覽器**） |
| `分析全部素材.bat` | ① 讀 `Clippings/` 的文章 → 知識卡 + 審稿表 |
| `出圖.bat` | ② 出圖卡（用系統的 Edge/Chrome） |
| `產生文案.bat` | ③ 寫文案（hook + 正文 + hashtag） |
| `測試.bat` | 跑測試 |
| `提交.bat` | 存檔進 git（訊息用時間戳自動生成） |

開發／一次性工具在 `tools\`：預覽版型、校準字數上限、比較密度、測試拆卡、測試Gemini、檢查瀏覽器。

**每一階段都會自己判斷要不要重跑**：產物比**任何一個輸入**舊就重做——
輸入包括資料、prompt、版型，**以及產生它的那段程式碼**。

## 貫穿全部的一條線

> **機械的事程式做，判斷的事模型做，品味的事人來決定。**

| 誰做 | 做什麼 |
| --- | --- |
| **程式** | 出處標註、hashtag、字數上限、簡繁轉換、剝 emoji、拆卡、圖片清單、hook 的形式檢查 |
| **模型** | 把知識講成人話：知識卡、hook、正文 |
| **人** | 這句話夠不夠有力、這張卡能不能發、這個詞在台灣有沒有人這樣講 |

**推論：機器只做「驗得出來」的事。** 驗不出來的（好不好看、有沒有超譯、算不算標題黨），
機器把它**標出來給人看**，不替人決定。

## 三個踩過的大坑（都寫成測試釘死了）

**一、我猜的數字，比模型的錯誤更常害死整條 pipeline。**

字級門檻、測試範圍、`steps` 每步 50 字、caption 500 字、一則 6 張卡、hook 30 字——
每一個都曾讓「本來沒問題的東西」被擋下來，然後我去怪模型。

所以現在**三種上限分住三個地方**：

| 性質 | 住在哪 | 違反時 |
| --- | --- | --- |
| **平台／物理極限**（IG 2200 字、版面塞不下） | `schemas/` 與程式 | 硬擋 |
| **編輯目標**（hook 25 字、每段 100 字） | `prompts/` | 只是建議 |
| **密度／品味** | 渲染器自動拆卡；或標出來給人看 | 不擋 |

**二、LLM 不會數中文字。**

叫它「壓到 400 字以內」，它給 729 → 717 → 561——一路逼近，永遠差一點。
**要用它做得到的方式下指令**：「寫 3 句話」「一段 2–3 句」「一句話，只講一個意思」。
真的要切在某個字數上，**那一刀由程式砍**（切在句號上，絕不切在句子中間）。

**三、規則要能被檢查，否則它只是願望。**

prompt 裡寫「前 125 字要能自己站著」→ 模型照做了，**卻是用一句廢話站著**。
現在「開頭有沒有被出處吃掉」「hook 是不是只是把 angle 換句話說」「正文有沒有分段」
全部由程式驗——**能驗的就別只靠叮嚀。**

## 資料夾

```
docs/spec.md                     ★ 資料契約與四階段流程，改動要進版號
docs/style.md                    ★ 視覺 token 與內容紅線（v1.1）
schemas/                         三份 JSON Schema + examples
prompts/                         prompt 獨立成檔，不寫死在程式裡
src/
├── errors.py                    PipelineError 與錯誤碼
├── paths.py                     輸出路徑、slug、圖檔命名、is_stale（單一事實來源）
├── schema.py                    寫檔前驗證：不符 schema 就不落地
├── llm.py                       Gemini/Anthropic（REST 直呼，沒裝 SDK）
├── ingest/read_article.py       Web Clipper 剪報正規化
├── analyze/
│   ├── extract_highlights.py    知識卡抽取
│   ├── grounding.py             每條主張對回原文（**標記，不攔截**）
│   └── locale.py                簡體 → 台灣正體（OpenCC）；中國用語只標記
├── render/
│   ├── layout.py                拆卡邏輯（純函式，測試不用開瀏覽器）
│   ├── browser.py               只驅動系統的 Edge/Chrome，**不下載**
│   └── render_cards.py          autofit 字級 + 截圖
└── compose/write_post.py        hook + 正文；IG 與 Threads 共用一份
templates/                       兩個主題（深色螢光 / 編輯大字）
out/<slug>/                      產物（不進版控）
```

## 給接手的人

1. **寫檔一律走 `schema.write_json(kind, path, data)`**——先驗證再落地。
2. **讀上游一律走 `schema.read_json(kind, path)`**——檔案不存在會給 `MISSING_INPUT`。
3. **路徑與檔名一律走 `paths.py`**，不要自己拼字串。
4. **要刪一個檔案，先問「誰 import 它」，不是「誰執行它」。**（我踩過）

失敗就 `raise PipelineError(code, message, hint)`，別回半成品。

### 幻覺防線：對照，但不攔截

每一條主張（含每一步、對照的兩邊）都帶 `evidence`（段落 index + 逐字原句）。
`grounding.review()` 逐條比對，標成三級：

| 標記 | 意義 |
| --- | --- |
| ✓ | 對得上原文 |
| ⚠ | 段落標錯或跨段落（**句子是真的**，無害） |
| ✗ | 原文中完全找不到 ← **這才是幻覺** |

**機器不判斷「中文有沒有超譯」**——那要人看並排的審稿表。
要恢復「對不上就擋」：`STRICT_GROUNDING=1`。

## LLM 供應商

預設 **Gemini**（`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`）。要換回 Claude：`LLM_PROVIDER=anthropic`。
供應商集中在 `src/llm.py`，其他模組不准直接 import SDK。**REST 直呼，沒裝 SDK**——出事時看得到原始的 HTTP 狀態碼。

沒有 key 也能跑測試——模型呼叫是注入的（`analyze(article, llm=...)`），**測試全部不打網路**。

## 為什麼不用裝瀏覽器

**因為你已經有了。** Windows 內建 Edge（Chromium 核心），排版能力跟 Playwright 自帶的那顆一樣。
`src/render/browser.py` 直接驅動系統的 Edge / Chrome，**一個位元組都不下載**。
找不到就報錯，不偷偷下載。真要用下載的那顆得明講：`set CARD_BROWSER=bundled`。

> 通則：**AI 傾向為了保險而重複安裝你已經有的東西**（瀏覽器、Docker、字體）。
> 那樣它不會錯，但成本是你在付。

## ⚠️ Windows 的編碼地雷

**`.bat` 和 `requirements.txt` 只能放 ASCII。** 它們由 cmd / pip 讀，用的是系統預設編碼（繁中是 cp950），
中文塞進去就爆炸。中文寫進 Python 或 README，**不要寫進這兩種檔案**（檔名可以是中文）。
已寫成測試：`tests/test_windows_encoding.py`。

| 套件 | 給哪個階段 | 為什麼 |
| --- | --- | --- |
| `jsonschema` | 契約 | 寫檔前驗證 |
| `python-dotenv` | 分析／文案 | 讀 `.env` 的 API key |
| `opencc-python-reimplemented` | 分析 | 簡體 → 台灣正體。**別自己手刻對照表**：一簡對多繁要看詞 |
| `playwright` | 出圖 | 驅動系統瀏覽器 |
| `pytest` | 開發 | 測試 |
