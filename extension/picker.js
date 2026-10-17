(function () {
  if (window.__cd_picker_active) return;
  window.__cd_picker_active = true;

  var style = document.createElement('style');
  style.id = '__cd_picker_style';
  style.textContent = `
    .__cd_hover_ext {
      outline: 2px solid #6f95f0 !important;
      outline-offset: 1px !important;
      cursor: crosshair !important;
    }
  `;
  document.head.appendChild(style);

  var last = null;

  function unique(sel) {
    try {
      return document.querySelectorAll(sel).length === 1;
    } catch (e) {
      return false;
    }
  }

  function pathFor(el) {
    var parts = [];
    while (el && el.nodeType === 1 && el !== document.documentElement) {
      if (el.id && unique('#' + CSS.escape(el.id))) {
        parts.unshift('#' + CSS.escape(el.id));
        break;
      }
      var p = el.tagName.toLowerCase();
      if (el === document.body) {
        parts.unshift('body');
        break;
      }
      var par = el.parentElement;
      if (par) {
        var same = Array.prototype.filter.call(par.children, function (c) {
          return c.tagName === el.tagName;
        });
        if (same.length > 1) {
          p += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
        }
      }
      parts.unshift(p);
      el = par;
    }
    return parts.join(' > ');
  }

  function onMouseOver(e) {
    if (last) last.classList.remove('__cd_hover_ext');
    last = e.target;
    last.classList.add('__cd_hover_ext');
  }

  function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    var selector = pathFor(e.target);
    cleanup();
    chrome.runtime.sendMessage({ type: 'ELEMENT_PICKED', selector: selector });
  }

  function onKeyDown(e) {
    if (e.key === 'Escape') cleanup();
  }

  function cleanup() {
    window.__cd_picker_active = false;
    if (last) last.classList.remove('__cd_hover_ext');
    document.removeEventListener('mouseover', onMouseOver, true);
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('keydown', onKeyDown, true);
    var el = document.getElementById('__cd_picker_style');
    if (el) el.remove();
  }

  document.addEventListener('mouseover', onMouseOver, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKeyDown, true);
})();
