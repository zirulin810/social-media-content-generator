/* 卡片渲染 + 自動適配字級
 *
 * 核心原則（2026-07-13 與 Human 討論後定的）：
 *   **內容決定卡片，不是卡片決定內容。**
 *
 * 所以這裡不砍內容，只做兩件事：
 *   1. autofit()：二分搜尋「塞得下的最大字級」，有可讀性下限
 *   2. 塞不下（低於下限）→ 回報 overflow，由渲染器決定拆卡（見 render_cards.py）
 *
 * 字級下限 MIN_FS 是唯一的紅線：低於它就不叫圖卡，叫掃描件。
 */

const MAX_FS = 76;      // px @1080
const MIN_FS = 34;      // **硬底線**：低於這個根本不能出圖，那不叫圖卡叫掃描件
const COMFORT_FS = 44;  // **舒適下限**：低於這個就該拆卡了
//
// 兩個門檻，不是一個。
// 原本我只有 MIN_FS，於是「縮到 34px 還塞得下」就不拆——結果是一面文字牆。
// 技術上讀得到，實際上沒人會讀。34–44 之間那段「醜但不違法」的區間必須擋掉。
const esc = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

/* 讓模型可以用 **粗體** 標重點，主題 B 會渲染成螢光筆 */
const hi = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<mark>$1</mark>');

/* 封面**不吃**重點標記：那張卡的焦點是大數字，再加螢光就是兩個焦點打架。
 * 用機制擋掉，不要指望模型自己遵守約定。 */
const plain = (s) => esc(String(s ?? '').replace(/\*\*(.+?)\*\*/g, '$1'));

/* 作者可能沒有（Google 課程、官方文件、白皮書常常沒有個人作者）。
 * **沒有就整個不印**——不要留一個孤零零的「—」在那裡，那看起來像出錯。
 * （出處不會因此消失：結尾卡仍然印標題與連結。） */
const byline = (ctx) => (ctx.author ? `— ${esc(ctx.author)}` : '');

function footer(card, ctx) {
  return `<div class="footer"><span></span><span>${byline(ctx)}</span></div>`;
}

function kickrow(label, pager) {
  const p = pager ? `<span class="pager">${esc(pager)}</span>` : '';
  return `<div class="kickrow"><span class="kicker">${esc(label)}</span>${p}</div>`;
}

const TEMPLATES = {
  cover: (c, ctx) => `
    <div class="card cover">
      <div class="kicker">${esc(c.kicker || ctx.series || '')}</div>
      <div class="rule"></div>
      ${c.stat ? `<div class="stat">${esc(c.stat)}</div>` : ''}
      <div class="angle">${plain(c.angle)}</div>
      ${c.hook ? `<div class="hook">${plain(c.hook)}</div>` : ''}
      <div class="footer"><span>${esc(ctx.handle || '')}</span><span>${byline(ctx)}</span></div>
    </div>`,

  point: (c, ctx) => `
    <div class="card">
      ${kickrow(c.kicker || '重點')}
      <div class="body" style="margin-top:36px">
        <div class="title">${hi(c.title)}</div>
        <div class="text">${hi(c.body)}</div>
      </div>
      ${footer(c, ctx)}
    </div>`,

  steps: (c, ctx) => `
    <div class="card">
      ${kickrow(c.kicker || '步驟', c.pager)}
      <div class="body" style="margin-top:36px">
        <div class="title">${hi(c.title)}</div>
        <ol class="steps">
          ${c.steps.map((s, i) => `<li><span class="num">${(c.startIndex || 1) + i}</span><span>${hi(s.text ?? s)}</span></li>`).join('')}
        </ol>
      </div>
      ${footer(c, ctx)}
    </div>`,

  contrast: (c, ctx) => `
    <div class="card">
      ${kickrow(c.kicker || '常見錯法')}
      <div class="body" style="margin-top:36px">
        <div class="title">${hi(c.title)}</div>
        <div class="vs">
          <div class="side wrong"><span class="lab">✗</span><span>${hi(c.wrong.text ?? c.wrong)}</span></div>
          <div class="side right"><span class="lab">✓</span><span>${hi(c.right.text ?? c.right)}</span></div>
        </div>
      </div>
      ${footer(c, ctx)}
    </div>`,

  quote: (c, ctx) => `
    <div class="card">
      ${kickrow(c.kicker || '金句')}
      <div class="quote">
        <div class="mark"></div>
        <div class="q">${hi(c.text)}</div>
      </div>
      ${footer(c, ctx)}
    </div>`,

  /* 結尾卡＝整則貼文唯一的出處（caption 不再帶）。
   * **標題與連結一定要有；作者沒有就不印那一行**，不要留空行或孤零零的破折號。 */
  outro: (c, ctx) => `
    <div class="card outro">
      <div class="kicker">出處</div>
      <div class="body" style="margin-top:36px">
        <div class="src">${esc(ctx.title)}</div>
        ${ctx.author || ctx.series
          ? `<div class="meta">${esc(ctx.author || '')}${
              ctx.author && ctx.series ? '<br>' : ''
            }${esc(ctx.series || '')}</div>`
          : ''}
        <div class="url">${esc(ctx.url)}</div>
      </div>
      <div class="handle">${esc(ctx.handle || '')}</div>
    </div>`,
};

/* 溢出偵測。
 *
 * ⚠️ 這裡曾經有一個 bug，害第 11 張卡被切掉：
 *
 *   原本量的是 `card.scrollHeight <= card.clientHeight`。
 *   但 `.card` 是 flex 項目、`min-height` 預設 `auto`——**它會被內容撐大**
 *   （撐到 1300px），再被 body 的 overflow:hidden 裁掉。
 *   於是檢查變成 1300 <= 1300 → 回報「塞得下」。**量尺跟著卡片一起長高，永遠量不出溢出。**
 *
 * 正確的做法是量**視窗**：body 的高度是固定的（1080/1350），它不會跟著長。
 */
function overflows(root) {
  const card = root.querySelector('.card');
  const limitH = root.clientHeight;      // 視窗高度——這個不會變
  const limitW = root.clientWidth;
  return (
    root.scrollHeight > limitH + 1 ||                        // 內容把 body 撐開了
    card.getBoundingClientRect().height > limitH + 1 ||      // 卡片自己長高了
    card.scrollHeight > limitH + 1 ||
    root.scrollWidth > limitW + 1
  );
}

/* 二分搜尋塞得下的最大字級。回傳 {fs, overflow}。 */
function autofit(root) {
  let lo = MIN_FS, hi_ = MAX_FS, best = MIN_FS;
  const fits = (fs) => {
    root.style.setProperty('--fs', fs + 'px');
    return !overflows(root);
  };
  for (let i = 0; i < 12 && lo <= hi_; i++) {
    const mid = Math.floor((lo + hi_) / 2);
    if (fits(mid)) { best = mid; lo = mid + 1; } else { hi_ = mid - 1; }
  }
  root.style.setProperty('--fs', best + 'px');
  const overflow = overflows(root);   // 連下限都塞不下 → 要拆卡
  return { fs: best, overflow };
}

/* 獨立稽核：掃過每一個元素，回報有沒有人跑出視窗外。
 *
 * 為什麼要這個？因為 overflows() 是靠 CSS/flex 的行為推論出來的，
 * 而**我已經在那上面栽過一次**（卡片自己長高，量尺跟著長高，永遠量不出溢出）。
 *
 * 這支不推論，直接量每個元素的 bounding box。它跟 CSS 怎麼寫無關——
 * 就算 flex 的行為又出乎我意料，這裡還是抓得到。
 * render_cards.py 在截圖前會呼叫它，任何一個元素超出邊界就拒絕出圖。
 */
function audit(root) {
  const W = root.clientWidth, H = root.clientHeight;
  let worst = null, maxOver = 0;
  for (const el of root.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    const over = Math.max(r.bottom - H, r.right - W, -r.top, -r.left);
    if (over > maxOver) {
      maxOver = over;
      worst = { tag: el.className || el.tagName, over: Math.round(over),
                text: (el.textContent || '').slice(0, 30) };
    }
  }
  return { clipped: maxOver > 1, overBy: Math.round(maxOver), worst };
}

function renderCard(card, ctx) {
  const fn = TEMPLATES[card.type];
  if (!fn) throw new Error('未知的卡型：' + card.type);
  document.body.innerHTML = fn(card, ctx);
  const r = autofit(document.body);
  r.audit = audit(document.body);          // 獨立稽核，不信任 autofit 的判斷
  window.__fit = r;
  return r;
}

window.renderCard = renderCard;
window.CARD_MIN_FS = MIN_FS;
window.CARD_COMFORT_FS = COMFORT_FS;
