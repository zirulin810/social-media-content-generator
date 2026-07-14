# 自動化輸出

一篇文章（本機 markdown）→ 重點金句 → 社群圖卡 → 貼文文案。目標平台：Instagram、Threads。

**MVP 邊界**：只吃文字。影片不在範圍——現成工具已經能把 YouTube 轉成文章，那一段外包出去，
這條 pipeline 從「已經是文字」的地方開始。若來源是影片轉出的文章，`source.url` 仍指回原影片。

任務佇列在 Obsidian：`02_Project/自動化輸出/`（母專案筆記 `02_Project/自動化輸出.md`）。
**資料契約是這個專案的核心**，先讀 [`docs/spec.md`](docs/spec.md)。

## 現況

骨架與契約已就位（v2.0）；四個階段的實作分別由各自的任務筆記負責，尚未實作。

## 安裝

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
playwright install chromium       # 出圖階段才需要
copy .env.example .env            # 填入 ANTHROPIC_API_KEY
```

## 用法

```bash
python -m src.cli "文章.md"                  # 全跑
python -m src.cli "文章.md" --dry-run        # 只到 highlights，快速看分析品質
python -m src.cli "文章.md" --only analyze   # 只跑單一階段
python -m src.cli "文章.md" --force          # 重跑已有產物的階段
python -m src.cli "文章.md" --ratio 1:1      # 圖卡比例（預設 4:5）
```

跑完後 `out/<slug>/` 就是可直接發布的一包。

## 資料夾

```
docs/spec.md                     ★ 資料契約與四階段流程，改動要進版號
schemas/                         三份 JSON Schema + examples
src/
├── errors.py                    PipelineError 與錯誤碼
├── paths.py                     輸出路徑、slug、圖檔命名（單一事實來源）
├── schema.py                    寫檔前驗證：不符 schema 就不落地
├── cli.py                       ← 任務〈端到端 CLI 串接〉
├── ingest/read_article.py       ← 任務〈文章讀取與正規化〉
├── analyze/extract_highlights.py ← 任務〈重點分析與金句抽取〉
├── render/render_cards.py       ← 任務〈圖卡渲染器〉
└── compose/write_post.py        ← 任務〈貼文文案產生器〉
templates/                       ← 任務〈社群圖卡版型設計〉（HTML/CSS + design token）
prompts/                         ← prompt 獨立成檔，不寫死在程式裡
out/<slug>/                      產物（不進版控）
tests/test_contract.py           契約自測
```

## 給接手的模組作者

三條規矩，守住就不會互相踩腳：

1. **寫檔一律走 `schema.write_json(kind, path, data)`**——先驗證再落地，不符 schema 的東西進不了 `out/`。
2. **讀上游一律走 `schema.read_json(kind, path)`**——檔案不存在會給你 `MISSING_INPUT`，不會讓你拿 None 亂跑。
3. **路徑與檔名一律走 `paths.py`**，不要自己拼字串。

失敗就 `raise PipelineError(code, message, hint)`，別回半成品。錯誤碼表在 spec。

**幻覺防線**：金句必須對得回原文。`analyze.assert_grounded()` 會檢查每句金句的 `source_text`
逐字出現在 `article.body`、且確實落在它宣稱的段落裡；標了 `verbatim=true` 就不准偷改字。
對不上一律 `QUOTE_NOT_GROUNDED`。

```bash
pytest -q     # 契約自測，含「憑空捏造的金句必須被擋下」
```

## LLM 供應商

預設 **Gemini**（`GEMINI_API_KEY` 或 `GOOGLE_API_KEY`，兩個名字都認）。
供應商集中在 `src/llm.py`，其他模組不准直接 import 任何 SDK。要換回 Claude 就設 `LLM_PROVIDER=anthropic`。

用 REST 直呼，沒裝 SDK——少一個依賴，出事時看得到原始的 HTTP 狀態碼。

```bash
python scripts\smoke_test.py     # 或雙擊「測試Gemini.bat」
```

煙霧測試會依序查：key 找不找得到 → 這把 key 能用哪些模型 → 呼叫得通嗎 → 跑一篇真實文章的金句抽取。
**模型名稱會改**，所以它會直接問 API 有哪些模型可用，不把名字寫死在腦子裡。

## ⚠️ Windows 的編碼地雷

**`.bat` 和 `requirements.txt` 只能放 ASCII。**

這兩種檔案由 Windows 原生工具讀（cmd、pip），它們**不用 UTF-8，用系統預設編碼**（繁中是 cp950）。中文塞進去就爆炸：

- `.bat` 裡的中文 → cmd 逐位元組解析錯位，`echo` 被吃成 `ho`
- `requirements.txt` 裡的中文註解 → `UnicodeDecodeError: 'cp950' codec can't decode byte 0x9e`

**中文寫進 Python 或 README，不要寫進這兩種檔案。** 檔名可以是中文（那是檔案系統的事）。
已寫成測試：`tests/test_windows_encoding.py`。

依賴的說明（原本在 requirements.txt 的中文註解）：

| 套件 | 給哪個階段 | 為什麼 |
| --- | --- | --- |
| `jsonschema` | 契約 | 寫檔前驗證，不符 schema 就不落地 |
| `python-dotenv` | 分析／文案 | 讀 `.env` 的 API key |
| `playwright` | 出圖 | 無頭瀏覽器截圖 |
| `pytest` | 開發 | 79 條測試 |

**沒有 LLM SDK**——`src/llm.py` 用 REST 直呼。少一個依賴，出事時看得到原始的 HTTP 狀態碼。
**沒有 markdown 解析器、沒有 frontmatter 套件**——Web Clipper 的格式夠單純，regex 就夠。

## 出圖

```bash
pip install -r requirements.txt
playwright install chromium        # 第一次要裝瀏覽器
出圖.bat                            # 或 python scripts\render_sample.py b 1x1
```

**塞不下就拆卡，不砍內容。** 字級由 `templates/card.js` 二分搜尋（34–76px）；
連下限都塞不下 → `layout.py` 把卡拆開（步驟卡切段、重點卡從句號切），編號續接、標上 1/2、2/2。
拆不動的（金句、對照）→ 丟 `RENDER_OVERFLOW`，回頭改文案，**不默默截掉半個字送出去**。

## 進度

| 階段 | 狀態 |
| --- | --- |
| 1 ingest（`read_article.py`） | ✅ 完成，5 篇真實 Clippings 跑通 |
| 2 analyze（`extract_highlights.py`） | ✅ Gemini 跑通，內容品質達標 |
| 3 render（`render_cards.py` + `templates/`） | ⚠️ 程式完成，**待你跑 `出圖.bat` 驗證** |
| 4 compose | 未開始 |

沒有 key 也能跑測試——模型呼叫是注入的（`analyze(article, llm=...)`），49 條測試全部不打網路。
