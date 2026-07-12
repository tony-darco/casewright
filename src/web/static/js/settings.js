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

  /* ---- "New organization" reveal-on-click + empty state (GitHub pattern) ---- */
  var orgForm = document.getElementById('orgForm'), orgHint = document.getElementById('orgHint');
  var orgEmpty = document.getElementById('orgEmpty'), orgError = document.getElementById('orgError');
  var newOrgBtn = document.getElementById('newOrgBtn'), orgCancel = document.getElementById('orgCancel');
  function showOrgForm(show) {
    if (orgForm) orgForm.hidden = !show;
    if (orgHint) orgHint.hidden = !show;
    if (newOrgBtn) newOrgBtn.hidden = show;
    if (show && orgInput) orgInput.focus();
  }
  function refreshOrgEmpty() {
    if (!orgEmpty) return;
    var listEl = document.getElementById('orgList');
    orgEmpty.hidden = !!(listEl && listEl.querySelector('.org-card'));
  }
  /* ---- API key: reveal the replace form (the status block is swapped by HTMX) ---- */
  document.body.addEventListener('click', function (e) {
    if (e.target.closest('#apikeyReplaceBtn')) {
      var f = document.getElementById('apikeyForm');
      if (f) { f.hidden = false; var i = f.querySelector('input'); if (i) i.focus(); }
    }
  });

  if (newOrgBtn) newOrgBtn.addEventListener('click', function () { showOrgForm(true); });
  if (orgCancel) orgCancel.addEventListener('click', function () {
    if (orgInput) orgInput.value = ''; if (orgError) orgError.innerHTML = ''; orgToggle(); showOrgForm(false);
  });
  refreshOrgEmpty();

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

  document.body.addEventListener('htmx:beforeRequest', function (e) {
    var form = e.detail.elt.closest && e.detail.elt.closest('form');
    var host = form && form.parentElement;
    var msg = host && host.querySelector('.set-error.js-fallback');
    if (msg) msg.remove();
  });

  /* ---- guarantee a visible message even for responses htmx won't swap (e.g. a
     raw 5xx with no rendered error partial) so failures are never silent ---- */
  document.body.addEventListener('htmx:responseError', function (e) {
    var xhr = e.detail.xhr;
    if (xhr && xhr.getResponseHeader && xhr.getResponseHeader('HX-Redirect')) return; // session-expiry redirect
    var form = e.detail.elt.closest && e.detail.elt.closest('form');
    var host = (form && form.parentElement) || e.detail.elt;
    if (!host) return;
    var msg = host.querySelector('.set-error.js-fallback');
    if (!msg) {
      msg = document.createElement('div');
      msg.className = 'set-error js-fallback';
      msg.style.display = 'block';
      host.appendChild(msg);
    }
    msg.innerHTML = '<b>&#10007;</b> Request failed (' + xhr.status + '). Check the server logs for details.';
  });

  /* ---- after each successful add: clear inputs, collapse the net-form ---- */
  document.body.addEventListener('htmx:afterRequest', function (e) {
    var xhr = e.detail.xhr; if (!xhr) return;
    var retargeted = xhr.getResponseHeader && xhr.getResponseHeader('HX-Retarget'); // errors are retargeted
    var form = e.detail.elt.closest && e.detail.elt.closest('form');
    if (form && form.id === 'providerForm') return; // provider settings stay filled after save
    if (xhr.status >= 200 && xhr.status < 300 && !retargeted && form) {
      form.querySelectorAll('input').forEach(function (i) { if (i.type !== 'hidden') i.value = ''; });
      if (form.classList.contains('net-form')) form.hidden = true;
      if (form.id === 'orgForm') showOrgForm(false);
      orgToggle();
    }
  });
  document.body.addEventListener('htmx:afterSwap', function () {
    // refresh per-org network counts (data itself is persisted server-side)
    document.querySelectorAll('.org-card').forEach(function (card) {
      var count = card.querySelectorAll('.net-card').length;
      var el = card.querySelector('[data-netcount]');
      if (el) el.textContent = count ? (count + ' network' + (count > 1 ? 's' : '')) : '';
    });
    refreshOrgEmpty();
  });
})();
