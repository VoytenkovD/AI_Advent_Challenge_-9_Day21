(function () {
  'use strict';

  var STRATS = ['fixed', 'structure'];
  var NAMES = { fixed: 'fixed', structure: 'structure' };
  var state = { overview: null, questions: [], logAfter: 0, polling: null, chapter: 1, selChunk: null, compare: null };

  // ───────────── утилиты ─────────────
  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v);
    });
    for (var i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c == null || c === false) return;
    if (Array.isArray(c)) { c.forEach(function (x) { add(el, x); }); return; }
    el.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
  }
  function $(id) { return document.getElementById(id); }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function api(path, body) {
    return fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {})
      .then(function (r) { return r.json().then(function (j) { if (!r.ok && j.error) throw new Error(j.error); return j; }); });
  }
  function num(n) { return n == null ? '—' : Number(n).toLocaleString('ru-RU'); }
  function pct(x) { return x == null ? '—' : Math.round(x * 100) + '%'; }
  function kv(pairs) {
    var box = h('div', { class: 'kv' });
    pairs.forEach(function (p) { box.appendChild(h('div', { class: 'k', text: p[0] })); box.appendChild(h('div', { class: 'v' }, p[1])); });
    return box;
  }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // ───────────── вкладки ─────────────
  var loaders = {};
  function show(tab) {
    if (!loaders[tab]) tab = 'index';
    document.querySelectorAll('.tab').forEach(function (b) { b.classList.toggle('active', b.dataset.tab === tab); });
    document.querySelectorAll('.view').forEach(function (v) { v.classList.toggle('active', v.id === 'view-' + tab); });
    if (location.hash !== '#' + tab) history.replaceState(null, '', '#' + tab);
    lsSet('tab', tab);
    loaders[tab]();
  }
  document.querySelectorAll('.tab').forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.tab); }); });

  // ───────────── шапка ─────────────
  function renderPills(o) {
    var box = clear($('pills'));
    var ol = o.ollama || {};
    box.appendChild(h('span', { class: 'pill ' + (ol.ok ? 'ok' : 'err'), text: ol.ok ? 'ollama ' + ol.version : 'ollama offline' }));
    if (ol.ok) box.appendChild(h('span', { class: 'pill ' + (ol.model_pulled ? 'ok' : 'err'), text: ol.model }));
    if (ol.loaded) box.appendChild(h('span', { class: 'pill ' + (ol.on_gpu ? 'ok' : 'err'), text: (ol.on_gpu ? 'GPU ' : 'CPU/GPU ') + ol.vram_mb + ' МБ' }));
    var built = STRATS.filter(function (s) { return o.variants[s].built; }).length;
    var running = o.job && o.job.state === 'running';
    box.appendChild(h('span', { class: 'pill ' + (running ? 'run' : built === 2 ? 'ok' : 'err'), text: running ? 'индексация…' : 'индекс ' + built + '/2' }));
    if (o.document) $('brand-sub').textContent = o.document.title.split(';')[0] + ' · главы 1–' + o.document.chapters.length + ' · ' + (ol.model || '');
  }

  function loadOverview() {
    return api('/api/overview').then(function (o) { state.overview = o; renderPills(o); return o; });
  }

  // ═════════════ 01 ИНДЕКС ═════════════
  function renderPipeline(o) {
    var steps = [
      ['Gutenberg', 'pg2701.txt, зеркало, один раз'],
      ['очистка', 'шапка, лицензия, оглавление'],
      ['главы 1–' + o.max_chapters, 'главы → абзацы'],
      ['<span>fixed</span> · <span>structure</span>', 'две стратегии нарезки', 'split'],
      [(o.ollama.model || 'bge-m3'), 'Ollama, видеокарта'],
      ['L2-норма', 'cos = скалярное произв.'],
      ['SQLite', 'data/index.db']
    ];
    var box = clear($('pipeline'));
    steps.forEach(function (s, i) {
      var t = h('div', { class: 'stage__t' });
      t.innerHTML = s[0];  // только наши статичные строки
      box.appendChild(h('div', { class: 'stage' + (s[2] ? ' ' + s[2] : '') }, h('div', { class: 'stage__n', text: '0' + (i + 1) }), t, h('div', { class: 'stage__d', text: s[1] })));
    });
  }

  function renderIndex(o) {
    renderPipeline(o);
    var d = o.document;
    clear($('doc-card')).appendChild(d ? kv([
      ['source', d.source], ['title', d.title], ['author', d.author],
      ['главы', '1–' + d.chapters.length + ' из 135'], ['символов', num(d.chars)], ['≈ страниц', num(d.pages)],
      ['sha256', d.sha256.slice(0, 16) + '…']
    ]) : h('div', { class: 'muted', text: 'Книга ещё не загружена — нажмите «Построить индекс».' }));
    var ol = o.ollama;
    clear($('model-card')).appendChild(kv([
      ['ollama', ol.ok ? ol.version + ' · ' + ol.url : (ol.error || 'недоступна')],
      ['модель', ol.model + (ol.model_pulled ? '' : ' (не скачана)')],
      ['видеопамять', ol.loaded ? ol.vram_mb + ' / ' + ol.size_mb + ' МБ' + (ol.on_gpu ? ' · целиком на GPU' : ' · частично CPU') : 'модель не загружена'],
      ['вектор', (o.variants.structure.dim || o.variants.fixed.dim || '—') + ' чисел, L2 = 1'],
      ['символов/токен', o.cpt || 'калибруется при индексации']
    ]));

    var cards = clear($('variant-cards'));
    STRATS.forEach(function (s) {
      var v = o.variants[s];
      cards.appendChild(h('div', { class: 'panel variant variant--' + s },
        h('div', { class: 'variant__head' },
          h('div', null, h('div', { class: 'variant__name', text: s }), h('div', { class: 'muted small', text: v.title + ' — ' + v.about })),
          h('span', { class: 'badge ' + (v.built ? 'ok' : 'no'), text: v.built ? 'в индексе' : 'нет' })),
        v.built ? h('div', { class: 'stats' },
          stat(num(v.chunks), 'чанков'), stat(num(v.tokens), 'токенов'), stat(num(v.model_tokens), 'токенов модели'),
          stat(v.dim, 'размерность'), stat(v.seconds + ' с', 'время'), stat(v.fingerprint, 'отпечаток')) : null));
    });
    renderSample(o.sample);
    $('btn-build').disabled = $('btn-rebuild').disabled = o.job.state === 'running';
    if (o.job.state === 'running') startPolling();
  }

  function stat(v, k) { return h('div', { class: 'stat' }, h('div', { class: 'stat__v', text: v }), h('div', { class: 'stat__k', text: k })); }

  function vecBars(head) {
    var max = Math.max.apply(null, head.map(Math.abs)) || 1;
    var box = h('div', { class: 'vec', title: 'первые ' + head.length + ' координат вектора' });
    head.forEach(function (x) { box.appendChild(h('i', { class: x < 0 ? 'neg' : '', style: 'height:' + Math.max(4, Math.abs(x) / max * 100) + '%' })); });
    return box;
  }

  function chunkMeta(c) {
    return kv([
      ['chunk_id', c.chunk_id], ['strategy', c.strategy], ['source', c.source], ['title', c.title], ['author', c.author],
      ['section', c.section], ['chapters', c.chapters.join(', ')],
      ['start / body / end', c.start + ' / ' + c.body_start + ' / ' + c.end],
      ['tokens', c.tokens + (c.overlap_tokens ? ' (перекрытие ' + c.overlap_tokens + ')' : '')],
      ['начало / конец', (c.starts_mid_sentence ? '✂ посреди предложения' : 'с начала предложения') + ' / ' + (c.ends_mid_sentence ? '✂ обрыв' : 'конец предложения')],
      ['vector', (c.dim || (c.vector_head || []).length) + ' чисел' + (c.vector_norm ? ', |v| = ' + c.vector_norm : '')]
    ]);
  }

  function chunkText(c) {
    var ovl = c.body_start - c.start;
    return h('div', { class: 'sample__text' }, ovl > 0 ? h('span', { class: 'ovl', title: 'перекрытие с предыдущим чанком', text: c.text.slice(0, ovl) }) : null, c.text.slice(ovl));
  }

  function renderSample(c) {
    var box = clear($('sample'));
    if (!c) { box.appendChild(h('div', { class: 'muted', text: 'Появится после индексации.' })); return; }
    box.appendChild(h('div', { class: 'sample' }, h('div', null, chunkMeta(c), vecBars(c.vector_head)), chunkText(c)));
  }

  function logLine(e) {
    var t = new Date(e.t * 1000).toLocaleTimeString('ru-RU');
    return h('div', { class: 'ln' }, h('span', { class: 't', text: t }), h('span', { class: 'tag ' + e.stage, text: '[' + e.stage + ']' }), ' ' + e.msg);
  }

  function poll() {
    api('/api/index/status?after=' + state.logAfter).then(function (s) {
      var con = $('console');
      if (state.logAfter === 0 && s.log.length) clear(con);
      s.log.forEach(function (e) { con.appendChild(logLine(e)); });
      state.logAfter += s.log.length;
      con.scrollTop = con.scrollHeight;
      var p = s.progress;
      $('progress-bar').style.width = s.state === 'done' ? '100%' : p ? Math.round(p.done / p.total * 100) + '%' : '3%';
      $('progress-text').textContent = s.state === 'running' ? (p ? p.stage + ': ' + p.done + ' / ' + p.total : 'идёт…') :
        s.state === 'done' ? 'готово' : s.state === 'error' ? 'ошибка' : 'ожидание';
      $('log-state').textContent = s.state;
      if (s.state !== 'running') {
        stopPolling();
        state.compare = null;
        loadOverview().then(renderIndex);
      }
    }).catch(function () {});
  }
  function startPolling() { if (!state.polling) { state.polling = setInterval(poll, 1000); poll(); } }
  function stopPolling() { clearInterval(state.polling); state.polling = null; }

  function build(rebuild) {
    if (rebuild && !confirm('Стереть индекс и посчитать всё заново?')) return;
    state.logAfter = 0;
    clear($('console'));
    api('/api/index', { rebuild: rebuild }).then(function () {
      $('btn-build').disabled = $('btn-rebuild').disabled = true;
      startPolling();
    }).catch(function (e) { alert(e.message); });
  }
  $('btn-build').addEventListener('click', function () { build(false); });
  $('btn-rebuild').addEventListener('click', function () { build(true); });

  loaders.index = function () { loadOverview().then(renderIndex); };

  // ═════════════ 02 НАРЕЗКА ═════════════
  function fillChapterSelect() {
    var sel = $('ch-select');
    if (sel.options.length || !state.overview || !state.overview.document) return;
    state.overview.document.chapters.forEach(function (c) { sel.appendChild(h('option', { value: c.num, text: c.label })); });
  }

  function renderColumn(s, data) {
    var text = data.text, off = data.offset, len = text.length;
    var chunks = data.strategies[s];
    // точки разреза: начало перекрытия, начало тела, конец каждого чанка (в пределах главы)
    var cuts = [0, len];
    chunks.forEach(function (c) {
      [c.start, c.body_start, c.end].forEach(function (p) { p -= off; if (p > 0 && p < len) cuts.push(p); });
    });
    cuts = cuts.filter(function (v, i, a) { return a.indexOf(v) === i; }).sort(function (a, b) { return a - b; });
    var reader = h('div', { class: 'reader' });
    var starts = {};
    chunks.forEach(function (c) { var b = Math.max(0, c.body_start - off); (starts[b] = starts[b] || []).push(c); });
    for (var i = 0; i < cuts.length - 1; i++) {
      var a = cuts[i], b = cuts[i + 1];
      (starts[a] || []).forEach(function (c) {
        reader.appendChild(h('span', { class: 'mark' + (c.starts_mid_sentence && c.body_start === c.start ? ' cutm' : ''), 'data-id': c.chunk_id,
          title: c.chunk_id + ' · ' + c.tokens + ' ток.', text: '#' + c.seq, onclick: function () { selectChunk(c.chunk_id); } }));
      });
      var cover = chunks.filter(function (c) { return c.start - off < b && c.end - off > a; });
      if (!cover.length) { reader.appendChild(document.createTextNode(text.slice(a, b))); continue; }
      var main = cover[cover.length - 1];
      var inOverlap = cover.length > 1 || (main.body_start - off > a && main.start - off <= a);
      var seg = h('span', { class: 'seg ' + (main.seq % 2 ? 'b' : 'a') + (inOverlap ? ' ovl' : ''), 'data-id': main.chunk_id,
        title: cover.map(function (c) { return c.chunk_id; }).join(' + '), text: text.slice(a, b) });
      seg.addEventListener('click', function (id) { return function () { selectChunk(id); }; }(main.chunk_id));
      reader.appendChild(seg);
      var ends = chunks.filter(function (c) { return c.end - off === b && c.ends_mid_sentence; });
      if (ends.length) reader.appendChild(h('span', { class: 'cut', title: 'чанк обрывается посреди предложения', text: '✂' }));
    }
    var mid = chunks.filter(function (c) { return c.ends_mid_sentence; }).length;
    var cross = chunks.filter(function (c) { return c.chapters.length > 1; }).length;
    return h('div', { class: 'panel col col--' + s },
      h('div', { class: 'col__head' }, h('b', { text: s }),
        h('span', { class: 'muted', text: chunks.length + ' чанков · обрывов ' + mid + ' · захватывают 2 главы: ' + cross })),
      reader);
  }

  function loadChapter(n) {
    state.chapter = n;
    $('ch-select').value = n;
    lsSet('chapter', n);
    var cols = clear($('chunk-cols'));
    cols.appendChild(h('div', { class: 'empty', text: 'Загружаю главу…' }));
    return api('/api/chapter?num=' + n).then(function (d) {
      clear(cols);
      STRATS.forEach(function (s) { cols.appendChild(renderColumn(s, d)); });
      if (state.selChunk) highlight(state.selChunk, true);
    }).catch(function (e) { clear(cols).appendChild(h('div', { class: 'empty', text: e.message })); });
  }

  function highlight(id, scroll) {
    document.querySelectorAll('.seg.sel').forEach(function (e) { e.classList.remove('sel'); });
    var segs = document.querySelectorAll('.seg[data-id="' + id + '"]');
    segs.forEach(function (e) { e.classList.add('sel'); });
    if (scroll && segs[0]) segs[0].scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function selectChunk(id) {
    state.selChunk = id;
    highlight(id, false);
    api('/api/chunk?id=' + encodeURIComponent(id)).then(function (c) {
      var box = $('chunk-detail');
      box.hidden = false;
      clear(box).appendChild(h('div', { class: 'panel__title', text: 'Чанк ' + c.chunk_id }));
      box.appendChild(h('div', { class: 'sample' }, h('div', null, chunkMeta(c), vecBars(c.vector_head)), chunkText(c)));
    });
  }

  $('ch-select').addEventListener('change', function () { state.selChunk = null; loadChapter(+this.value); });
  $('ch-prev').addEventListener('click', function () { if (state.chapter > 1) { state.selChunk = null; loadChapter(state.chapter - 1); } });
  $('ch-next').addEventListener('click', function () {
    var max = state.overview && state.overview.document ? state.overview.document.chapters.length : 1;
    if (state.chapter < max) { state.selChunk = null; loadChapter(state.chapter + 1); }
  });

  loaders.chunks = function () {
    (state.overview ? Promise.resolve(state.overview) : loadOverview()).then(function (o) {
      if (!o.document) { clear($('chunk-cols')).appendChild(h('div', { class: 'empty', text: 'Сначала постройте индекс.' })); return; }
      fillChapterSelect();
      loadChapter(state.chapter || +(lsGet('chapter') || 1));
    });
  };

  function openInChunks(c) {
    state.selChunk = c.chunk_id;
    state.chapter = c.chapters[0];
    show('chunks');
    setTimeout(function () { selectChunk(c.chunk_id); }, 400);
  }

  // ═════════════ 03 СРАВНЕНИЕ ═════════════
  var METRICS = [
    ['чанков', function (m) { return m.structure.chunks; }, num, null],
    ['токенов всего', function (m) { return m.structure.tokens_total; }, num, -1],
    ['средний размер', function (m) { return m.structure.tokens_avg; }, num, null],
    ['медиана / σ', function (m) { return m.structure.tokens_median + ' / ' + m.structure.tokens_stdev; }, String, null],
    ['мин – макс', function (m) { return m.structure.tokens_min + ' – ' + m.structure.tokens_max; }, String, null],
    ['в диапазоне', function (m) { return m.structure.in_range; }, pct, 1],
    ['перекрытие', function (m) { return m.structure.overlap_share; }, pct, -1],
    ['обрываются посреди предложения', function (m) { return m.structure.ends_mid_sentence; }, pct, -1],
    ['начинаются посреди предложения', function (m) { return m.structure.starts_mid_sentence; }, pct, -1],
    ['захватывают две главы', function (m) { return m.structure.cross_chapter; }, pct, -1],
    ['время индексации, с', function (m) { return m.structure.seconds; }, String, -1],
    ['hit@1', function (m) { return m.retrieval.hit1; }, pct, 1],
    ['hit@3', function (m) { return m.retrieval.hit3; }, pct, 1],
    ['hit@5', function (m) { return m.retrieval.hit5; }, pct, 1],
    ['MRR@10', function (m) { return m.retrieval.mrr10; }, String, 1],
    ['hit@1, вопросы по-русски', function (m) { return m.retrieval.hit1_ru; }, pct, 1],
    ['hit@1, вопросы по-английски', function (m) { return m.retrieval.hit1_en; }, pct, 1]
  ];

  function metricsTable(st) {
    var tbl = h('table', { class: 't' }, h('tr', null, h('th', { text: 'метрика' }), STRATS.map(function (s) { return h('th', { class: s, text: s }); })));
    METRICS.forEach(function (m) {
      var vals = STRATS.map(function (s) { return st[s] ? m[1](st[s]) : null; });
      var win = null;
      if (m[3] && typeof vals[0] === 'number' && typeof vals[1] === 'number' && vals[0] !== vals[1]) {
        win = (vals[0] > vals[1]) === (m[3] > 0) ? 0 : 1;
      }
      tbl.appendChild(h('tr', null, h('td', { text: m[0] }), vals.map(function (v, i) {
        return h('td', { class: 'num' + (win === i ? ' win' : ''), text: v == null ? '—' : m[2](v) });
      })));
    });
    return tbl;
  }

  function svg(w, ht, children) {
    var s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    s.setAttribute('viewBox', '0 0 ' + w + ' ' + ht);
    s.setAttribute('width', '100%');
    children.forEach(function (c) { s.appendChild(c); });
    return s;
  }
  function sv(tag, attrs, text) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  function cssVar(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }

  function histogram(s, hist, range) {
    var W = 520, H = 170, pad = 26, maxC = Math.max.apply(null, hist.map(function (b) { return b.count; })) || 1;
    var bins = [];
    for (var f = 0; f <= 550; f += 50) bins.push(f);
    var bw = (W - pad * 2) / bins.length;
    var color = cssVar('--' + s), els = [];
    els.push(sv('rect', { x: pad + (range[0] / 50) * bw, y: 8, width: ((range[1] - range[0]) / 50) * bw, height: H - pad - 8, fill: 'rgba(57,229,140,.06)', stroke: 'rgba(57,229,140,.25)', 'stroke-dasharray': '3 3' }));
    bins.forEach(function (f, i) {
      var b = hist.filter(function (x) { return x.from === f; })[0];
      var c = b ? b.count : 0, bh = (c / maxC) * (H - pad - 16);
      var r = sv('rect', { x: pad + i * bw + 2, y: H - pad - bh, width: bw - 4, height: bh, fill: color, opacity: .85 });
      r.appendChild(sv('title', {}, f + '–' + (f + 50) + ' токенов: ' + c + ' чанков'));
      els.push(r);
      if (c) els.push(sv('text', { x: pad + i * bw + bw / 2, y: H - pad - bh - 4, 'text-anchor': 'middle' }, c));
      els.push(sv('text', { x: pad + i * bw + bw / 2, y: H - 8, 'text-anchor': 'middle' }, f));
    });
    els.push(sv('line', { x1: pad, x2: W - pad, y1: H - pad, y2: H - pad, stroke: cssVar('--line-2') }));
    return h('div', null, h('div', { class: 'col__head' }, h('b', { style: 'color:var(--' + s + ')', text: s }),
      h('span', { class: 'muted', text: 'зелёная зона — ' + range[0] + '–' + range[1] + ' токенов' })), svg(W, H, els));
  }

  function retrievalChart(st) {
    var keys = [['hit1', 'hit@1'], ['hit3', 'hit@3'], ['hit5', 'hit@5'], ['mrr10', 'MRR@10']];
    var W = 520, H = 200, pad = 30, gw = (W - pad * 2) / keys.length, els = [];
    [0, .25, .5, .75, 1].forEach(function (y) {
      var yy = H - pad - y * (H - pad - 14);
      els.push(sv('line', { x1: pad, x2: W - pad, y1: yy, y2: yy, stroke: cssVar('--line'), 'stroke-dasharray': y ? '2 4' : '' }));
      els.push(sv('text', { x: pad - 6, y: yy + 3, 'text-anchor': 'end' }, Math.round(y * 100)));
    });
    keys.forEach(function (k, i) {
      STRATS.forEach(function (s, j) {
        var v = st[s] ? st[s].retrieval[k[0]] || 0 : 0, bh = v * (H - pad - 14), x = pad + i * gw + 10 + j * ((gw - 20) / 2);
        var r = sv('rect', { x: x, y: H - pad - bh, width: (gw - 20) / 2 - 3, height: bh, fill: cssVar('--' + s) });
        r.appendChild(sv('title', {}, s + ' ' + k[1] + ': ' + (k[0] === 'mrr10' ? v : Math.round(v * 100) + '%')));
        els.push(r);
        els.push(sv('text', { x: x + ((gw - 20) / 2 - 3) / 2, y: H - pad - bh - 4, 'text-anchor': 'middle' }, k[0] === 'mrr10' ? v.toFixed(2) : Math.round(v * 100)));
      });
      els.push(sv('text', { x: pad + i * gw + gw / 2, y: H - 10, 'text-anchor': 'middle' }, k[1]));
    });
    var n = (st.fixed || st.structure).retrieval.questions;
    return h('div', null, svg(W, H, els), h('div', { class: 'muted small', text: n + ' контрольных вопросов из eval/questions.json; место засчитывается, если чанк захватывает нужную главу.' }));
  }

  function rankChip(r) {
    if (!r) return h('span', { class: 'rank miss', text: '>10' });
    return h('span', { class: 'rank ' + (r === 1 ? 'r1' : r <= 3 ? 'r3' : 'r10'), text: '#' + r });
  }

  function questionsTable(c) {
    var tbl = h('table', { class: 't' }, h('tr', null, h('th', { text: 'вопрос' }), h('th', { text: 'глава' }),
      STRATS.map(function (s) { return h('th', { class: s, text: s }); }), h('th', { text: 'топ-1 у structure' })));
    c.questions.forEach(function (q) {
      tbl.appendChild(h('tr', null,
        h('td', null, h('span', { class: 'lang', text: q.lang }), ' ', q.q, q.verified ? null : h('div', { class: 'flag warn', text: 'не подтверждён текстом: ' + q.missing.join(', ') })),
        h('td', { class: 'num', text: q.chapters.join(', ') }),
        STRATS.map(function (s) { return h('td', { class: 'num' }, q.by[s] ? rankChip(q.by[s].rank) : '—'); }),
        h('td', { class: 'muted small', text: q.by.structure ? q.by.structure.top_section : '' })));
    });
    return tbl;
  }

  function renderCompare(c) {
    var ul = clear($('conclusion'));
    if (!Object.keys(c.strategies).length) { ul.appendChild(h('li', { class: 'muted', text: 'Индекс ещё не построен.' })); return; }
    c.conclusion.forEach(function (l) { ul.appendChild(h('li', { text: l })); });
    $('cmp-ms').textContent = 'посчитано за ' + c.ms + ' мс';
    clear($('metrics-table')).appendChild(metricsTable(c.strategies));
    clear($('retrieval-chart')).appendChild(retrievalChart(c.strategies));
    var hb = clear($('histograms'));
    STRATS.forEach(function (s) { if (c.strategies[s]) hb.appendChild(histogram(s, c.strategies[s].structure.histogram, c.range)); });
    clear($('questions-table')).appendChild(questionsTable(c));
  }

  loaders.compare = function () {
    if (state.compare) { renderCompare(state.compare); return; }
    clear($('conclusion')).appendChild(h('li', { class: 'muted', text: 'Считаю метрики (вопросы прогоняются через модель)…' }));
    api('/api/compare').then(function (c) { state.compare = c; renderCompare(c); })
      .catch(function (e) { clear($('conclusion')).appendChild(h('li', { class: 'muted', text: e.message })); });
  };

  // ═════════════ 04 ПОИСК ═════════════
  var activeQuestion = null;

  function renderExamples() {
    var box = clear($('examples'));
    state.questions.forEach(function (q) {
      box.appendChild(h('button', { class: 'chip', type: 'button', title: 'глава ' + q.chapters.join(', '), text: q.q, onclick: function () {
        $('q').value = q.q; activeQuestion = q;
        document.querySelectorAll('.chip').forEach(function (c) { c.classList.toggle('on', c === this); }, this);
        runSearch();
      } }));
    });
  }

  function resultCard(s, r, i) {
    var hit = activeQuestion && r.chapters.some(function (n) { return activeQuestion.chapters.indexOf(n) !== -1; });
    return h('div', { class: 'res' + (hit ? ' hit' : '') },
      h('div', { class: 'res__head' }, h('span', null, '#' + (i + 1) + ' ', h('span', { class: 'muted', text: r.chunk_id })),
        h('span', null, r.similarity.toFixed(3), h('span', { class: 'simbar' }, h('i', { style: 'width:' + Math.max(0, (r.similarity - 0.5) * 200) + '%' })))),
      h('div', { class: 'res__sec', text: (hit ? '✔ ' : '') + r.section }),
      h('div', { class: 'res__text', text: r.text.slice(r.body_start - r.start, r.body_start - r.start + 600) }),
      h('div', { class: 'res__flags' },
        h('span', { class: 'flag', text: r.tokens + ' ток.' }),
        r.chapters.length > 1 ? h('span', { class: 'flag warn', text: 'захватывает ' + r.chapters.length + ' главы' }) : null,
        r.ends_mid_sentence ? h('span', { class: 'flag warn', text: 'обрыв посреди предложения' }) : null,
        hit ? h('span', { class: 'flag ok', text: 'нужная глава' }) : null,
        h('button', { class: 'link', type: 'button', text: 'показать в нарезке →', onclick: function () { openInChunks(r); } })));
  }

  function runSearch() {
    var q = $('q').value.trim();
    if (!q) return;
    if (activeQuestion && activeQuestion.q !== q) { activeQuestion = null; document.querySelectorAll('.chip.on').forEach(function (c) { c.classList.remove('on'); }); }
    var res = clear($('results'));
    res.appendChild(h('div', { class: 'empty', text: 'Ищу…' }));
    api('/api/search', { q: q, k: +$('k').value }).then(function (d) {
      clear(res);
      $('search-meta').textContent = 'эмбеддинг вопроса ' + d.embed_ms + ' мс · всего ' + d.total_ms + ' мс · сходство = (1 + cos) / 2';
      STRATS.forEach(function (s) {
        var list = d.results[s] || [];
        var place = activeQuestion ? list.findIndex(function (r) { return r.chapters.some(function (n) { return activeQuestion.chapters.indexOf(n) !== -1; }); }) : -1;
        res.appendChild(h('div', { class: 'col col--' + s },
          h('div', { class: 'col__head' }, h('b', { text: s }),
            h('span', { class: 'muted', text: activeQuestion ? (place >= 0 ? 'нужная глава на месте #' + (place + 1) : 'нужной главы нет в топ-' + list.length) : list.length + ' результатов' })),
          list.length ? list.map(function (r, i) { return resultCard(s, r, i); }) : h('div', { class: 'empty', text: 'Индекс пуст' })));
      });
    }).catch(function (e) { clear(res).appendChild(h('div', { class: 'empty', text: e.message })); });
  }
  $('search-form').addEventListener('submit', function (e) { e.preventDefault(); runSearch(); });

  loaders.search = function () {
    if (!state.questions.length) api('/api/questions').then(function (d) { state.questions = d.questions; renderExamples(); });
    $('q').focus();
  };

  // ───────────── старт ─────────────
  show((location.hash || '').slice(1) || lsGet('tab') || 'index');
})();
