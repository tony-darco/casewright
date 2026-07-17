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
  var workspace = document.getElementById('workspace');
  var topTitle = document.getElementById('topTitle');

  /* ---------------- claimable devices (unclaimed org inventory) ----------------
     @-mentions offer only hardware a run could actually claim, so a test is never
     grounded on a device the run can't use. Devices live in org inventory, not in a
     network, so the list is flat across every connected org. */
  var DEVICES = [];                    // [{name,serial,mac,model,productType,orgId,orgName,clientId}]
  var DEVICE_ERROR = '';
  function loadDevices() {
    fetch('/api/devices').then(function (r) { return r.json(); }).then(function (d) {
      DEVICES = (d && d.devices) || [];
      DEVICE_ERROR = ((d && d.errors) || []).join('; ');
    }).catch(function (e) {
      // never silent: an empty picker must not be indistinguishable from a failed fetch
      DEVICES = []; DEVICE_ERROR = 'Could not load devices (' + e + ').';
    });
  }
  function currentDevices() { return DEVICES; }

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
        orgId: chip.dataset.orgId || ''
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

  // --- background generation (#11): the run lives server-side, keyed by test id.
  // The client keeps a stream open per active generation and updates the workspace
  // only while that test is the one being viewed, so navigating away doesn't stop it.
  var activeGens = {};   // testId -> { stage, code, streaming }
  function viewing() { return app.dataset.viewing || ''; }

  function renderStreamShell(prefill) {
    workspace.innerHTML = STREAM_SHELL;
    if (!prefill) return;
    var stageEl = workspace.querySelector('.stream-stage .stxt');
    var codeEl = workspace.querySelector('#streamCode');
    if (stageEl && prefill.stage) stageEl.textContent = prefill.stage;
    if (codeEl && prefill.code) { codeEl.textContent = prefill.code; codeEl.scrollTop = codeEl.scrollHeight; }
  }

  function onGenEvent(testId, ev) {
    var g = activeGens[testId] || (activeGens[testId] = { stage: '', code: '' });
    var isViewing = viewing() === String(testId);
    if (ev.type === 'stage') {
      g.stage = ev.label;
      if (isViewing) { var s = workspace.querySelector('.stream-stage .stxt'); if (s) s.textContent = ev.label; }
    } else if (ev.type === 'token') {
      g.code += ev.text;
      if (isViewing) {
        var c = workspace.querySelector('#streamCode');
        if (c) {
          var atBottom = (c.scrollHeight - c.scrollTop - c.clientHeight) <= 24;  // don't yank a scrolled-up reader
          c.textContent += ev.text;
          if (atBottom) c.scrollTop = c.scrollHeight;
        }
      }
    } else if (ev.type === 'error') {
      g.stage = ev.message;
      if (isViewing) { var se = workspace.querySelector('.stream-stage .stxt'); if (se) se.textContent = ev.message; }
    } else if (ev.type === 'done') {
      // sidebar item: done -> replace (flips status, rewires rename); failed -> remove.
      var existing = document.getElementById('test-' + testId);
      if (ev.item_html) {
        if (existing) existing.outerHTML = ev.item_html;
        else { var list = document.getElementById('testList'); if (list) list.insertAdjacentHTML('afterbegin', ev.item_html); }
        var el = document.getElementById('test-' + testId);
        if (window.htmx && el) htmx.process(el);
        if (isViewing && el) setActive(el);
      } else if (existing) {
        existing.remove();
      }
      if (isViewing) {
        workspace.innerHTML = ev.panel_html;
        // innerHTML bypasses htmx, so the panel's hx-* (the Run form) needs wiring or
        // its submit falls back to a native GET of the current URL.
        if (window.htmx) htmx.process(workspace);
        attachRunStreams(workspace);
      }
      delete activeGens[testId];
    }
  }

  function pumpStream(resp, testId) {
    var reader = resp.body.getReader(), dec = new TextDecoder(), buf = '';
    function pump() {
      return reader.read().then(function (r) {
        if (r.done) { if (activeGens[testId]) activeGens[testId].streaming = false; return; }
        buf += dec.decode(r.value, { stream: true });
        var parts = buf.split('\n\n'); buf = parts.pop();
        for (var i = 0; i < parts.length; i++) {
          var line = parts[i].replace(/^data: ?/, '');
          if (!line) continue;
          var ev; try { ev = JSON.parse(line); } catch (e) { continue; }
          onGenEvent(testId, ev);
        }
        return pump();
      });
    }
    return pump();
  }

  // Attach to a test's generation stream. focus=true shows it in the workspace.
  function attachStream(testId, focus) {
    if (focus) { app.dataset.viewing = String(testId); renderStreamShell(activeGens[testId]); }
    if (activeGens[testId] && activeGens[testId].streaming) return;   // already streaming client-side
    activeGens[testId] = activeGens[testId] || { stage: '', code: '' };
    activeGens[testId].streaming = true;
    fetch('/app/generate/' + testId + '/stream').then(function (resp) {
      var ct = resp.headers.get('content-type') || '';
      if (resp.status === 401) { window.location = '/login'; return; }
      if (!resp.ok || ct.indexOf('text/event-stream') === -1) { if (activeGens[testId]) activeGens[testId].streaming = false; if (viewing() === String(testId)) streamError(); return; }
      return pumpStream(resp, testId);
    }).catch(function () { if (activeGens[testId]) activeGens[testId].streaming = false; if (viewing() === String(testId)) streamError(); });
  }

  // Start a new generation (or regenerate): create it server-side, then attach.
  // `language` is the per-test choice; it falls back to the Settings default.
  // regenOf (optional): regenerate into an existing test as a new version (#12).
  function generate(text, devices, language, regenOf) {
    app.dataset.view = 'work';
    topTitle.innerHTML = '<b>' + esc(trunc(text, 60)) + '</b>';
    var le = document.getElementById('libEmpty'); if (le) le.hidden = true;
    app.dataset.viewing = '';                 // no test id yet; set on attach
    workspace.innerHTML = STREAM_SHELL;
    var params = {
      prompt: text, devices: JSON.stringify(devices || []),
      language: language || localStorage.getItem('cw.language') || 'py'
    };
    if (regenOf) params.regen_of = regenOf;
    fetch('/app/generate/start', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(params).toString()
    }).then(function (resp) {
      if (resp.status === 401) { window.location = '/login'; return null; }
      if (!resp.ok) { streamError(); return null; }
      return resp.json();
    }).then(function (d) {
      if (!d) return;
      var list = document.getElementById('testList');
      if (d.item_html) {
        var existing = document.getElementById('test-' + d.test_id);
        if (existing) existing.outerHTML = d.item_html;      // regenerate: reuse the item
        else if (list) list.insertAdjacentHTML('afterbegin', d.item_html);
        var el = document.getElementById('test-' + d.test_id);
        if (window.htmx && el) htmx.process(el);
      }
      attachStream(d.test_id, true);
    }).catch(streamError);
  }

  // Send a failed run back through the pipeline (prompt + the endpoints it was grounded
  // in + the code + the run's output) and ask the model to fix it. Streams like any other
  // generation and lands as a new version, so the current code stays reachable.
  function repairTest(testId) {
    app.dataset.view = 'work';
    app.dataset.viewing = '';
    workspace.innerHTML = STREAM_SHELL;
    fetch('/app/tests/' + testId + '/repair/start', { method: 'POST' }).then(function (resp) {
      if (resp.status === 401) { window.location = '/login'; return null; }
      return resp.json().then(function (d) { return resp.ok ? d : { _err: d && d.error }; });
    }).then(function (d) {
      if (!d) return;
      if (d._err) {   // e.g. nothing failed to learn from — say so, don't stall on the shell
        workspace.innerHTML = '<div class="panel"><div class="panel-body"><div class="gen-error">'
          + '<b>&#10007; Can\'t repair this test.</b><div class="muted">' + esc(d._err) + '</div>'
          + '</div></div></div>';
        return;
      }
      var existing = document.getElementById('test-' + d.test_id);
      if (d.item_html && existing) {
        existing.outerHTML = d.item_html;
        var el = document.getElementById('test-' + d.test_id);
        if (window.htmx && el) htmx.process(el);
      }
      attachStream(d.test_id, true);
    }).catch(streamError);
  }

  function langOf(id) { var s = document.getElementById(id); return s ? s.value : null; }
  function fromHero(text, devices) {
    var lang = langOf('heroLang');
    emptyView.classList.add('leaving');
    setTimeout(function () {
      generate(text, devices, lang); emptyView.classList.remove('leaving');
      clearComposer(heroInput); heroSend.disabled = true;
    }, 200);
  }

  // Open a test in the workspace. A still-generating test reattaches to its live
  // stream (#11); a finished one loads its saved code.
  function loadTest(item) {
    if (!item) return;
    var tt = item.querySelector('.tt');
    var testId = item.dataset.testId;
    setActive(item);
    app.dataset.view = 'work'; app.dataset.nav = 'closed';
    topTitle.innerHTML = '<b>' + esc(trunc((tt ? tt.textContent : 'Test').trim(), 60)) + '</b>';
    if (item.dataset.status === 'generating' || activeGens[testId]) {
      attachStream(testId, true);            // reattach: buffered progress + live updates
    } else {
      app.dataset.viewing = String(testId);
      workspace.innerHTML = GENNING;
      htmx.ajax('GET', '/app/tests/' + testId, { target: '#workspace', swap: 'innerHTML' });
    }
  }

  /* ---------------- workspace: tabs / export (delegated; survives swaps) ---------------- */
  var PANES = { prompt: '#panePrompt', code: '#paneCode', config: '#paneConfig', output: '#paneOutput' };
  function switchTab(name) {
    var panel = workspace.querySelector('.panel'); if (!panel) return;
    panel.querySelectorAll('.tab').forEach(function (t) { t.classList.toggle('active', t.dataset.tab === name); });
    Object.keys(PANES).forEach(function (key) {
      var pane = panel.querySelector(PANES[key]);
      if (pane) pane.classList.toggle('show', key === name);
    });
  }
  // Regenerate from the edited prompt (Prompt tab): reuses the same @device grounding.
  function regenerate() {
    var panel = workspace.querySelector('.panel'); if (!panel) return;
    var ta = panel.querySelector('#promptEdit'); if (!ta) return;
    var text = ta.value.trim(); if (!text) { ta.focus(); return; }
    var devices = []; try { devices = JSON.parse(panel.dataset.devices || '[]'); } catch (e) { devices = []; }
    var langSel = panel.querySelector('#regenLang');
    generate(text, devices, langSel ? langSel.value : null, panel.dataset.testId || null);  // new version (#12)
  }

  // Load a specific version of a test into the workspace (#12). The latest version is
  // the editable test itself; older ones are read-only.
  function loadVersion(testId, versionNo, count) {
    app.dataset.viewing = String(testId);
    var url = (versionNo >= count - 1) ? ('/app/tests/' + testId)
                                       : ('/app/tests/' + testId + '/versions/' + versionNo);
    workspace.innerHTML = GENNING;
    htmx.ajax('GET', url, { target: '#workspace', swap: 'innerHTML' });
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
    // The topbar Run button starts the run with the test's saved config and shows the
    // Output tab, where the terminal streams it.
    if (e.target.closest('#runBtn')) {
      var runForm = workspace.querySelector('#ptRunForm');
      if (runForm && window.htmx) htmx.trigger(runForm, 'submit');
      switchTab(runForm ? 'output' : 'config');
      return;
    }
    var fix = e.target.closest('#repairBtn');
    if (fix) { fix.disabled = true; repairTest(fix.dataset.testId); return; }
    // Test Configuration: add/remove hardware rows
    if (e.target.closest('#ptHwAdd')) {
      var rows = workspace.querySelector('#ptHwRows'), tpl = workspace.querySelector('#ptHwRowTpl');
      if (rows && tpl) rows.appendChild(tpl.content.cloneNode(true));
      return;
    }
    var hwRemove = e.target.closest('.pt-hw-remove');
    if (hwRemove) { var row = hwRemove.closest('.pt-hw-row'); if (row) row.remove(); return; }
    if (e.target.closest('#regenBtn')) { regenerate(); return; }
    if (e.target.closest('#promptStartOver')) { document.getElementById('newBtn').click(); return; }
    var vnav = e.target.closest('[data-ver-nav]'), vlatest = e.target.closest('[data-ver-latest]');
    if (vnav || vlatest) {
      var panel = workspace.querySelector('.panel'); var bar = workspace.querySelector('.version-bar');
      if (!panel || !bar) return;
      var testId = panel.dataset.testId, cur = Number(bar.dataset.version), count = Number(bar.dataset.count);
      var target = vlatest ? (count - 1) : (vnav.dataset.verNav === 'prev' ? cur - 1 : cur + 1);
      if (target >= 0 && target < count) loadVersion(testId, target, count);
      return;
    }
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
  // Test Configuration: show the example-network picker only when "build from example"
  workspace.addEventListener('change', function (e) {
    var r = e.target.closest('input[name="runSource"]'); if (!r) return;
    var pick = workspace.querySelector('.pt-netpick');
    if (pick) pick.hidden = r.value !== 'example';
  });

  /* ---------------- run stream (Run feature): live logs + status over SSE ---------------- */
  // Must mirror partials/run_status.html exactly: replayed lines are server-rendered and
  // live ones are built here, so they have to be indistinguishable.
  function termLine(stage, message, level) {
    var line = document.createElement('div');
    line.className = 'tl' + (level === 'error' ? ' tl-err' : '');
    line.dataset.stage = stage || '';
    var s = document.createElement('span'); s.className = 'tl-s'; s.textContent = stage || '';
    var m = document.createElement('span'); m.className = 'tl-m'; m.textContent = message || '';
    line.appendChild(s); line.appendChild(m);
    return line;
  }
  function attachRunStreams(root) {
    var boxes = (root || workspace).querySelectorAll('.run-status[data-run-stream]');
    boxes.forEach(function (box) {
      if (box.dataset.attached === '1') return;   // already streaming
      box.dataset.attached = '1';
      var es = new EventSource(box.dataset.runStream);
      var log = box.querySelector('#runLog'), state = box.querySelector('.term-state');
      es.onmessage = function (e) {
        var ev; try { ev = JSON.parse(e.data); } catch (_) { return; }
        if (ev.type === 'log') {
          if (log) {
            var atBottom = (log.scrollHeight - log.scrollTop - log.clientHeight) <= 24;  // don't yank a scrolled-up reader
            log.appendChild(termLine(ev.stage, ev.message, ev.level));
            if (atBottom) log.scrollTop = log.scrollHeight;
          }
        } else if (ev.type === 'status') {
          if (state) { state.textContent = ev.status; state.className = 'term-state mono run-' + ev.status; }
        } else if (ev.type === 'done') {
          es.close();
          if (log) delete log.dataset.live;   // stop the cursor even if the re-render is slow
          // re-render the whole Output pane (version output-nav + status + footer) cleanly
          // from the server, so the nav's "run i of N" reflects this now-finished run.
          if (window.htmx) htmx.ajax('GET', '/app/tests/' + box.dataset.testId + '/runs/' + box.dataset.runId,
                                     { target: '#ptRunResult', swap: 'innerHTML' });
        }
      };
      es.onerror = function () { es.close(); };
    });
  }
  document.body.addEventListener('htmx:afterSwap', function (e) {
    if (e.target && e.target.querySelector) attachRunStreams(e.target);
  });
  // Starting a run: show the Output tab, where the terminal is, and flip Run -> Re-run.
  // Only #ptRunResult re-renders from here on, so the button's own label is ours to keep
  // honest until the next full panel render.
  document.body.addEventListener('htmx:beforeRequest', function (e) {
    var form = e.detail.elt && e.detail.elt.closest && e.detail.elt.closest('#ptRunForm');
    if (!form) return;
    switchTab('output');
    var label = workspace.querySelector('#runBtn .runbtn-label');
    if (label) label.textContent = 'Re-run';
    var btn = workspace.querySelector('#runBtn');
    if (btn) btn.title = 'Run this test again';
  });

  /* ---------------- sidebar / new test ---------------- */
  document.getElementById('newBtn').addEventListener('click', function () {
    app.dataset.view = 'empty'; app.dataset.nav = 'closed'; app.dataset.viewing = '';
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

  function positionMenu(menu, anchor) {
    var r = anchor.getBoundingClientRect();
    menu.style.top = (r.bottom + 6) + 'px';
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8)) + 'px';
  }

  /* ---------------- @mention-enabled composer ---------------- */
  var mentionMenu = document.getElementById('mentionMenu');
  var mentionState = null;
  function closeMention() { mentionMenu.classList.remove('open'); mentionState = null; }
  function renderMentionMenu() {
    var devices = currentDevices();
    var q = mentionState.query.toLowerCase();
    var filtered = devices.filter(function (d) {
      return (d.name || '').toLowerCase().indexOf(q) !== -1 || (d.model || '').toLowerCase().indexOf(q) !== -1;
    });
    mentionState.filtered = filtered; mentionState.sel = 0;
    if (DEVICE_ERROR) {
      mentionMenu.innerHTML = '<div class="mempty">' + esc(DEVICE_ERROR) + '</div>';
    } else if (!devices.length) {
      mentionMenu.innerHTML = '<div class="mempty">No claimable devices. A run can only claim hardware '
        + 'that isn\'t already in a network. <a href="/settings">Check inventory in Settings &#8594;</a></div>';
    } else if (!filtered.length) {
      mentionMenu.innerHTML = '<div class="mempty">No device matches “' + esc(mentionState.query) + '”.</div>';
    } else {
      mentionMenu.innerHTML = filtered.map(function (d, i) {
        return '<button type="button" class="mopt' + (i === 0 ? ' sel' : '') + '" data-i="' + i + '">' +
          '<span class="n">' + esc(d.name) + '</span><span class="d">' + esc(d.model) + ' · ' + esc(d.serial)
          + ' · ' + esc(d.orgName || '') + '</span></button>';
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
    chip.dataset.clientId = device.clientId; chip.dataset.orgId = device.orgId || '';
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
    if (!input || !send) return;   // a composer the template doesn't render on this page
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

  // The composer's language defaults to the Settings preference (a per-test choice in
  // the Prompt tab overrides it but never changes this default).
  (function initComposerLang() {
    var lang = localStorage.getItem('cw.language') || 'py';
    var s = document.getElementById('heroLang'); if (s) s.value = lang;
  })();

  // Code-view text-wrap preference (Settings > General, #13). Set on the app root so
  // it covers both the live stream view and every swapped-in code panel.
  app.dataset.wrap = localStorage.getItem('cw.wrap') === 'on' ? 'on' : 'off';

  wireComposer(heroInput, heroSend, fromHero);
  loadDevices();    // claimable hardware for @-mentions (unclaimed org inventory)
})();
