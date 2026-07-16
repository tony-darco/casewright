/* casewright coverage — the endpoint tree's client interactions.
   The whole tree ships collapsed in the initial HTML, so expanding is a local
   aria-expanded flip (CSS does the hiding) and never a round trip. Only the detail
   panel goes to the server. One handler, delegated from the root: with ~1000 leaves,
   per-node listeners (or per-node hx-* attributes) would be the expensive part. */
(function () {
  'use strict';
  var root = document.querySelector('.tv-root');
  var detail = document.getElementById('covDetail');
  if (!root) return;

  root.addEventListener('click', function (ev) {
    var node = ev.target.closest('.tv-node');
    if (!node || !root.contains(node)) return;
    var item = node.closest('.tv-item');

    if (node.classList.contains('tv-leaf')) {
      root.querySelectorAll('.tv-leaf.is-current').forEach(function (el) {
        el.classList.remove('is-current');
        el.closest('.tv-item').setAttribute('aria-selected', 'false');
      });
      node.classList.add('is-current');
      item.setAttribute('aria-selected', 'true');
      // the label is the endpoint id — read it rather than duplicate it into a data-ep
      // attribute on every one of ~1000 leaves
      var ep = node.querySelector('.tv-label').textContent;
      if (detail && window.htmx) {
        htmx.ajax('GET', '/coverage/endpoint?ep=' + encodeURIComponent(ep),
                  { target: '#covDetail', swap: 'innerHTML' });
      }
      return;
    }
    item.setAttribute('aria-expanded', item.getAttribute('aria-expanded') !== 'true');
  });
})();
