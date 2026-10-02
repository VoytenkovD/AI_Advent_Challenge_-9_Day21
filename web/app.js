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

  // ═════════════ LLM: выбор модели (общий для вкладок 05 и 06) ═════════════
  var models = { list: [], current: null };

  function fillModelSelect(sel, value) {
    clear(sel);
    models.catalog && Object.keys(models.catalog.providers).forEach(function (p) {
      var info = models.catalog.providers[p];
      var og = h('optgroup', { label: p + (info.ok ? '' : ' — недоступен') });
      info.models.forEach(function (m) { og.appendChild(h('option', { value: p + ':' + m, text: m })); });
      if (!info.models.length) og.appendChild(h('option', { disabled: 'disabled', text: info.ok ? 'нет моделей' : (info.error || '').slice(0, 60) }));
      sel.appendChild(og);
    });
    if (value) sel.value = value;
  }

  function loadModels() {
    if (models.catalog) return Promise.resolve(models.catalog);
    return api('/api/llm/models').then(function (c) {
      models.catalog = c;
      var saved = lsGet('model');
      models.current = saved || c.default;
      fillModelSelect($('model-select'), models.current);
      if ($('model-select').value !== models.current) { models.current = c.default; $('model-select').value = c.default; }
      fillModelSelect($('judge-select'), lsGet('judge') || models.current);
      $('eval-model').textContent = models.current;
      return c;
    });
  }
  $('model-select').addEventListener('change', function () {
    models.current = this.value; lsSet('model', this.value);
    $('eval-model').textContent = this.value;
    if (document.getElementById('view-quality').classList.contains('active')) loadRuns();
  });
  $('judge-select').addEventListener('change', function () { lsSet('judge', this.value); });

  // ═════════════ 05 ВОПРОС · RAG ═════════════
  var ragQuestions = [];
  var askQuestion = null;

  function renderFlow() {
    var box = clear($('rag-flow'));
    [['p', 'без RAG: вопрос → LLM'], ['', '|'],
     ['r', 'RAG: вопрос → bge-m3 → top-5'], ['', '→'], ['r', 'вопрос + отрывки [n] → LLM'], ['', '|'],
     ['g', 'RAG + rerank: rewrite'], ['', '→'], ['g', 'top-K до'], ['', '→'], ['g', 'порог косинуса'], ['', '→'], ['g', 'реранкер + порог'], ['', '→'], ['g', 'top-K после → LLM']
    ].forEach(function (x) { box.appendChild(x[1] === '→' || x[1] === '|' ? h('i', { text: x[1] }) : h('span', { class: x[0], text: x[1] })); });
  }

  function withCites(text, onCite) {
    var box = h('div', { class: 'answer__text' });
    text.split(/(\[\d+(?:\s*,\s*\d+)*\])/).forEach(function (part) {
      var m = part.match(/^\[(\d+(?:\s*,\s*\d+)*)\]$/);
      if (!m) { box.appendChild(document.createTextNode(part)); return; }
      m[1].split(',').forEach(function (n) {
        n = +n.trim();
        box.appendChild(h('span', { class: 'cite', text: n, title: 'отрывок ' + n, onclick: function () { onCite(n); } }));
      });
    });
    return box;
  }

  var KIND = {
    plain: ['Без RAG', 'по памяти модели', 'модель вспоминает'],
    rag: ['RAG', 'вопрос как есть, top-5', 'ищу отрывки и читаю'],
    rag2: ['RAG + rerank', 'rewrite → фильтр → реранкер', 'переписываю вопрос, фильтрую и ранжирую']
  };

  function answerPanel(kind, a) {
    var title = KIND[kind][0];
    var sub = KIND[kind][1];
    var p = h('div', { class: 'panel answer answer--' + kind },
      h('div', { class: 'answer__head' }, h('div', null, h('span', { class: 'answer__name', text: title }), ' ', h('span', { class: 'muted small', text: sub })),
        a && a.model ? h('span', { class: 'muted small mono', text: a.model }) : null));
    if (!a) { p.appendChild(h('div', { class: 'thinking', text: KIND[kind][2] })); return p; }
    if (a.error) { p.appendChild(h('div', { class: 'flag warn', text: a.error })); return p; }
    p.appendChild(kind === 'plain' ? h('div', { class: 'answer__text', text: a.text })
      : withCites(a.text, function (n) { flashSource(n, kind); }));
    var meta = h('div', { class: 'answer__meta' },
      h('span', { class: 'flag', text: (a.latency_ms / 1000).toFixed(1) + ' с' }),
      h('span', { class: 'flag', text: 'промпт ' + a.usage.prompt + ' · ответ ' + a.usage.completion + ' ток.' }));
    if (kind === 'rag2' && a.funnel) {
      var fc = a.funnel.counts;
      meta.appendChild(h('span', { class: 'flag ' + (fc.kept ? '' : 'warn'), text: 'отрывков: ' + fc.before + ' → ' + fc.kept }));
    }
    if (kind !== 'plain') {
      meta.appendChild(h('span', { class: 'flag', text: 'поиск ' + a.search_ms + ' мс' }));
      meta.appendChild(h('span', { class: 'flag' + (a.cited.length ? ' ok' : ' warn'), text: a.cited.length ? 'ссылки: ' + a.cited.join(', ') : 'без ссылок на отрывки' }));
      if (askQuestion && askQuestion.chapters.length) {
        var hit = a.sources.some(function (s) { return s.chapters.some(function (n) { return askQuestion.chapters.indexOf(n) !== -1; }); });
        meta.appendChild(h('span', { class: 'flag ' + (hit ? 'ok' : 'warn'), text: hit ? 'нужная глава в отрывках' : 'нужной главы нет в отрывках' }));
      }
    }
    p.appendChild(meta);
    return p;
  }

  function flashSource(n, kind) {
    var el = kind === 'rag2' ? document.querySelector('#ask-funnel tr[data-final="' + n + '"]')
      : document.querySelector('#ask-sources .src[data-n="' + n + '"]');
    if (!el) return;
    el.classList.add('open', 'flash');
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setTimeout(function () { el.classList.remove('flash'); }, 1600);
  }

  function renderSources(a) {
    var box = $('ask-sources');
    if (!a || !a.sources) { box.hidden = true; return; }
    box.hidden = false;
    clear(box).appendChild(h('div', { class: 'panel__title', text: 'Отрывки, которые получила модель (' + a.sources.length + ', стратегия structure, ' + a.prompt_chars + ' символов контекста)' }));
    a.sources.forEach(function (s) {
      var expectedHit = askQuestion && s.chapters.some(function (n) { return askQuestion.chapters.indexOf(n) !== -1; });
      var row = h('div', { class: 'src', 'data-n': s.n },
        h('div', { class: 'src__n' + (a.cited.indexOf(s.n) !== -1 ? ' cited' : ''), text: '[' + s.n + ']', title: a.cited.indexOf(s.n) !== -1 ? 'модель сослалась на этот отрывок' : '' }),
        h('div', null,
          h('div', { class: 'src__head' }, h('span', null, (expectedHit ? '✔ ' : '') + s.section),
            h('span', { class: 'muted' }, s.chunk_id + ' · сходство ' + s.similarity.toFixed(3) + ' · ' + s.tokens + ' ток. ',
              h('button', { class: 'link', type: 'button', text: 'в нарезке →', onclick: function () { openInChunks(s); } }))),
          h('div', { class: 'src__text', text: s.text, title: 'клик — развернуть', onclick: function () { row.classList.toggle('open'); } })));
      box.appendChild(row);
    });
  }

  // ── настройки воронки улучшенного RAG ──
  var rr = { config: null };
  var TUNE = ['k_before', 'sim_min', 'rel_min', 'k_after'];

  function tuneParams() {
    var p = { query: $('t-query').value, rerank: $('t-rerank').checked };
    TUNE.forEach(function (k) { p[k] = +$('t-' + k).value; });
    return p;
  }

  function tuneSummary() {
    var p = tuneParams();
    TUNE.forEach(function (k) { $('v-' + k).textContent = p[k]; });
    $('t-rel_min').disabled = !p.rerank;
    $('tune-sum').textContent = '— ' + p.query + ' · top-' + p.k_before + ' → cos ≥ ' + p.sim_min +
      (p.rerank ? ' → реранкер ≥ ' + p.rel_min : ' · без реранкера') + ' → top-' + p.k_after;
    lsSet('tune', JSON.stringify(p));
  }

  function setTune(p) {
    $('t-query').value = p.query; $('t-rerank').checked = !!p.rerank;
    TUNE.forEach(function (k) { $('t-' + k).value = p[k]; });
    tuneSummary();
  }

  function loadTune() {
    if (rr.config) return Promise.resolve(rr.config);
    return api('/api/rerank/config').then(function (c) {
      rr.config = c;
      var saved = null;
      try { saved = JSON.parse(lsGet('tune') || 'null'); } catch (e) {}
      setTune(saved || c.improved);
      $('t-note').textContent = c.reranker.pulled ? 'реранкер: ' + c.reranker.model : '⚠ реранкер ' + c.reranker.model + ' не скачан: ollama pull ' + c.reranker.model;
      return c;
    });
  }
  ['t-query', 't-rerank'].concat(TUNE.map(function (k) { return 't-' + k; })).forEach(function (id) {
    $(id).addEventListener('input', tuneSummary);
  });
  $('t-reset').addEventListener('click', function () { if (rr.config) setTune(rr.config.improved); });

  function renderFunnel(a) {
    var box = $('ask-funnel');
    if (!a || !a.funnel) { box.hidden = true; return; }
    var f = a.funnel, c = f.counts, p = f.params;
    box.hidden = false;
    clear(box).appendChild(h('div', { class: 'panel__title', text: 'Как искал улучшенный RAG' }));
    if (p.query !== 'raw') box.appendChild(h('div', null, h('span', { class: 'muted small mono', text: 'запрос для поиска (' + p.query + ', ' + f.ms.rewrite + ' мс):' }), h('div', { class: 'fq', text: f.query })));
    box.appendChild(h('div', { class: 'fcounts' },
      h('span', null, 'кандидатов ', h('b', { text: c.before })), h('i', { text: '→' }),
      h('span', null, 'cos ≥ ' + p.sim_min + ': ', h('b', { text: c.passed_sim })), h('i', { text: '→' }),
      p.rerank ? [h('span', null, 'реранкер ≥ ' + p.rel_min + ': ', h('b', { text: c.passed_rel })), h('i', { text: '→' })] : null,
      h('span', null, 'в модель ', h('b', { text: c.kept })),
      h('span', { class: 'muted', text: 'поиск ' + f.ms.search + ' мс · реранкер ' + f.ms.rerank + ' мс' + (f.rerank_cached ? ' (из кэша ' + f.rerank_cached + ')' : '') })));
    var tbl = h('table', { class: 't cand' }, h('tr', null, h('th', { text: '#' }), h('th', { text: 'отрывок' }), h('th', { text: 'косинус' }),
      h('th', { text: 'реранкер' }), h('th', { text: 'итог' })));
    f.candidates.forEach(function (x) {
      var hit = askQuestion && x.chapters.some(function (n) { return askQuestion.chapters.indexOf(n) !== -1; });
      tbl.appendChild(h('tr', { class: x.stage, 'data-final': x.final || '' },
        h('td', { class: 'num', text: x.rank }),
        h('td', null, (hit ? '✔ ' : '') + x.section, h('div', { class: 'muted small', text: x.chunk_id })),
        h('td', { class: 'num', text: x.cos.toFixed(3) }),
        h('td', { class: 'num' }, x.rel == null ? '—' : [h('span', { class: 'relbar' }, h('i', { style: 'width:' + Math.round(x.rel * 100) + '%' })), x.rel.toFixed(2)]),
        h('td', null, h('span', { class: 'stage ' + x.stage, text: x.stage === 'kept' ? '[' + x.final + '] в модель' : x.stage === 'top' ? 'не вошёл в top' : x.stage === 'sim' ? 'косинус' : 'реранкер' }),
          ' ', h('span', { class: 'muted small', text: x.reason }))));
    });
    box.appendChild(tbl);
  }

  function renderExpected() {
    var box = clear($('ask-expected'));
    if (!askQuestion) return;
    box.appendChild(h('div', { class: 'expected' }, h('b', { text: 'ожидание' }), askQuestion.expected,
      h('div', { class: 'muted small', style: 'margin-top:4px', text: 'источник: ' + (askQuestion.chapters.length ? askQuestion.sources.join(', ') : 'нет — правильный ответ «в книге этого нет»') })));
  }

  function runAsk() {
    var q = $('ask-q').value.trim();
    if (!q) return;
    if (askQuestion && askQuestion.q !== q) askQuestion = null;
    document.querySelectorAll('#ask-examples .chip').forEach(function (c) { c.classList.toggle('on', !!askQuestion && c.textContent === askQuestion.q); });
    renderExpected();
    var ans = clear($('answers'));
    ['plain', 'rag', 'rag2'].forEach(function (k) { ans.appendChild(answerPanel(k, null)); });
    $('ask-sources').hidden = true;
    $('ask-funnel').hidden = true;
    $('ask-btn').disabled = true;
    api('/api/ask', { q: q, model: models.current, k: +$('ask-k').value, params: tuneParams() }).then(function (r) {
      clear(ans);
      ['plain', 'rag', 'rag2'].forEach(function (k) { ans.appendChild(answerPanel(k, r[k])); });
      renderFunnel(r.rag2);
      renderSources(r.rag);
    }).catch(function (e) {
      clear(ans).appendChild(h('div', { class: 'empty', text: e.message }));
    }).finally(function () { $('ask-btn').disabled = false; });
  }
  $('ask-form').addEventListener('submit', function (e) { e.preventDefault(); runAsk(); });

  function loadRagQuestions() {
    if (ragQuestions.length) return Promise.resolve(ragQuestions);
    return api('/api/rag/questions').then(function (d) { ragQuestions = d.questions; return ragQuestions; });
  }

  loaders.ask = function () {
    renderFlow();
    Promise.all([loadModels(), loadRagQuestions(), loadTune()]).then(function () {
      var box = clear($('ask-examples'));
      ragQuestions.forEach(function (q) {
        box.appendChild(h('button', { class: 'chip', type: 'button', text: q.q, onclick: function () { askQuestion = q; $('ask-q').value = q.q; runAsk(); } }));
      });
    });
    $('ask-q').focus();
  };

  // ═════════════ 06 КАЧЕСТВО ═════════════
  var evalAfter = 0, evalPoll = null;

  function yesNo(v) { return v ? h('span', { class: 'verdict v-верно', text: 'да' }) : h('span', { class: 'verdict v-неверно', text: 'нет' }); }

  function verdictChip(v) { return h('span', { class: 'verdict v-' + (v || 'none'), text: v || '—' }); }

  function stack(t, n) {
    var box = h('div', { class: 'stack' });
    [['верно', 's1'], ['частично', 's2'], ['неверно', 's3']].forEach(function (x) {
      if (t[x[0]]) box.appendChild(h('i', { class: x[1], style: 'width:' + (t[x[0]] / n * 100) + '%', title: x[0] + ': ' + t[x[0]] }));
    });
    return box;
  }

  function renderTotals(res) {
    var box = clear($('q-totals'));
    $('q-model').textContent = res ? res.model + ' · судья ' + res.judge + ' · ' + new Date(res.finished * 1000).toLocaleString('ru-RU') : '';
    if (!res) { box.appendChild(h('div', { class: 'empty', text: 'Для этой модели прогона ещё нет — нажмите «Запустить прогон».' })); return; }
    var t = res.totals, n = t.questions;
    box.appendChild(h('div', { class: 'score' },
      h('div', { class: 'score__card p' }, h('div', { class: 'stat__k', text: 'без RAG — верно' }), h('div', { class: 'score__big', text: t.plain['верно'] + ' / ' + n }),
        stack(t.plain, n), h('div', { class: 'muted small', text: 'частично ' + t.plain['частично'] + ' · неверно ' + t.plain['неверно'] })),
      h('div', { class: 'score__card r' }, h('div', { class: 'stat__k', text: 'RAG — верно' }), h('div', { class: 'score__big', text: t.rag['верно'] + ' / ' + n }),
        stack(t.rag, n), h('div', { class: 'muted small', text: 'частично ' + t.rag['частично'] + ' · неверно ' + t.rag['неверно'] })),
      t.rag2 ? h('div', { class: 'score__card g' }, h('div', { class: 'stat__k', text: 'RAG + rerank — верно' }), h('div', { class: 'score__big', text: t.rag2['верно'] + ' / ' + n }),
        stack(t.rag2, n), h('div', { class: 'muted small', text: 'частично ' + t.rag2['частично'] + ' · неверно ' + t.rag2['неверно'] })) : null,
      h('div', { class: 'score__card s' }, h('div', { class: 'stat__k', text: 'нужная глава в отрывках' }),
        h('div', { class: 'score__big', text: t.chapter_hit + (t.chapter_hit2 != null ? ' → ' + t.chapter_hit2 : '') + ' / ' + t.with_source }),
        h('div', { class: 'muted small', text: 'RAG → RAG + rerank; проверка кодом, без судьи; ловушка не считается' }))));
    if (res.params2) {
      var p = res.params2;
      box.appendChild(h('div', { class: 'muted small mono', style: 'margin-top:8px', text: 'воронка RAG + rerank: ' + p.query + ' · top-' + p.k_before +
        ' → cos ≥ ' + p.sim_min + (p.rerank ? ' → реранкер ≥ ' + p.rel_min : '') + ' → top-' + p.k_after }));
    }
  }

  function renderQTable(res) {
    var box = clear($('q-table'));
    var tbl = h('table', { class: 't qa' }, h('tr', null, h('th', { text: 'вопрос' }), h('th', { text: 'ожидание · источник' }),
      h('th', { style: 'color:var(--plain)', text: 'без RAG' }), h('th', { style: 'color:var(--structure)', text: 'RAG' }),
      h('th', { style: 'color:var(--rr)', text: 'RAG + rerank' }), h('th', { text: 'глава в отрывках' })));
    ragQuestions.forEach(function (q) {
      var it = res && res.items[q.id];
      function cell(mode) {
        var a = it && it[mode];
        if (!a) return h('td', { class: 'ans muted', text: '—' });
        return h('td', { class: 'ans' }, verdictChip(a.verdict), ' ', a.error ? h('span', { class: 'flag warn', text: a.error }) : a.text,
          a.reason ? h('div', { class: 'reason', text: 'судья: ' + a.reason }) : null);
      }
      tbl.appendChild(h('tr', null,
        h('td', null, q.q, q.verified ? null : h('div', { class: 'flag warn', text: 'не подтверждён текстом' })),
        h('td', { class: 'exp' }, q.expected, h('div', { class: 'muted small', style: 'margin-top:4px', text: q.chapters.length ? q.sources.join(', ') : 'источника нет (ловушка)' })),
        cell('plain'), cell('rag'), cell('rag2'),
        h('td', { class: 'num' }, !it || it.chapter_hit == null ? '—' : [yesNo(it.chapter_hit), it.chapter_hit2 != null ? [' → ', yesNo(it.chapter_hit2)] : null,
          it.rag2 && it.rag2.counts ? h('div', { class: 'muted small', text: 'отрывков ' + it.rag2.counts.kept }) : null],
          it && it.chapter_hit == null && it.rag2 && it.rag2.counts ? h('div', { class: 'muted small', text: 'отрывков ' + it.rag2.counts.kept }) : null)));
    });
    box.appendChild(tbl);
  }

  function showResult(model) {
    return api('/api/rag/results?model=' + encodeURIComponent(model)).then(function (d) {
      renderTotals(d.result); renderQTable(d.result);
      document.querySelectorAll('.run').forEach(function (r) { r.classList.toggle('on', r.dataset.model === model); });
    });
  }

  function loadRuns() {
    return api('/api/rag/results').then(function (d) {
      var box = clear($('runs'));
      if (!d.runs.length) box.appendChild(h('div', { class: 'muted small', text: 'Прогонов пока нет.' }));
      d.runs.forEach(function (r) {
        var t = r.totals;
        box.appendChild(h('div', { class: 'run', 'data-model': r.model, onclick: function () { showResult(r.model); } },
          h('div', null, r.model, h('br'), h('small', { text: 'судья ' + r.judge })),
          h('div', { style: 'text-align:right' }, h('span', { style: 'color:var(--plain)', text: t.plain['верно'] }), ' → ',
            h('span', { style: 'color:var(--structure)', text: t.rag['верно'] }),
            t.rag2 ? [' → ', h('span', { style: 'color:var(--rr)', text: t.rag2['верно'] })] : null, h('small', { text: ' из ' + t.questions }))));
      });
      return showResult(models.current);
    });
  }

  function pollEval() {
    api('/api/rag/eval/status?after=' + evalAfter).then(function (s) {
      var con = $('eval-console');
      if (evalAfter === 0 && s.log.length) clear(con);
      s.log.forEach(function (e) { con.appendChild(logLine(e)); });
      evalAfter += s.log.length;
      con.scrollTop = con.scrollHeight;
      if (s.progress) $('eval-bar').style.width = Math.round(s.progress.done / s.progress.total * 100) + '%';
      if (s.state === 'running') { showResult(models.current); return; }
      clearInterval(evalPoll); evalPoll = null;
      $('btn-eval').disabled = false;
      if (s.state === 'done') $('eval-bar').style.width = '100%';
      loadRuns();
    }).catch(function () {});
  }

  $('btn-eval').addEventListener('click', function () {
    evalAfter = 0;
    clear($('eval-console'));
    $('eval-bar').style.width = '2%';
    api('/api/rag/eval', { model: models.current, judge: $('judge-select').value, force: $('eval-force').checked }).then(function (r) {
      if (!r.started) { alert('Прогон уже идёт'); }
      $('btn-eval').disabled = true;
      if (!evalPoll) evalPoll = setInterval(pollEval, 1500);
    }).catch(function (e) { alert(e.message); });
  });

  loaders.quality = function () {
    Promise.all([loadModels(), loadRagQuestions()]).then(function () {
      $('eval-model').textContent = models.current;
      loadRuns();
      api('/api/rag/eval/status?after=0').then(function (s) { if (s.state === 'running' && !evalPoll) { $('btn-eval').disabled = true; evalPoll = setInterval(pollEval, 1500); } });
    });
  };

  // ═════════════ 07 РЕРАНКИНГ ═════════════
  var rrReport = null, rrAfter = 0, rrPoll = null;
  var QM = { raw: 'вопрос как есть', en: 'перевод (en)', hyde: 'HyDE' };

  // та же воронка, что rerank.funnel на сервере, — по сохранённым кандидатам
  function rrApply(cands, cfg) {
    var pool = cands.slice(0, cfg.k_before).filter(function (c) { return c.cos >= cfg.sim_min; });
    if (cfg.rerank) {
      pool = pool.filter(function (c) { return c.rel >= cfg.rel_min; });
      pool = pool.slice().sort(function (a, b) { return b.rel - a.rel; });
    }
    return pool.slice(0, cfg.k_after);
  }

  function rrMetrics(data, cfg) {
    var ans = data.filter(function (d) { return d.chapters.length; }), no = data.filter(function (d) { return !d.chapters.length; });
    var m = { hit1: 0, hitk: 0, prec: 0, kept: 0, empty: 0 };
    ans.forEach(function (d) {
      var kept = rrApply(d.modes[cfg.query], cfg);
      var ok = function (c) { return c.chapters.some(function (n) { return d.chapters.indexOf(n) !== -1; }); };
      var good = kept.filter(ok);
      m.hit1 += kept.length && ok(kept[0]) ? 1 : 0;
      m.hitk += good.length ? 1 : 0;
      m.prec += kept.length ? good.length / kept.length : 0;
      m.kept += kept.length;
      m.empty += kept.length ? 0 : 1;
    });
    Object.keys(m).forEach(function (k) { m[k] = m[k] / (ans.length || 1); });
    var nk = no.map(function (d) { return rrApply(d.modes[cfg.query], cfg).length; });
    m.no_empty = nk.filter(function (x) { return !x; }).length / (nk.length || 1);
    m.no_kept = nk.reduce(function (a, b) { return a + b; }, 0) / (nk.length || 1);
    return m;
  }

  var RR_COLS = [['hit1', 'hit@1', pct, 1], ['hitk', 'hit@K', pct, 1], ['prec', 'точность контекста', pct, 1],
    ['kept', 'отрывков в модель', function (x) { return x.toFixed(1); }, 0], ['empty', 'пустой контекст (с ответом)', pct, -1],
    ['no_empty', 'пустой контекст (без ответа)', pct, 1], ['no_kept', 'отрывков (без ответа)', function (x) { return x.toFixed(1); }, -1]];

  function rrTable(rows, bestId) {
    var tbl = h('table', { class: 't' }, h('tr', null, h('th', { text: 'режим' }), RR_COLS.map(function (c) { return h('th', { style: 'text-align:right', text: c[1] }); })));
    var best = {};
    RR_COLS.forEach(function (c) {
      if (!c[3]) return;
      var vals = rows.map(function (r) { return r[c[0]]; });
      best[c[0]] = c[3] > 0 ? Math.max.apply(null, vals) : Math.min.apply(null, vals);
    });
    rows.forEach(function (r) {
      tbl.appendChild(h('tr', { class: r.id === bestId ? 'best' : '' }, h('td', null, r.title, r.cfg ? h('div', { class: 'muted small mono', text: cfgText(r.cfg) }) : null),
        RR_COLS.map(function (c) { return h('td', { class: 'num' + (c[3] && r[c[0]] === best[c[0]] ? ' win' : ''), text: c[2](r[c[0]]) }); })));
    });
    return tbl;
  }

  function cfgText(c) {
    return c.query + ' · top-' + c.k_before + (c.sim_min > -1 ? ' → cos ≥ ' + c.sim_min : '') + (c.rerank ? ' → rel ≥ ' + c.rel_min : '') + ' → top-' + c.k_after;
  }

  function rrSweepChart(query) {
    var data = rrReport.questions, W = 560, H = 230, pad = 34, els = [];
    var pts = [];
    for (var i = 0; i < 20; i++) {
      var t = Math.round(i * 5) / 100;
      pts.push({ t: t, m: rrMetrics(data, { query: query, rerank: true, k_before: 20, sim_min: -1, rel_min: t, k_after: 5 }) });
    }
    var x = function (t) { return pad + t / 0.95 * (W - pad * 2); }, y = function (v) { return H - pad - v * (H - pad - 14); };
    [0, .25, .5, .75, 1].forEach(function (v) {
      els.push(sv('line', { x1: pad, x2: W - pad, y1: y(v), y2: y(v), stroke: cssVar('--line'), 'stroke-dasharray': v ? '2 4' : '' }));
      els.push(sv('text', { x: pad - 6, y: y(v) + 3, 'text-anchor': 'end' }, Math.round(v * 100)));
    });
    [0, .2, .4, .6, .8].forEach(function (t) { els.push(sv('text', { x: x(t), y: H - 12, 'text-anchor': 'middle' }, t.toFixed(1))); });
    els.push(sv('text', { x: W - pad, y: H - 12, 'text-anchor': 'end' }, 'порог реранкера →'));
    var lines = [['hitk', cssVar('--structure'), 'hit@K'], ['prec', cssVar('--rr'), 'точность'], ['no_empty', cssVar('--plain'), 'пусто на «нет ответа»'], ['empty', cssVar('--err'), 'пусто на вопросах с ответом']];
    lines.forEach(function (l, li) {
      els.push(sv('polyline', { points: pts.map(function (p) { return x(p.t) + ',' + y(p.m[l[0]]); }).join(' '), fill: 'none', stroke: l[1], 'stroke-width': 2 }));
      pts.forEach(function (p) {
        var c = sv('circle', { cx: x(p.t), cy: y(p.m[l[0]]), r: 2.5, fill: l[1] });
        c.appendChild(sv('title', {}, l[2] + ' при пороге ' + p.t + ': ' + Math.round(p.m[l[0]] * 100) + '%'));
        els.push(c);
      });
      els.push(sv('rect', { x: pad + li * 128, y: 0, width: 10, height: 4, fill: l[1] }));
      els.push(sv('text', { x: pad + li * 128 + 14, y: 6 }, l[2]));
    });
    var cur = +$('rl-rel_min').value;
    els.push(sv('line', { x1: x(cur), x2: x(cur), y1: 12, y2: H - pad, stroke: cssVar('--text'), 'stroke-dasharray': '3 3' }));
    var rec = rrReport.recommended;
    if (rec.query === query) els.push(sv('line', { x1: x(rec.rel_min), x2: x(rec.rel_min), y1: 12, y2: H - pad, stroke: cssVar('--rr'), 'stroke-width': 1, opacity: .6 }));
    return h('div', null, svg(W, H, els), h('div', { class: 'muted small', text: 'Режим запроса: ' + QM[query] + ', top-20 → реранкер → top-5. Пунктир — текущий порог, зелёная линия — рекомендованный.' }));
  }

  function rrLiveCfg() {
    var c = { query: $('rl-query').value, rerank: $('rl-rerank').checked, k_before: +$('rl-k_before').value,
      sim_min: +$('rl-sim_min').value, rel_min: +$('rl-rel_min').value, k_after: +$('rl-k_after').value };
    ['k_before', 'sim_min', 'rel_min', 'k_after'].forEach(function (k) { $('rv-' + k).textContent = c[k]; });
    return c;
  }

  function rrRenderLive() {
    var cfg = rrLiveCfg();
    var m = rrMetrics(rrReport.questions, cfg);
    var base = rrReport.summary.filter(function (r) { return r.id === 'base'; })[0];
    clear($('rr-live-out')).appendChild(rrTable([
      Object.assign({ id: 'base', title: 'Базовый (День 22)', cfg: base.cfg }, base),
      Object.assign({ id: 'live', title: 'Ваши настройки', cfg: cfg }, m)], 'live'));
    clear($('rr-sweep')).appendChild(rrSweepChart(cfg.rerank ? cfg.query : rrReport.recommended.query));
    rrNoAnswer(cfg);
  }

  function rrNoAnswer(cfg) {
    var box = clear($('rr-noans'));
    var tbl = h('table', { class: 't' }, h('tr', null, h('th', { text: 'вопрос' }), h('th', { style: 'text-align:right', text: 'базовый: отрывков' }),
      h('th', { style: 'text-align:right', text: 'ваши настройки: отрывков' }), h('th', { text: 'что всё же прошло (оценка реранкера)' })));
    rrReport.questions.filter(function (d) { return !d.chapters.length; }).forEach(function (d) {
      var kept = rrApply(d.modes[cfg.query], cfg);
      tbl.appendChild(h('tr', null, h('td', { text: d.q }), h('td', { class: 'num', text: 5 }),
        h('td', { class: 'num' }, kept.length ? String(kept.length) : h('span', { class: 'verdict v-верно', text: '0 — честный отказ' })),
        h('td', { class: 'muted small', text: kept.map(function (c) { return c.section + ' (' + c.rel.toFixed(2) + ')'; }).join('; ') })));
    });
    box.appendChild(tbl);
  }

  function rrBuildLive() {
    var rec = rrReport.recommended;
    var box = clear($('rr-live'));
    var sel = h('select', { id: 'rl-query' }, ['raw', 'en', 'hyde'].map(function (q) { return h('option', { value: q, text: QM[q] }); }));
    box.appendChild(h('div', { class: 'live' },
      h('label', null, 'запрос', sel),
      h('label', null, h('span', null, 'top-K до ', h('b', { id: 'rv-k_before' })), h('input', { type: 'range', id: 'rl-k_before', min: 5, max: 20, step: 1, value: 20 })),
      h('label', null, h('span', null, 'порог косинуса ', h('b', { id: 'rv-sim_min' })), h('input', { type: 'range', id: 'rl-sim_min', min: 0, max: 0.6, step: 0.01, value: rec.sim_min > 0 ? rec.sim_min : 0 })),
      h('label', { class: 'check' }, h('input', { type: 'checkbox', id: 'rl-rerank', checked: 'checked' }), 'реранкер'),
      h('label', null, h('span', null, 'порог реранкера ', h('b', { id: 'rv-rel_min' })), h('input', { type: 'range', id: 'rl-rel_min', min: 0, max: 0.95, step: 0.05, value: rec.rel_min })),
      h('label', null, h('span', null, 'top-K после ', h('b', { id: 'rv-k_after' })), h('input', { type: 'range', id: 'rl-k_after', min: 1, max: 10, step: 1, value: 5 }))));
    box.appendChild(h('div', { id: 'rr-live-out' }));
    box.appendChild(h('div', { class: 'controls', style: 'margin-top:10px' }, h('button', { class: 'btn', type: 'button', text: 'использовать в «Вопрос · RAG» →', onclick: function () {
      var c = rrLiveCfg(); loadTune().then(function () { setTune(c); show('ask'); });
    } })));
    sel.value = rec.query;
    box.querySelectorAll('input, select').forEach(function (e) { e.addEventListener('input', rrRenderLive); });
    rrRenderLive();
  }

  function rrRender() {
    var r = rrReport;
    if (!r) { clear($('rr-table')).appendChild(h('div', { class: 'empty', text: 'Эксперимента ещё нет — нажмите «Запустить эксперимент» (≈10 минут на GTX 1050 Ti).' })); return; }
    $('rr-meta').textContent = new Date(r.finished * 1000).toLocaleString('ru-RU') + ' · ' + r.seconds + ' с · реранкер ' + r.rerank_model;
    clear($('rr-rec')).appendChild(kv([['запрос', QM[r.recommended.query]], ['порог косинуса', r.recommended.sim_min > -1 ? r.recommended.sim_min : 'нет'], ['порог реранкера', r.recommended.rel_min],
      ['top-K до → после', r.k_pool + ' → 5'], ['вопросов', r.questions.length + ' (без ответа: ' + r.questions.filter(function (d) { return !d.chapters.length; }).length + ')'],
      ['rewrite', r.rewrite_model]]));
    $('rr-thr').textContent = 'пороги в режимах с фильтром: косинус ≥ ' + r.recommended.sim_min + ', реранкер ≥ ' + r.recommended.rel_min;
    var bestId = r.recommended.query === 'raw' ? 'filter' : r.recommended.query + '-filter';
    clear($('rr-table')).appendChild(rrTable(r.summary, bestId));
    rrBuildLive();
  }

  function rrLoad() {
    return api('/api/experiment/report').then(function (d) { rrReport = d.report; rrRender(); });
  }

  function rrPollFn() {
    api('/api/experiment/status?after=' + rrAfter).then(function (s) {
      var con = $('rr-console');
      if (rrAfter === 0 && s.log.length) clear(con);
      s.log.forEach(function (e) { con.appendChild(logLine(e)); });
      rrAfter += s.log.length;
      con.scrollTop = con.scrollHeight;
      if (s.progress) $('rr-bar').style.width = Math.round(s.progress.done / s.progress.total * 100) + '%';
      if (s.state === 'running') return;
      clearInterval(rrPoll); rrPoll = null;
      $('btn-rr').disabled = false;
      if (s.state === 'done') { $('rr-bar').style.width = '100%'; rrLoad(); }
    }).catch(function () {});
  }

  $('btn-rr').addEventListener('click', function () {
    rrAfter = 0; clear($('rr-console'));
    api('/api/experiment', { model: models.current }).then(function (r) {
      $('btn-rr').disabled = true;
      if (!rrPoll) rrPoll = setInterval(rrPollFn, 1500);
    }).catch(function (e) { alert(e.message); });
  });

  loaders.rerank = function () {
    var box = clear($('rr-flow'));
    [['r', 'вопрос'], ['', '→'], ['g', 'rewrite: raw / en / HyDE'], ['', '→'], ['r', 'bge-m3: top-20'], ['', '→'], ['g', 'фильтр: косинус ≥ порог'], ['', '→'],
     ['g', 'Qwen3-Reranker: P(yes)'], ['', '→'], ['g', 'фильтр: оценка ≥ порог'], ['', '→'], ['r', 'top-5 → LLM']
    ].forEach(function (x) { box.appendChild(x[1] === '→' ? h('i', { text: '→' }) : h('span', { class: x[0], text: x[1] })); });
    loadModels().then(function () { $('rr-model').textContent = models.current; });
    rrLoad();
    api('/api/experiment/status?after=0').then(function (s) { if (s.state === 'running' && !rrPoll) { $('btn-rr').disabled = true; rrPoll = setInterval(rrPollFn, 1500); } });
  };

  loadModels().catch(function () {});

  // ───────────── старт ─────────────
  show((location.hash || '').slice(1) || lsGet('tab') || 'index');
})();
