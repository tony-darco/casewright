/* casewright product app — client interactions.
   Ported from docs/mockups/three-page-site.html, with the fake client-side
   generate() replaced by a real HTMX post to /app/generate that swaps in the
   server-rendered workspace panel. Pure-client concerns (composer, tabs, mentions,
   device context, export) stay here; generation + Meraki data come from the backend.

   Verified Meraki networks/devices are read from localStorage (written by the
   Settings page). This is a deliberate stub — real persistence is a backend
   contract, TBD (handoff G10/G11). */
(function () {
  'use strict';
  var app = document.getElementById('appRoot');
  if (!app) return;

  function esc(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function trunc(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }
  function textOf(el) { return el.textContent.replace(/ /g, ' ').trim(); }
  function clearComposer(el) { el.innerHTML = ''; }

  var emptyView = document.getElementById('emptyView');
  var heroInput = document.getElementById('heroInput'), heroSend = document.getElementById('heroSend');
  var dockInput = document.getElementById('dockInput'), dockSend = document.getElementById('dockSend');
  var workspace = document.getElementById('workspace');
  var topTitle = document.getElementById('topTitle');

  /* ---------------- verified networks (from the server-side store) ---------------- */
  var NETWORKS = [];                   // [{id, name, orgName, devices:[{name,serial,mac,model,clientId,networkId}]}]
  var activeNetworkId = null;
  function loadNetworks() {
    fetch('/api/networks').then(function (r) { return r.json(); }).then(function (d) {
      NETWORKS = Array.isArray(d) ? d : []; refreshCtx();
    }).catch(function () { /* leave empty; picker shows the "add in Settings" state */ });
  }

  function allNetworks() { return NETWORKS; }
  function activeNetwork() {
    for (var i = 0; i < NETWORKS.length; i++) if (NETWORKS[i].id === activeNetworkId) return NETWORKS[i];
    return null;
  }
  function currentDevices() { var n = activeNetwork(); return n ? (n.devices || []) : []; }

  /* ---------------- generate (real: POST /app/generate) ---------------- */
  function collectDevices(input) {
    // The @-mention chips carry the full identifier set; serialize them for the backend.
    return Array.prototype.map.call(input.querySelectorAll('.mention-chip'), function (chip) {
      return {
        name: chip.textContent.replace(/^@/, ''),
        serial: chip.dataset.serial || '',
        mac: chip.dataset.mac || '',
        model: chip.dataset.model || '',
        clientId: chip.dataset.clientId || '',
        networkId: chip.dataset.networkId || (activeNetwork() ? activeNetwork().id : '')
      };
    });
  }
  var GENNING = '<div class="panel"><div class="panel-body"><div class="genning"><span class="dot"></span> Generating tests…</div></div></div>';

  function setActive(item) {
    document.querySelectorAll('.ritem').forEach(function (r) { r.classList.remove('active'); });
    if (item) item.classList.add('active');
  }

  var STREAM_SHELL = '<div class="panel"><div class="panel-body"><div class="stream-wrap">'
    + '<div class="stream-stage"><span class="dot"></span> <span class="stxt">Starting…</span></div>'
    + '<pre class="code stream-code" id="streamCode"></pre></div></div></div>';

  function streamError() {
    workspace.innerHTML = '<div class="panel"><div class="panel-body"><div class="gen-error">'
      + '<b>&#10007; Generation interrupted.</b>'
      + '<div class="muted">The stream was lost — check that the model backend (Ollama) is running and reachable, then try again.</div>'
      + '</div></div></div>';
  }

  function handleStreamEvent(ev, stageEl, codeEl) {
    if (ev.type === 'stage') { if (stageEl) stageEl.textContent = ev.label; }
    else if (ev.type === 'token') { if (codeEl) { codeEl.textContent += ev.text; codeEl.scrollTop = codeEl.scrollHeight; } }
    else if (ev.type === 'error') { if (stageEl) stageEl.textContent = ev.message; }  // final 'done' renders the error panel
    else if (ev.type === 'done') {
      workspace.innerHTML = ev.panel_html;   // final panel, OR the error/empty state
      if (ev.item_html) {
        var list = document.getElementById('testList');
        if (list) {
          list.insertAdjacentHTML('afterbegin', ev.item_html);
          var first = list.firstElementChild;
          if (window.htmx && first) htmx.process(first);   // wire the item's rename form
          setActive(first);
        }
      }
    }
  }

  // Live generation: stream stage-progress + tokens over SSE (fetch stream).
  // `language` is the per-test choice; it falls back to the Settings default.
  function generate(text, devices, language) {
    app.dataset.view = 'work';
    topTitle.innerHTML = '<b>' + esc(trunc(text, 60)) + '</b>';
    var le = document.getElementById('libEmpty'); if (le) le.hidden = true;
    workspace.innerHTML = STREAM_SHELL;
    var stageEl = workspace.querySelector('.stream-stage .stxt');
    var codeEl = workspace.querySelector('#streamCode');
    var body = new URLSearchParams({
      prompt: text, devices: JSON.stringify(devices || []),
      language: language || localStorage.getItem('cw.language') || 'py'
    });
    fetch('/app/generate/stream', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body.toString()
    }).then(function (resp) {
      var ct = resp.headers.get('content-type') || '';
      if (!resp.ok || ct.indexOf('text/event-stream') === -1) { window.location = '/login'; return; }
      var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var parts = buf.split('\n\n'); buf = parts.pop();
          for (var i = 0; i < parts.length; i++) {
            var line = parts[i].replace(/^data: ?/, '');
            if (!line) continue;
            var ev; try { ev = JSON.parse(line); } catch (e) { continue; }
            handleStreamEvent(ev, stageEl, codeEl);
          }
          return pump();
        });
      }
      return pump();
    }).catch(streamError);
  }

  function langOf(id) { var s = document.getElementById(id); return s ? s.value : null; }
  function fromHero(text, devices) {
    var lang = langOf('heroLang');
    emptyView.classList.add('leaving');
    setTimeout(function () {
      generate(text, devices, lang); emptyView.classList.remove('leaving');
      clearComposer(heroInput); heroSend.disabled = true; dockInput.focus();
    }, 200);
  }
  function fromDock(text, devices) { generate(text, devices, langOf('dockLang')); clearComposer(dockInput); dockSend.disabled = true; }

  // Load a saved test back into the workspace (GET, not a re-generate).
  function loadTest(item) {
    if (!item) return;
    var tt = item.querySelector('.tt');
    setActive(item);
    app.dataset.view = 'work'; app.dataset.nav = 'closed';
    topTitle.innerHTML = '<b>' + esc(trunc((tt ? tt.textContent : 'Test').trim(), 60)) + '</b>';
    workspace.innerHTML = GENNING;
    htmx.ajax('GET', '/app/tests/' + item.dataset.testId, { target: '#workspace', swap: 'innerHTML' });
  }

  /* ---------------- workspace: tabs / export (delegated; survives swaps) ---------------- */
  function switchTab(name) {
    var panel = workspace.querySelector('.panel'); if (!panel) return;
    panel.querySelectorAll('.tab').forEach(function (t) { t.classList.toggle('active', t.dataset.tab === name); });
    var prompt = panel.querySelector('#panePrompt'), code = panel.querySelector('#paneCode'), out = panel.querySelector('#paneOutput');
    if (prompt) prompt.classList.toggle('show', name === 'prompt');
    if (code) code.classList.toggle('show', name === 'code');
    if (out) out.classList.toggle('show', name === 'output');
  }
  // Regenerate from the edited prompt (Prompt tab): reuses the same @device grounding.
  function regenerate() {
    var panel = workspace.querySelector('.panel'); if (!panel) return;
    var ta = panel.querySelector('#promptEdit'); if (!ta) return;
    var text = ta.value.trim(); if (!text) { ta.focus(); return; }
    var devices = []; try { devices = JSON.parse(panel.dataset.devices || '[]'); } catch (e) { devices = []; }
    var langSel = panel.querySelector('#regenLang');
    generate(text, devices, langSel ? langSel.value : null);
  }
  function downloadFile() {
    var codeEl = workspace.querySelector('#codeEl'); if (!codeEl) return;
    var panel = workspace.querySelector('.panel');
    var name = (panel && panel.dataset.file) || 'generated.test.py';
    var blob = new Blob([codeEl.textContent], { type: 'text/plain' });
    var url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    a.remove(); URL.revokeObjectURL(url);
  }
  function copyCode(flashBtn) {
    var codeEl = workspace.querySelector('#codeEl'); if (!codeEl) return;
    if (navigator.clipboard) navigator.clipboard.writeText(codeEl.textContent);
    if (flashBtn) { var o = flashBtn.innerHTML; flashBtn.innerHTML = '&#10003;'; setTimeout(function () { flashBtn.innerHTML = o; }, 1200); }
  }
  /* ---------------- editable code (Code tab): gutter sync + autosave ---------------- */
  function syncGutter(codeEl) {
    var panel = codeEl.closest('.panel'); var gutter = panel && panel.querySelector('.gutter');
    if (!gutter) return;
    var n = codeEl.textContent.split('\n').length, lines = [];
    for (var i = 1; i <= n; i++) lines.push(i);
    gutter.textContent = lines.join('\n');
  }
  function flashStatus(panel, msg, sticky) {
    var s = panel.querySelector('#codeStatus'); if (!s) return;
    s.textContent = msg;
    if (!sticky) setTimeout(function () { if (s.textContent === msg) s.textContent = ''; }, 1500);
  }
  function saveCode(codeEl) {
    var panel = codeEl.closest('.panel'); if (!panel) return;
    var id = panel.dataset.testId; if (!id) return;              // error/empty/unsaved: nothing to save to
    var code = codeEl.textContent;
    if (code === codeEl.dataset.base) return;                    // unchanged since focus
    flashStatus(panel, 'Saving…', true);
    fetch('/app/tests/' + id + '/code', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ code: code }).toString()
    }).then(function (r) {
      if (r.ok) { codeEl.dataset.base = code; flashStatus(panel, 'Saved'); }
      else flashStatus(panel, 'Save failed', true);
    }).catch(function () { flashStatus(panel, 'Save failed', true); });
  }
  // Delegated (survives panel swaps): track a baseline on focus, sync line numbers on
  // input, and autosave on blur when the code changed.
  workspace.addEventListener('focusin', function (e) {
    var codeEl = e.target.closest('#codeEl'); if (codeEl) codeEl.dataset.base = codeEl.textContent;
  });
  workspace.addEventListener('input', function (e) {
    var codeEl = e.target.closest('#codeEl'); if (codeEl) syncGutter(codeEl);
  });
  workspace.addEventListener('focusout', function (e) {
    var codeEl = e.target.closest('#codeEl'); if (codeEl) saveCode(codeEl);
  });

  workspace.addEventListener('click', function (e) {
    var tab = e.target.closest('.tab'); if (tab) { switchTab(tab.dataset.tab); return; }
    if (e.target.closest('#regenBtn')) { regenerate(); return; }
    if (e.target.closest('#promptStartOver')) { document.getElementById('newBtn').click(); return; }
    var exportBtn = e.target.closest('#exportBtn');
    if (exportBtn) { var m = workspace.querySelector('#exportMenu'); if (m) m.classList.toggle('open'); return; }
    var exp = e.target.closest('[data-export]');
    if (exp) {
      if (exp.dataset.export === 'copy') copyCode();
      else if (exp.dataset.export === 'download') downloadFile();
      var menu = workspace.querySelector('#exportMenu'); if (menu) menu.classList.remove('open');
    }
  });
  document.addEventListener('click', function (e) {
    if (!e.target.closest('.export-wrap')) { var m = workspace.querySelector('#exportMenu'); if (m) m.classList.remove('open'); }
  });

  /* ---------------- sidebar / new test ---------------- */
  document.getElementById('newBtn').addEventListener('click', function () {
    app.dataset.view = 'empty'; app.dataset.nav = 'closed';
    document.querySelectorAll('.ritem').forEach(function (r) { r.classList.remove('active'); });
    topTitle.textContent = 'New test'; clearComposer(heroInput); heroSend.disabled = true; heroInput.focus();
  });
  // library items are added at runtime — delegate so clicks work as they appear
  document.getElementById('recents').addEventListener('click', function (e) {
    var rename = e.target.closest('.ritem-rename');
    if (rename) {
      var it = rename.closest('.ritem'); it.classList.add('editing');
      var inp = it.querySelector('.ritem-input'); if (inp) { inp.focus(); inp.select(); }
      return;
    }
    var load = e.target.closest('.ritem-load');
    if (load) loadTest(load.closest('.ritem'));
  });
  // Esc cancels an in-progress rename
  document.getElementById('recents').addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { var it = e.target.closest('.ritem.editing'); if (it) it.classList.remove('editing'); }
  });
  document.getElementById('menuBtn').addEventListener('click', function () {
    app.dataset.nav = app.dataset.nav === 'open' ? 'closed' : 'open';
  });
  document.getElementById('backdrop').addEventListener('click', function () { app.dataset.nav = 'closed'; });

  /* ---------------- device context selector (topbar) ---------------- */
  var ctxBtn = document.getElementById('ctxBtn'), ctxMenu = document.getElementById('ctxMenu'), ctxLabel = document.getElementById('ctxLabel');
  function positionMenu(menu, anchor) {
    var r = anchor.getBoundingClientRect();
    menu.style.top = (r.bottom + 6) + 'px';
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
  }
  function refreshCtx() {
    var nets = allNetworks();
    if (!activeNetworkId && nets.length) activeNetworkId = nets[0].id;
    var current = activeNetwork();
    ctxLabel.textContent = current ? current.name : 'none';
    if (!nets.length) {
      ctxMenu.innerHTML = '<div class="empty">No verified networks yet.<br><a href="/settings">Add one in Settings &#8594;</a></div>';
      return;
    }
    ctxMenu.innerHTML = nets.map(function (n) {
      return '<button type="button" class="item' + (n.id === activeNetworkId ? ' active' : '') + '" data-net-id="' + esc(n.id) + '">' +
        '<span class="n">' + esc(n.name) + '</span><span class="o">' + esc(n.orgName || '') + ' · ' + (n.devices ? n.devices.length : 0) + ' devices</span></button>';
    }).join('');
  }
  ctxBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    var open = ctxMenu.classList.toggle('open');
    ctxBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) positionMenu(ctxMenu, ctxBtn);
  });
  ctxMenu.addEventListener('click', function (e) {
    var item = e.target.closest('.item'); if (!item) return;
    activeNetworkId = item.dataset.netId; refreshCtx(); ctxMenu.classList.remove('open');
  });
  document.addEventListener('click', function (e) {
    if (!ctxMenu.contains(e.target) && e.target !== ctxBtn && !ctxBtn.contains(e.target)) ctxMenu.classList.remove('open');
  });

  /* ---------------- @mention-enabled composer ---------------- */
  var mentionMenu = document.getElementById('mentionMenu');
  var mentionState = null;
  function closeMention() { mentionMenu.classList.remove('open'); mentionState = null; }
  function renderMentionMenu() {
    var devices = currentDevices();
    var q = mentionState.query.toLowerCase();
    var filtered = devices.filter(function (d) { return d.name.toLowerCase().indexOf(q) !== -1; });
    mentionState.filtered = filtered; mentionState.sel = 0;
    if (!devices.length) {
      mentionMenu.innerHTML = '<div class="mempty">No devices available. <a href="/settings">Add a network in Settings &#8594;</a></div>';
    } else if (!filtered.length) {
      mentionMenu.innerHTML = '<div class="mempty">No device matches “' + esc(mentionState.query) + '”.</div>';
    } else {
      mentionMenu.innerHTML = filtered.map(function (d, i) {
        return '<button type="button" class="mopt' + (i === 0 ? ' sel' : '') + '" data-i="' + i + '">' +
          '<span class="n">' + esc(d.name) + '</span><span class="d">' + esc(d.model) + ' · ' + esc(d.serial) + '</span></button>';
      }).join('');
    }
    mentionMenu.classList.add('open');
    positionMenu(mentionMenu, mentionState.input);
  }
  function updateSel(delta) {
    if (!mentionState || !mentionState.filtered.length) return;
    var n = mentionState.filtered.length;
    mentionState.sel = (mentionState.sel + delta + n) % n;
    Array.prototype.forEach.call(mentionMenu.querySelectorAll('.mopt'), function (el, i) { el.classList.toggle('sel', i === mentionState.sel); });
  }
  function insertMention(device) {
    var st = mentionState; if (!st) return;
    var node = st.node, text = node.textContent;
    var before = text.slice(0, st.start), after = text.slice(st.end);
    var beforeNode = document.createTextNode(before), afterNode = document.createTextNode(' ' + after);
    var chip = document.createElement('span');
    chip.className = 'mention-chip'; chip.contentEditable = 'false'; chip.textContent = '@' + device.name;
    chip.title = device.serial + ' · ' + device.model + ' · ' + device.mac;
    chip.dataset.serial = device.serial; chip.dataset.mac = device.mac; chip.dataset.model = device.model;
    chip.dataset.clientId = device.clientId; chip.dataset.networkId = device.networkId || (activeNetwork() ? activeNetwork().id : '');
    var parent = node.parentNode;
    parent.replaceChild(afterNode, node);
    parent.insertBefore(chip, afterNode);
    parent.insertBefore(beforeNode, chip);
    var range = document.createRange(), sel = window.getSelection();
    range.setStart(afterNode, 1); range.collapse(true);
    sel.removeAllRanges(); sel.addRange(range);
    st.input.dispatchEvent(new Event('input', { bubbles: true }));
    closeMention();
  }
  mentionMenu.addEventListener('mousedown', function (e) {
    var opt = e.target.closest('.mopt'); if (!opt || !mentionState) return;
    e.preventDefault(); insertMention(mentionState.filtered[Number(opt.dataset.i)]);
  });
  function detectMention(input) {
    var sel = window.getSelection();
    if (!sel.rangeCount || !input.contains(sel.anchorNode)) { closeMention(); return; }
    var node = sel.anchorNode, offset = sel.anchorOffset;
    if (node.nodeType !== 3) { closeMention(); return; }
    var before = node.textContent.slice(0, offset);
    var m = /(^|[\s ])@([\w-]*)$/.exec(before);
    if (!m) { closeMention(); return; }
    mentionState = { input: input, node: node, start: m.index + m[1].length, end: offset, query: m[2] };
    renderMentionMenu();
  }

  function wireComposer(input, send, submit) {
    input.addEventListener('input', function () {
      send.disabled = textOf(input) === '';
      detectMention(input);
    });
    input.addEventListener('keydown', function (e) {
      if (mentionState) {
        if (e.key === 'ArrowDown') { e.preventDefault(); updateSel(1); return; }
        if (e.key === 'ArrowUp') { e.preventDefault(); updateSel(-1); return; }
        if ((e.key === 'Enter' || e.key === 'Tab') && mentionState.filtered.length) { e.preventDefault(); insertMention(mentionState.filtered[mentionState.sel]); return; }
        if (e.key === 'Escape') { e.preventDefault(); closeMention(); return; }
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        var t = textOf(input); if (t) submit(t, collectDevices(input));
        return;
      }
      if (e.key === 'Backspace') {
        var s = window.getSelection();
        if (s.isCollapsed) {
          var node = s.anchorNode, off = s.anchorOffset, chip = null;
          if (node.nodeType === 3 && off === 0) chip = node.previousSibling;
          else if (node.nodeType === 1 && off > 0) { var child = node.childNodes[off - 1]; if (child && child.classList && child.classList.contains('mention-chip')) chip = child; }
          if (chip && chip.classList && chip.classList.contains('mention-chip')) { e.preventDefault(); chip.remove(); send.disabled = textOf(input) === ''; }
        }
      }
    });
    send.addEventListener('click', function () { var t = textOf(input); if (t) submit(t, collectDevices(input)); });
  }
  document.addEventListener('click', function (e) { if (!mentionMenu.contains(e.target)) closeMention(); }, true);

  // Composer language pickers default to the Settings preferred language (per-test
  // choices override it but never change this default).
  (function initComposerLang() {
    var lang = localStorage.getItem('cw.language') || 'py';
    ['heroLang', 'dockLang'].forEach(function (id) { var s = document.getElementById(id); if (s) s.value = lang; });
  })();

  wireComposer(heroInput, heroSend, fromHero);
  wireComposer(dockInput, dockSend, fromDock);
  refreshCtx();
  loadNetworks();   // fetch verified networks from the server, then refresh the picker
})();
