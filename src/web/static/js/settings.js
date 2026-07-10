/* casewright settings — client interactions (handoff G9/G10/G12).
   Category nav + mobile drawer, the language picker, and the Meraki add-network
   reveal. The Meraki verify/fetch round-trips themselves are HTMX (declared in the
   templates); this file handles the pure-client bits and syncs verified networks
   to localStorage so the app page can offer their devices for @-mentions (G11).
   localStorage is a deliberate stub — real persistence is a backend contract (TBD). */
(function () {
  'use strict';
  var setBody = document.getElementById('setBody');
  var setShell = document.getElementById('setShell');
  if (!setBody) return;

  /* ---- category nav + mobile drawer ---- */
  function closeNav() { setShell.dataset.navOpen = 'false'; }
  var burger = document.getElementById('setBurger');
  if (burger) burger.addEventListener('click', function () {
    setShell.dataset.navOpen = setShell.dataset.navOpen === 'true' ? 'false' : 'true';
  });
  var backdrop = document.getElementById('setBackdrop');
  if (backdrop) backdrop.addEventListener('click', closeNav);
  document.querySelectorAll('.set-nav-item').forEach(function (b) {
    b.addEventListener('click', function () {
      setBody.dataset.cat = b.dataset.cat;
      document.querySelectorAll('.set-nav-item').forEach(function (x) { x.classList.toggle('active', x === b); });
      closeNav();
    });
  });

  /* ---- output language picker (persists to localStorage; read by the app) ---- */
  var langList = document.getElementById('langList');
  if (langList) {
    var saved = localStorage.getItem('cw.language');
    if (saved) langList.querySelectorAll('.lang-opt').forEach(function (o) { o.classList.toggle('active', o.dataset.lang === saved); });
    langList.addEventListener('click', function (e) {
      var opt = e.target.closest('.lang-opt'); if (!opt) return;
      langList.querySelectorAll('.lang-opt').forEach(function (o) { o.classList.toggle('active', o === opt); });
      localStorage.setItem('cw.language', opt.dataset.lang);
    });
  }

  /* ---- org "Save" enable/disable ---- */
  var orgInput = document.getElementById('orgInput'), orgSave = document.getElementById('orgSaveBtn');
  function orgToggle() { if (orgSave) orgSave.disabled = !orgInput || orgInput.value.trim() === ''; }
  if (orgInput) { orgInput.addEventListener('input', orgToggle); orgToggle(); }

  /* ---- Meraki add-network reveal / cancel (cards are added dynamically) ---- */
  var orgList = document.getElementById('orgList');
  if (orgList) {
    orgList.addEventListener('click', function (e) {
      var add = e.target.closest('.addnet-btn');
      if (add) {
        var form = add.closest('.org-card').querySelector('.net-form');
        if (form) { form.hidden = false; var inp = form.querySelector('.net-input'); if (inp) inp.focus(); }
        return;
      }
      var cancel = e.target.closest('.netcancel');
      if (cancel) {
        var f = cancel.closest('.net-form');
        if (f) { f.hidden = true; var i = f.querySelector('.net-input'); if (i) i.value = ''; }
        var err = cancel.closest('.nested').querySelector('.net-error'); if (err) err.innerHTML = '';
      }
    });
  }

  /* ---- sync verified networks -> localStorage (for the app @-mentions / context) ---- */
  function syncNetworks() {
    var seen = {}, uniq = [];
    document.querySelectorAll('.net-card[data-network]').forEach(function (card) {
      try {
        var n = JSON.parse(card.getAttribute('data-network'));
        if (n && n.id && !seen[n.id]) { seen[n.id] = 1; uniq.push(n); }
      } catch (e) { /* ignore malformed */ }
    });
    localStorage.setItem('cw.networks', JSON.stringify(uniq));
  }

  /* ---- after each successful add: clear inputs, collapse the net-form, resync ---- */
  document.body.addEventListener('htmx:afterRequest', function (e) {
    var xhr = e.detail.xhr; if (!xhr) return;
    var retargeted = xhr.getResponseHeader && xhr.getResponseHeader('HX-Retarget'); // errors are retargeted
    var form = e.detail.elt.closest && e.detail.elt.closest('form');
    if (xhr.status >= 200 && xhr.status < 300 && !retargeted && form) {
      form.querySelectorAll('input').forEach(function (i) { if (i.type !== 'hidden') i.value = ''; });
      if (form.classList.contains('net-form')) form.hidden = true;
      orgToggle();
    }
  });
  document.body.addEventListener('htmx:afterSwap', function () {
    // refresh per-org network counts + keep localStorage current
    document.querySelectorAll('.org-card').forEach(function (card) {
      var count = card.querySelectorAll('.net-card').length;
      var el = card.querySelector('[data-netcount]');
      if (el) el.textContent = count ? (count + ' network' + (count > 1 ? 's' : '')) : '';
    });
    syncNetworks();
  });

  syncNetworks();
})();
