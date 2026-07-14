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

const MAX_FS = 76;   // px @1080
const MIN_FS = 34;   // 可讀性下限——1080 的圖縮到手機上，這是極限
const esc = (s) => String(s ?? '').replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

/* 讓模型可以用 **粗體** 標重點，主題 B 會渲染成螢光筆 */
const hi = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<mark>$1</mark>');

/* 封面**不吃**重點標記：那張卡的焦點是大數字，再加螢光就是兩個焦點打架。
 * 用機制擋掉，不要指望模型自己遵守約定。 */
const plain = (s) => esc(String(s ?? '').replace(/\*\*(.+?)\*\*/g, '$1'));

function footer(card, ctx) {
  const author = esc(ctx.author || '');
  return `<div class="footer"><span></span><span>— ${author}</span></div>`;
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
      <div class="footer"><span>${esc(ctx.handle || '')}</span><span>— ${esc(ctx.author || '')}</span></div>
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

  outro: (c, ctx) => `
    <div class="card outro">
      <div class="kicker">出處</div>
      <div class="body" style="margin-top:36px">
        <div class="src">${esc(ctx.title)}</div>
        <div class="meta">${esc(ctx.author)}${ctx.series ? `<br>${esc(ctx.series)}` : ''}</div>
        <div class="url">${esc(ctx.url)}</div>
      </div>
      <div class="handle">${esc(ctx.handle || '')}</div>
    </div>`,
};

/* 二分搜尋塞得下的最大字級。回傳 {fs, overflow}。 */
function autofit(root) {
  let lo = MIN_FS, hi_ = MAX_FS, best = MIN_FS;
  const fits = (fs) => {
    root.style.setProperty('--fs', fs + 'px');
    const card = root.querySelector('.card');
    return card.scrollHeight <= card.clientHeight + 1 && card.scrollWidth <= card.clientWidth + 1;
  };
  for (let i = 0; i < 12 && lo <= hi_; i++) {
    const mid = Math.floor((lo + hi_) / 2);
    if (fits(mid)) { best = mid; lo = mid + 1; } else { hi_ = mid - 1; }
  }
  const overflow = !fits(best);   // 連下限都塞不下
  root.style.setProperty('--fs', best + 'px');
  return { fs: best, overflow };
}

function renderCard(card, ctx) {
  const fn = TEMPLATES[card.type];
  if (!fn) throw new Error('未知的卡型：' + card.type);
  document.body.innerHTML = fn(card, ctx);
  const r = autofit(document.body);
  window.__fit = r;                       // Playwright 讀這個判斷要不要拆卡
  return r;
}

window.renderCard = renderCard;
window.CARD_MIN_FS = MIN_FS;
