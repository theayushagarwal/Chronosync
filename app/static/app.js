(function () {
  'use strict';

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return document.querySelectorAll(s); };

  var state = {
    selected: null,
    versions: [],
    watches: [],
    filter: 'all',
    alertsFilter: 'all',
    search: '',
    sort: 'checked',
    currentView: 'overview',
    theme: 'dark'
  };

  // State tracker for Check Now operations: id -> { status: 'queued'|'checking'|'done', initialChecked: number, startTime: number }
  var checkTracker = {};
  var prevRowData = {}; // Cache to detect specifically changed cells on poll
  var isServerDown = false;
  var pollInterval = null;
  var pendingDeleteId = null;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function ago(ts, fallback) {
    if (!ts) return fallback || 'Awaiting check';
    var s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return 'Just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function when(ts) {
    if (!ts) return 'Never';
    return new Date(ts * 1000).toLocaleString();
  }

  function getHost(url) {
    try {
      return new URL(url).hostname;
    } catch (e) {
      return url;
    }
  }

  function formatInterval(mins) {
    if (!mins) return '1h';
    if (mins >= 1440) return Math.floor(mins / 1440) + 'd';
    if (mins >= 60) return Math.floor(mins / 60) + 'h';
    return mins + 'm';
  }

  // ==========================================================================
  // Number Count-Up Animation (Motion Token: 400ms ease-out)
  // ==========================================================================
  function animateCount(el, targetVal) {
    if (!el) return;
    targetVal = Number(targetVal) || 0;
    var current = parseInt(el.textContent, 10);
    if (isNaN(current) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      el.textContent = targetVal;
      return;
    }
    if (current === targetVal) return;

    var start = current, range = targetVal - start;
    var startTime = null, duration = 400;

    function step(ts) {
      if (!startTime) startTime = ts;
      var progress = Math.min((ts - startTime) / duration, 1);
      var ease = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.round(start + range * ease);
      if (progress < 1) {
        requestAnimationFrame(step);
      } else {
        el.textContent = targetVal;
      }
    }
    requestAnimationFrame(step);
  }

  // ==========================================================================
  // Connection Lost Banner
  // ==========================================================================
  function setConnectionLost(down) {
    if (isServerDown === down) return;
    isServerDown = down;
    var banner = $('#connection-banner');
    if (banner) {
      banner.hidden = !down;
    }
  }

  // ==========================================================================
  // API Client with Try/Catch & Error Resilience
  // ==========================================================================
  async function api(path, opts) {
    var r;
    try {
      r = await fetch(path, opts);
      setConnectionLost(false);
    } catch (err) {
      setConnectionLost(true);
      throw err;
    }
    if (!r.ok) {
      var detail = r.statusText;
      try {
        var j = await r.json();
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail);
      } catch (e) {}
      throw new Error(detail);
    }
    return r.status === 204 ? null : r.json();
  }

  function lines(v) {
    return v.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
  }

  // ==========================================================================
  // Theme Switching (Dark & Light)
  // ==========================================================================
  function initTheme() {
    var saved = localStorage.getItem('cd-theme');
    if (!saved) {
      saved = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    setTheme(saved);
  }

  function setTheme(t) {
    state.theme = t;
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('cd-theme', t);

    var darkIcon = $('#theme-icon-dark');
    var lightIcon = $('#theme-icon-light');
    if (darkIcon && lightIcon) {
      if (t === 'dark') {
        darkIcon.classList.remove('hidden');
        lightIcon.classList.add('hidden');
      } else {
        darkIcon.classList.add('hidden');
        lightIcon.classList.remove('hidden');
      }
    }
  }

  $('#theme-toggle').addEventListener('click', function () {
    var next = state.theme === 'dark' ? 'light' : 'dark';
    setTheme(next);
    showToast('Theme set to ' + (next === 'dark' ? 'Dark' : 'Light'));
  });

  // ==========================================================================
  // Toast Notifications (aria-live polite)
  // ==========================================================================
  var toastTimer = null;
  function showToast(msg, dotColor) {
    var toast = $('#toast');
    var indicator = $('.toast-indicator');
    $('#toast-msg').textContent = msg;
    if (indicator && dotColor) indicator.style.backgroundColor = dotColor;
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.classList.remove('show');
    }, 2800);
  }

  // ==========================================================================
  // View Navigation (Spacious & Clean Layout)
  // ==========================================================================
  function switchView(viewName) {
    if (state.currentView === viewName) return;
    var apply = function () {
      state.currentView = viewName;
      $$('.view-pane').forEach(function (p) {
        var isTarget = p.id === 'view-' + viewName;
        p.classList.toggle('active', isTarget);
      });
      $$('.nav-tab').forEach(function (tab) {
        var isTarget = tab.dataset.view === viewName;
        tab.classList.toggle('active', isTarget);
        tab.setAttribute('aria-selected', isTarget ? 'true' : 'false');
      });
      window.scrollTo({ top: 0, behavior: 'smooth' });
      if (viewName === 'inspector') {
        setTimeout(updateSegmentedThumb, 40);
      }
    };

    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches && document.startViewTransition) {
      document.startViewTransition(apply);
    } else {
      apply();
    }
  }

  $$('.nav-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      switchView(tab.dataset.view);
    });
  });

  $('#quick-add-btn').addEventListener('click', function () {
    switchView('add');
    var input = $('#form-url');
    if (input) setTimeout(function () { input.focus(); }, 50);
  });

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-goto]');
    if (btn) switchView(btn.dataset.goto);
  });

  // ==========================================================================
  // Keyboard Shortcuts (Linear Style)
  // ==========================================================================
  document.addEventListener('keydown', function (e) {
    var tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === '1') { e.preventDefault(); switchView('overview'); }
    else if (e.key === '2') { e.preventDefault(); switchView('monitors'); }
    else if (e.key === '3') { e.preventDefault(); switchView('inspector'); }
    else if (e.key === '4' || e.key.toLowerCase() === 'n') {
      e.preventDefault();
      switchView('add');
      var urlInput = $('#form-url');
      if (urlInput) urlInput.focus();
    }
    else if (e.key.toLowerCase() === 't') {
      e.preventDefault();
      var next = state.theme === 'dark' ? 'light' : 'dark';
      setTheme(next);
      showToast('Theme: ' + (next === 'dark' ? 'Dark' : 'Light'));
    }
    else if (e.key.toLowerCase() === 'r') {
      e.preventDefault();
      refresh();
      showToast('Refreshing data...');
    }
    else if (e.key === '/') {
      e.preventDefault();
      switchView('monitors');
      var searchInput = $('#filter-search');
      if (searchInput) searchInput.focus();
    }
    else if (e.key === '?') {
      e.preventDefault();
      var sDlg = $('#shortcuts-dialog');
      if (sDlg) {
        if (sDlg.open) sDlg.close();
        else sDlg.showModal();
      }
    }
  });

  // Shortcuts modal controls
  var shortcutsDlg = $('#shortcuts-dialog');
  var shortcutsBtn = $('#shortcuts-btn');
  var shortcutsClose = $('#shortcuts-close');
  if (shortcutsBtn && shortcutsDlg) {
    shortcutsBtn.addEventListener('click', function () { shortcutsDlg.showModal(); });
  }
  if (shortcutsClose && shortcutsDlg) {
    shortcutsClose.addEventListener('click', function () { shortcutsDlg.close(); });
  }

  // ==========================================================================
  // In-Page Accessible Remove Dialog Controls
  // ==========================================================================
  var removeDialog = $('#remove-dialog');
  var removeNameEl = $('#remove-watch-name');
  var removeConfirmBtn = $('#remove-confirm-btn');
  var removeCancelBtn = $('#remove-cancel-btn');

  function openRemoveDialog(watchId) {
    pendingDeleteId = watchId;
    var w = state.watches.find(function (x) { return x.id === watchId; });
    var name = w ? w.name : ('Monitor #' + watchId);
    if (removeNameEl) removeNameEl.textContent = name;
    if (removeDialog && typeof removeDialog.showModal === 'function') {
      removeDialog.showModal();
    }
  }

  function closeRemoveDialog() {
    pendingDeleteId = null;
    if (removeDialog && removeDialog.open) {
      removeDialog.close();
    }
  }

  if (removeCancelBtn) removeCancelBtn.addEventListener('click', closeRemoveDialog);
  if (removeDialog) {
    removeDialog.addEventListener('cancel', closeRemoveDialog);
    removeDialog.addEventListener('close', function () { pendingDeleteId = null; });
  }

  if (removeConfirmBtn) {
    removeConfirmBtn.addEventListener('click', async function () {
      if (!pendingDeleteId) return;
      var idToDelete = pendingDeleteId;
      closeRemoveDialog();
      try {
        await api('/api/watches/' + idToDelete, { method: 'DELETE' });
        showToast('Monitor removed');
        if (state.selected === idToDelete) {
          state.selected = null;
          $('#detail').hidden = true;
          $('#detail-empty-state').hidden = false;
        }
        await refresh();
      } catch (err) {
        showToast('Failed to delete monitor: ' + err.message, '#ef4444');
      }
    });
  }

  // ==========================================================================
  // Check Now State Machine (Queued -> Checking -> Done)
  // ==========================================================================
  function updateCheckButtonState(watchId) {
    var tracker = checkTracker[watchId];
    var status = tracker ? tracker.status : 'idle';

    // Update table button
    var tr = $('#watch-table tbody tr[data-id="' + watchId + '"]');
    if (tr) {
      var btn = tr.querySelector('button[data-act="check"]');
      if (btn) {
        btn.classList.remove('btn-checking', 'btn-done');
        if (status === 'queued') {
          btn.textContent = 'Queued...';
          btn.classList.add('btn-checking');
        } else if (status === 'checking') {
          btn.textContent = 'Checking...';
          btn.classList.add('btn-checking');
        } else if (status === 'done') {
          btn.textContent = 'Done ✓';
          btn.classList.add('btn-done');
        } else {
          btn.textContent = 'Check';
        }
      }
    }

    // Update studio button if selected
    if (state.selected === watchId) {
      var studioBtn = $('#studio-check-btn');
      if (studioBtn) {
        studioBtn.classList.remove('btn-checking', 'btn-done');
        if (status === 'queued') {
          studioBtn.textContent = 'Queued...';
          studioBtn.classList.add('btn-checking');
        } else if (status === 'checking') {
          studioBtn.textContent = 'Checking...';
          studioBtn.classList.add('btn-checking');
        } else if (status === 'done') {
          studioBtn.textContent = '✓ Check Complete';
          studioBtn.classList.add('btn-done');
        } else {
          studioBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg> Check Page Now';
        }
      }
    }
  }

  async function triggerCheck(watchId) {
    var w = state.watches.find(function (x) { return x.id === watchId; });
    var initial = w ? (w.last_checked || 0) : 0;
    checkTracker[watchId] = {
      status: 'queued',
      initialChecked: initial,
      startTime: Date.now()
    };
    updateCheckButtonState(watchId);
    showToast('⚡ Check queued for watch #' + watchId);

    try {
      await api('/api/watches/' + watchId + '/check', { method: 'POST' });
      if (checkTracker[watchId]) {
        checkTracker[watchId].status = 'checking';
        updateCheckButtonState(watchId);
      }
    } catch (err) {
      delete checkTracker[watchId];
      updateCheckButtonState(watchId);
      showToast('Error queueing check: ' + err.message, '#ef4444');
    }
  }

  // ==========================================================================
  // Inline Field Validation
  // ==========================================================================
  function showFieldError(fieldId, errorMsg) {
    var errEl = $('#' + fieldId + '-error');
    var inputEl = $('#' + fieldId);
    if (errEl) {
      errEl.textContent = errorMsg;
      errEl.hidden = !errorMsg;
    }
    if (inputEl && inputEl.closest('.field')) {
      inputEl.closest('.field').classList.toggle('has-error', !!errorMsg);
    }
  }

  function clearInlineValidation() {
    showFieldError('form-url', '');
    showFieldError('form-name', '');
    var msg = $('#form-msg');
    if (msg) msg.textContent = '';
  }

  // ==========================================================================
  // Add Watch Form Submission (Server Authoritative)
  // ==========================================================================
  $('#add-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    clearInlineValidation();
    var f = e.target, msg = $('#form-msg');
    var urlVal = f.url.value.trim();
    var nameVal = f.name.value.trim();

    var hasClientErr = false;
    if (!urlVal) {
      showFieldError('form-url', 'Please enter a target URL.');
      hasClientErr = true;
    } else if (!/^https?:\/\//i.test(urlVal)) {
      showFieldError('form-url', 'URL must begin with http:// or https://');
      hasClientErr = true;
    }
    if (!nameVal) {
      showFieldError('form-name', 'Please provide a display name.');
      hasClientErr = true;
    }

    if (hasClientErr) return;

    msg.className = 'form-feedback';
    msg.textContent = 'Registering new monitor...';

    try {
      await api('/api/watches', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: f.url.value,
          name: f.name.value,
          interval_minutes: +f.interval.value,
          sensitivity: f.sensitivity.value,
          watch_selectors: lines(f.watch.value),
          ignore_selectors: lines(f.ignore.value),
          keywords: f.keywords.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean),
          notify_email: f.email.value || null,
          digest: f.digest.checked
        })
      });
      f.reset();
      msg.textContent = '';
      showToast('✓ Watch added! First check in progress.', '#10b981');
      await refresh();
      switchView('monitors');
    } catch (err) {
      msg.className = 'form-feedback error';
      msg.textContent = err.message;
      if (err.message && err.message.toLowerCase().includes('url')) {
        showFieldError('form-url', err.message);
      }
      showToast('Error: ' + err.message, '#ef4444');
    }
  });

  // ==========================================================================
  // Refresh & Data Sync (Summary Strip + Keyed Table Reconciliation)
  // ==========================================================================
  async function refresh() {
    try {
      var rows = await api('/api/watches');
      state.watches = rows;

      // Update check tracker states against refreshed data
      rows.forEach(function (w) {
        var tracker = checkTracker[w.id];
        if (tracker) {
          if (tracker.status === 'checking' || tracker.status === 'queued') {
            if (w.last_checked && w.last_checked > tracker.initialChecked) {
              tracker.status = 'done';
              updateCheckButtonState(w.id);
              if (state.selected === w.id) {
                select(w.id);
              }
              setTimeout(function () {
                delete checkTracker[w.id];
                updateCheckButtonState(w.id);
              }, 2500);
            } else if (Date.now() - tracker.startTime > 30000) {
              delete checkTracker[w.id];
              updateCheckButtonState(w.id);
              showToast('Check timed out for #' + w.id, '#ef4444');
            }
          }
        }
      });

      // Update Overview KPI metrics
      var totalCount = rows.length;
      var healthyCount = rows.filter(function (w) { return w.status === 'ok'; }).length;
      var failingCount = rows.filter(function (w) { return w.status === 'error'; }).length;
      var blockedCount = rows.filter(function (w) { return w.status === 'blocked'; }).length;
      var issuesCount = failingCount + blockedCount;
      var versionsCount = rows.reduce(function (acc, w) { return acc + (w.versions || 0); }, 0);
      var dayAgo = Date.now() / 1000 - 86400;
      var changed24hCount = rows.filter(function (w) {
        return w.last_changed && w.last_changed >= dayAgo;
      }).length;

      animateCount($('#kpi-total'), totalCount);
      animateCount($('#kpi-healthy'), healthyCount);
      animateCount($('#kpi-versions'), versionsCount);
      animateCount($('#kpi-issues'), issuesCount);
      animateCount($('#nav-count'), totalCount);

      // Update Summary Strip filter chips
      animateCount($('#chip-total'), totalCount);
      animateCount($('#chip-healthy'), healthyCount);
      animateCount($('#chip-failing'), failingCount);
      animateCount($('#chip-blocked'), blockedCount);
      animateCount($('#chip-changed24h'), changed24hCount);

      // Render Overview Quick Roster
      renderQuickRoster(rows.slice(0, 5));

      // Render table
      renderTable();

      // Render Alerts Feed
      var al = await api('/api/alerts?limit=25');
      renderAlerts(al);
    } catch (err) {
      // Polling network drop handled gracefully via connection banner
    }
  }

  // Keyed Quick Roster rendering
  function renderQuickRoster(items) {
    var roster = $('#quick-roster');
    if (!roster) return;
    if (!items.length) {
      roster.innerHTML = '<p class="empty-state">No watches configured yet.</p>';
      return;
    }
    var existingCards = {};
    Array.prototype.forEach.call(roster.children, function (card) {
      if (card.dataset && card.dataset.id) {
        existingCards[card.dataset.id] = card;
      }
    });

    var activeIds = {};
    items.forEach(function (w) {
      activeIds[w.id] = true;
      var priceBadge = (w.latest_value != null) ? '<span class="metric-tag">$' + Number(w.latest_value).toFixed(2) + '</span>' : '';
      var expectedHTML =
        '<div><div class="roster-name">' + esc(w.name) + priceBadge + '</div>' +
        '<div class="roster-url">' + esc(getHost(w.url)) + ' • checked ' + ago(w.last_checked) + '</div></div>' +
        '<div style="display:flex;align-items:center;gap:8px">' +
        '<span class="status-pill ' + esc(w.status) + '">' + esc(w.status) + '</span>' +
        '<button class="btn btn-secondary btn-sm" data-act="inspect">Inspect →</button></div>';

      var card = existingCards[w.id];
      if (card) {
        if (card.innerHTML !== expectedHTML) card.innerHTML = expectedHTML;
      } else {
        card = document.createElement('div');
        card.className = 'roster-card';
        card.dataset.id = String(w.id);
        card.innerHTML = expectedHTML;
        roster.appendChild(card);
        existingCards[w.id] = card;
      }
    });

    Object.keys(existingCards).forEach(function (id) {
      if (!activeIds[id]) {
        var cardToRemove = existingCards[id];
        if (cardToRemove.parentNode) cardToRemove.parentNode.removeChild(cardToRemove);
      }
    });

    items.forEach(function (w) {
      var card = existingCards[w.id];
      if (card) roster.appendChild(card);
    });
  }

  // ==========================================================================
  // Keyed Alerts Rendering (New Marker + Type Filter)
  // ==========================================================================
  var lastAlertsDigest = '';
  function renderAlerts(alertsList) {
    var digest = JSON.stringify(alertsList) + '-' + state.alertsFilter;
    if (digest === lastAlertsDigest) return;
    lastAlertsDigest = digest;

    var container = $('#alerts');
    if (!container) return;
    if (!alertsList || !alertsList.length) {
      container.innerHTML = '<li style="background:transparent;border:none;color:var(--text-tertiary);padding:24px 8px;text-align:center">No alerts recorded yet. Confirmed page changes and health alerts stream here in real time.</li>';
      return;
    }

    var lastVisit = Number(localStorage.getItem('cd-last-alerts-visit') || 0);
    var filteredList = alertsList.filter(function (a) {
      if (state.alertsFilter === 'change') return a.kind === 'change';
      if (state.alertsFilter === 'error') return a.kind === 'error';
      return true;
    });

    if (!filteredList.length) {
      container.innerHTML = '<li style="background:transparent;border:none;color:var(--text-tertiary);padding:24px 8px;text-align:center">No alerts match the selected filter.</li>';
      return;
    }

    var existingLis = {};
    Array.prototype.forEach.call(container.children, function (li) {
      if (li.dataset && li.dataset.alertKey) {
        existingLis[li.dataset.alertKey] = li;
      }
    });

    var activeKeys = {};
    filteredList.forEach(function (a) {
      var key = String(a.id || (a.watch_id + '-' + a.created_at));
      activeKeys[key] = true;
      var isPrice = a.summary && (a.summary.includes('$') || a.summary.toLowerCase().includes('price'));
      var isErr = a.kind === 'error';
      var cls = isErr ? 'alert-error' : (isPrice ? 'alert-price' : 'alert-change');

      var iconSvg = isErr
        ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>'
        : (isPrice
          ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>'
          : '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>');

      var badge = '<span class="alert-type-badge ' + (isErr ? 'err' : (isPrice ? 'price' : 'change')) + '">' +
        iconSvg + ' ' + (isErr ? 'Error' : (isPrice ? 'Price' : 'Diff')) + '</span>';

      var isNew = lastVisit > 0 && a.created_at > lastVisit;
      var newBadge = isNew ? '<span class="alert-new-badge">New</span>' : '';

      var inner = esc(a.summary);
      if (a.link && a.kind === 'change') inner = '<a class="link" href="' + esc(a.link) + '">' + inner + '</a>';

      var expected = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:3px">' +
        '<div style="display:flex;align-items:center;gap:6px">' + badge + ' <strong>' + esc(a.watch_name) + '</strong></div>' +
        newBadge + '</div>' +
        '<div>' + inner + '</div>' +
        '<small>' + ago(a.created_at) + (a.digest ? ' • Daily Digest' : '') + '</small>';

      var li = existingLis[key];
      if (li) {
        li.className = cls;
        li.dataset.watchId = String(a.watch_id);
        if (li.innerHTML !== expected) li.innerHTML = expected;
      } else {
        li = document.createElement('li');
        li.className = cls;
        li.dataset.watchId = String(a.watch_id);
        li.dataset.alertKey = key;
        li.innerHTML = expected;
        container.appendChild(li);
        existingLis[key] = li;
      }
    });

    Object.keys(existingLis).forEach(function (key) {
      if (!activeKeys[key]) {
        var liToRemove = existingLis[key];
        if (liToRemove.parentNode) liToRemove.parentNode.removeChild(liToRemove);
      }
    });

    filteredList.forEach(function (a) {
      var key = String(a.id || (a.watch_id + '-' + a.created_at));
      var li = existingLis[key];
      if (li) container.appendChild(li);
    });

    // Update last-visit timestamp
    localStorage.setItem('cd-last-alerts-visit', String(Date.now() / 1000));
  }

  // Alerts filter controls
  $$('[data-alert-filter]').forEach(function (b) {
    b.addEventListener('click', function () {
      $$('[data-alert-filter]').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      state.alertsFilter = b.dataset.alertFilter;
      lastAlertsDigest = '';
      refresh();
    });
  });

  var refreshAlertsBtn = $('#refresh-alerts-btn');
  if (refreshAlertsBtn) {
    refreshAlertsBtn.addEventListener('click', function () {
      lastAlertsDigest = '';
      refresh();
      showToast('✓ Feed refreshed');
    });
  }

  // ==========================================================================
  // Filter & Search Table (Highlight ONLY Specifically Changed Cells)
  // ==========================================================================
  function updateRowElement(tr, w, isSel) {
    tr.dataset.id = String(w.id);
    tr.setAttribute('role', 'row');
    tr.setAttribute('tabindex', '0');
    tr.setAttribute('aria-selected', isSel ? 'true' : 'false');

    var dayAgo = Date.now() / 1000 - 86400;
    var isChanged24h = w.last_changed && w.last_changed >= dayAgo;
    tr.className = (isSel ? 'sel ' : '') + (isChanged24h ? 'changed-24h' : '');

    var metricBadge = (w.latest_value != null) ? ' <span class="metric-tag">$' + Number(w.latest_value).toFixed(2) + '</span>' : '';
    var host = getHost(w.url);
    var intervalMins = w.interval_s ? Math.round(w.interval_s / 60) : (w.interval_minutes || 60);
    var cadence = formatInterval(intervalMins);

    var tracker = checkTracker[w.id];
    var checkText = 'Check';
    var checkCls = 'btn btn-secondary btn-sm';
    if (tracker) {
      if (tracker.status === 'queued') { checkText = 'Queued...'; checkCls += ' btn-checking'; }
      else if (tracker.status === 'checking') { checkText = 'Checking...'; checkCls += ' btn-checking'; }
      else if (tracker.status === 'done') { checkText = 'Done ✓'; checkCls += ' btn-done'; }
    }

    var statusExpl = '';
    if (w.status === 'blocked') statusExpl = 'Bot check blocked: site anti-scraping active. No retries.';
    else if (w.status === 'error') statusExpl = 'Failing ' + (w.consecutive_failures || 1) + ' attempts.';
    else if (w.status === 'pending') statusExpl = 'Waiting for first check.';
    else if (w.status === 'ok') statusExpl = 'Healthy and passing checks.';

    var tip = (w.last_error || statusExpl) ? ' title="' + esc(w.last_error || statusExpl) + '"' : '';

    var prev = prevRowData[w.id];
    var statusChanged = prev && (prev.status !== w.status);
    var checkedChanged = prev && (prev.checked !== w.last_checked);
    var changedChanged = prev && (prev.changed !== w.last_changed);
    var cadenceChanged = prev && (prev.cadence !== cadence);
    var versionsChanged = prev && (prev.versions !== w.versions);

    // Columns: 1. Target Page, 2. Status, 3. Last checked, 4. Last changed, 5. Interval, 6. Changes, 7. Actions
    var targetCell = '<div class="table-target"><div class="table-target-name">' + esc(w.name) + metricBadge + '</div>' +
      '<div class="table-target-url" title="' + esc(w.url) + '">' + esc(host) + '</div></div>';

    var statusCell = '<span class="status-pill ' + esc(w.status) + (tracker && (tracker.status === 'checking' || tracker.status === 'queued') ? ' checking' : '') + '"' + tip + '>' + esc(w.status) + '</span>' +
      (w.last_error ? '<div class="table-target-url" style="color:var(--danger)" title="' + esc(w.last_error) + '">' + esc(w.last_error.slice(0, 32)) + (w.last_error.length > 32 ? '…' : '') + '</div>' : '');

    var checkedCell = w.last_checked
      ? '<time datetime="' + new Date(w.last_checked * 1000).toISOString() + '" title="' + esc(when(w.last_checked)) + '">' + ago(w.last_checked) + '</time>'
      : '<span style="color:var(--text-tertiary)">Awaiting check</span>';

    var changedCell = w.last_changed
      ? '<time datetime="' + new Date(w.last_changed * 1000).toISOString() + '" title="' + esc(when(w.last_changed)) + '">' + ago(w.last_changed) + '</time>'
      : '<span style="color:var(--text-tertiary)">No recorded change</span>';

    var intervalCell = '<span class="cadence-badge" title="Checked every ' + cadence + '">' + cadence + '</span>';

    var changesText = '';
    if (!w.versions || w.versions === 0) {
      changesText = '<span style="color:var(--text-tertiary)">—</span>';
    } else if (w.versions === 1) {
      changesText = '<span class="badge-subtle" title="Initial snapshot baseline">Baseline only</span>';
    } else {
      var numChanges = w.versions - 1;
      changesText = '<strong style="font-family:var(--mono)">' + numChanges + '</strong> <span style="font-size:11px;color:var(--text-tertiary)">' + (numChanges === 1 ? 'change' : 'changes') + '</span>';
    }
    var changesCell = '<div class="changes-val">' + changesText + '</div>';

    var actionsCell = '<button class="' + checkCls + '" data-act="check">' + checkText + '</button> ' +
      '<button class="btn btn-primary btn-sm" data-act="inspect">Inspect</button> ' +
      '<button class="btn btn-danger btn-sm" data-act="del" title="Delete monitor">×</button>';

    var cells = [targetCell, statusCell, checkedCell, changedCell, intervalCell, changesCell, actionsCell];

    if (tr.children.length === 7) {
      for (var i = 0; i < 7; i++) {
        var td = tr.children[i];
        if (td.innerHTML !== cells[i]) {
          td.innerHTML = cells[i];
          var shouldHighlight = (i === 1 && statusChanged) ||
                                (i === 2 && checkedChanged) ||
                                (i === 3 && changedChanged) ||
                                (i === 4 && cadenceChanged) ||
                                (i === 5 && versionsChanged);
          if (shouldHighlight) {
            td.classList.add('cell-highlight');
            setTimeout(function (t) { t.classList.remove('cell-highlight'); }, 1000, td);
          }
        }
      }
    } else {
      tr.innerHTML = cells.map(function (c, idx) {
        return '<td' + (idx === 6 ? ' style="text-align:right"' : '') + '>' + c + '</td>';
      }).join('');
    }

    prevRowData[w.id] = {
      name: w.name,
      status: w.status,
      cadence: cadence,
      checked: w.last_checked,
      changed: w.last_changed,
      versions: w.versions
    };
  }

  function renderTable() {
    var list = state.watches.filter(function (w) {
      if (state.filter === 'ok' && w.status !== 'ok') return false;
      if (state.filter === 'blocked' && w.status !== 'blocked') return false;
      if (state.filter === 'error' && w.status !== 'error') return false;
      if (state.filter === 'changed24h') {
        var dayAgo = Date.now() / 1000 - 86400;
        if (!w.last_changed || w.last_changed < dayAgo) return false;
      }
      if (state.search) {
        var q = state.search.toLowerCase();
        return (w.name && w.name.toLowerCase().includes(q)) || (w.url && w.url.toLowerCase().includes(q));
      }
      return true;
    });

    // Sorting
    var sortMode = state.sort || 'checked';
    list.sort(function (a, b) {
      if (sortMode === 'name') {
        return (a.name || '').localeCompare(b.name || '');
      }
      if (sortMode === 'checked') {
        return (b.last_checked || 0) - (a.last_checked || 0);
      }
      if (sortMode === 'changed') {
        return (b.last_changed || 0) - (a.last_changed || 0);
      }
      if (sortMode === 'changes') {
        return ((b.versions || 0) - 1) - ((a.versions || 0) - 1);
      }
      if (sortMode === 'status') {
        var rank = { error: 0, blocked: 1, pending: 2, ok: 3 };
        return (rank[a.status] || 9) - (rank[b.status] || 9);
      }
      return 0;
    });

    var tbody = $('#watch-table tbody');
    if (!tbody) return;
    $('#empty').hidden = list.length > 0;

    var existingRows = {};
    Array.prototype.forEach.call(tbody.children, function (child) {
      if (child.dataset && child.dataset.id) {
        existingRows[child.dataset.id] = child;
      }
    });

    var activeIds = {};
    list.forEach(function (w) {
      activeIds[w.id] = true;
      var tr = existingRows[w.id];
      var isSel = state.selected === w.id;
      if (tr) {
        updateRowElement(tr, w, isSel);
      } else {
        tr = document.createElement('tr');
        updateRowElement(tr, w, isSel);
        tbody.appendChild(tr);
        existingRows[w.id] = tr;
      }
    });

    Object.keys(existingRows).forEach(function (id) {
      if (!activeIds[id]) {
        var rowToRemove = existingRows[id];
        if (rowToRemove.parentNode) {
          rowToRemove.parentNode.removeChild(rowToRemove);
        }
      }
    });

    list.forEach(function (w) {
      var tr = existingRows[w.id];
      if (tr) tbody.appendChild(tr);
    });
  }

  // Summary strip filter chips click delegation
  $$('.summary-chip').forEach(function (chip) {
    chip.addEventListener('click', function () {
      var f = chip.dataset.filter;
      $$('.summary-chip').forEach(function (c) { c.classList.toggle('active', c === chip); });
      $$('.filter-pill').forEach(function (p) { p.classList.toggle('active', p.dataset.filter === f); });
      state.filter = f;
      renderTable();
    });
  });

  // Table search filter
  var searchInput = $('#filter-search');
  if (searchInput) {
    searchInput.addEventListener('input', function (e) {
      state.search = e.target.value.trim();
      renderTable();
    });
  }

  // Sort dropdown
  var sortSelect = $('#table-sort');
  if (sortSelect) {
    sortSelect.addEventListener('change', function (e) {
      state.sort = e.target.value;
      renderTable();
    });
  }

  // Filter pills
  $$('.filter-pill').forEach(function (b) {
    b.addEventListener('click', function () {
      var f = b.dataset.filter;
      $$('.filter-pill').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      $$('.summary-chip').forEach(function (c) { c.classList.toggle('active', c.dataset.filter === f); });
      state.filter = f;
      renderTable();
    });
  });

  // Table row click delegation
  var watchTableTbody = $('#watch-table tbody');
  if (watchTableTbody) {
    watchTableTbody.addEventListener('click', async function (e) {
      var tr = e.target.closest('tr');
      if (!tr) return;
      var id = +tr.dataset.id, act = e.target.dataset.act;

      if (act === 'check') {
        e.stopPropagation();
        triggerCheck(id);
        return;
      }
      if (act === 'del') {
        e.stopPropagation();
        openRemoveDialog(id);
        return;
      }
      select(id);
      switchView('inspector');
    });

    // Keyboard navigation on table rows (Enter / Space)
    watchTableTbody.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        var tr = e.target.closest('tr');
        if (tr && e.target.tagName !== 'BUTTON') {
          e.preventDefault();
          var id = +tr.dataset.id;
          select(id);
          switchView('inspector');
        }
      }
    });
  }

  // Quick roster click delegation
  var quickRoster = $('#quick-roster');
  if (quickRoster) {
    quickRoster.addEventListener('click', function (e) {
      var card = e.target.closest('.roster-card');
      if (!card) return;
      var id = +card.dataset.id;
      select(id);
      switchView('inspector');
    });
  }

  // Alerts feed click delegation
  var alertsListEl = $('#alerts');
  if (alertsListEl) {
    alertsListEl.addEventListener('click', function (e) {
      var li = e.target.closest('li[data-watch-id]');
      if (!li || e.target.tagName === 'A') return;
      var id = +li.dataset.watchId;
      if (id) {
        select(id);
        switchView('inspector');
      }
    });
  }

  // Studio Check Button
  var studioCheckBtn = $('#studio-check-btn');
  if (studioCheckBtn) {
    studioCheckBtn.addEventListener('click', function () {
      if (!state.selected) return;
      triggerCheck(state.selected);
    });
  }

  // ==========================================================================
  // Detail Close Control
  // ==========================================================================
  var detailCloseBtn = $('#detail-close');
  if (detailCloseBtn) {
    detailCloseBtn.addEventListener('click', function () {
      state.selected = null;
      $('#detail').hidden = true;
      $('#detail-empty-state').hidden = false;
      $$('#watch-table tbody tr').forEach(function (r) {
        r.classList.remove('sel');
        r.setAttribute('aria-selected', 'false');
      });
      if (location.hash && location.hash.startsWith('#diff/')) {
        history.replaceState(null, '', location.pathname + location.search);
      }
      switchView('monitors');
    });
  }

  // ==========================================================================
  // Segmented Modes Sliding Thumb & ARIA Tabs Pattern
  // ==========================================================================
  function updateSegmentedThumb() {
    var modes = $('.segmented-modes');
    var thumb = $('#segmented-thumb');
    var activeTab = $('.segmented-modes .mode-tab.on');
    if (!modes || !thumb || !activeTab) return;
    thumb.style.width = activeTab.offsetWidth + 'px';
    thumb.style.transform = 'translateX(' + activeTab.offsetLeft + 'px)';
  }

  window.addEventListener('resize', updateSegmentedThumb);

  function activateTab(btn) {
    if (!btn) return;
    var tabName = btn.dataset.tab;
    if (!tabName) return;

    $$('.segmented-modes .mode-tab').forEach(function (b) {
      var isTarget = b === btn;
      b.classList.toggle('on', isTarget);
      b.setAttribute('aria-selected', isTarget ? 'true' : 'false');
      b.setAttribute('tabindex', isTarget ? '0' : '-1');
    });

    updateSegmentedThumb();
    ['slider', 'text', 'shot', 'side', 'chart'].forEach(function (n) {
      var p = $('#tab-' + n);
      if (p) p.hidden = n !== tabName;
    });
    if (tabName === 'slider') {
      setTimeout(syncSliderWidth, 50);
    }
    btn.focus();
  }

  // ARIA Arrow Key Navigation for Tablist
  var segmentedModes = $('.segmented-modes');
  if (segmentedModes) {
    segmentedModes.addEventListener('click', function (e) {
      var btn = e.target.closest('.mode-tab');
      if (btn) activateTab(btn);
    });

    segmentedModes.addEventListener('keydown', function (e) {
      var tabs = Array.from($$('.segmented-modes .mode-tab'));
      var currentIdx = tabs.findIndex(function (t) { return t === document.activeElement; });
      if (currentIdx === -1) return;

      var targetIdx = null;
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        targetIdx = (currentIdx + 1) % tabs.length;
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        targetIdx = (currentIdx - 1 + tabs.length) % tabs.length;
      } else if (e.key === 'Home') {
        e.preventDefault();
        targetIdx = 0;
      } else if (e.key === 'End') {
        e.preventDefault();
        targetIdx = tabs.length - 1;
      }

      if (targetIdx !== null) {
        activateTab(tabs[targetIdx]);
      }
    });
  }

  // ==========================================================================
  // Diff Studio Details (Reusing Cached Watch List & Plain-Language Status)
  // ==========================================================================
  async function select(id, a, b) {
    state.selected = id;

    // Reuse cached watch list first; fetch only if cache miss
    var w = state.watches.find(function (x) { return x.id === id; });
    if (!w) {
      try {
        var all = await api('/api/watches');
        state.watches = all;
        w = all.find(function (x) { return x.id === id; });
      } catch (e) {}
    }
    if (!w) return;

    $('#detail-empty-state').hidden = true;
    var detailEl = $('#detail');
    detailEl.hidden = false;
    $('#detail-title').textContent = w.name;
    var urlEl = $('#detail-url');
    urlEl.textContent = w.url;
    urlEl.href = w.url;
    $('#detail-status').className = 'status-pill ' + w.status;
    $('#detail-status').textContent = w.status;
    $('#rss-link').href = '/feeds/' + id + '.xml';

    // Plain-language status explanations
    var explBox = $('#detail-status-explanation');
    if (explBox) {
      if (w.status === 'blocked') {
        explBox.hidden = false;
        explBox.innerHTML = '🛡️ <strong>Bot Protection Detected:</strong> The target website presented a Cloudflare/bot challenge. Autonomous retries are suspended to protect against IP banning.';
      } else if (w.status === 'error') {
        explBox.hidden = false;
        var fails = w.consecutive_failures || 1;
        explBox.innerHTML = '⚠️ <strong>Connection Error:</strong> Check failing consecutively (' + fails + ' ' + (fails === 1 ? 'attempt' : 'attempts') + '). The engine will retry automatically according to the scheduled interval.';
      } else if (w.status === 'pending') {
        explBox.hidden = false;
        explBox.innerHTML = '⏳ <strong>Pending First Check:</strong> Waiting for Playwright browser instance to capture initial snapshot.';
      } else {
        explBox.hidden = true;
      }
    }

    // Technical details disclosure for raw error stack
    var err = $('#detail-error');
    var techDetails = $('#tech-details');
    var techPre = $('#tech-details-content');
    if (w.last_error) {
      err.hidden = false;
      err.textContent = 'Last error: ' + w.last_error;
      if (techDetails && techPre) {
        techDetails.hidden = false;
        techPre.textContent = w.last_error;
      }
    } else {
      err.hidden = true;
      if (techDetails) techDetails.hidden = true;
    }

    // Update check button state for studio
    updateCheckButtonState(id);

    try {
      state.versions = await api('/api/watches/' + id + '/versions');
      var total = state.versions.length;
      var opts = state.versions.map(function (v, idx) {
        var vNum = 'v' + (total - idx);
        var timeLabel = ago(v.created_at);
        var isLatest = idx === 0 ? ' · latest' : '';
        var label = vNum + ' · ' + timeLabel + isLatest;
        return '<option value="' + v.id + '" title="' + esc(v.hash) + ' (' + when(v.created_at) + ')">' + esc(label) + '</option>';
      }).join('');
      $('#ver-a').innerHTML = opts;
      $('#ver-b').innerHTML = opts;

      if (state.versions.length) {
        $('#ver-b').value = b || state.versions[0].id;
        $('#ver-a').value = a || (state.versions[1] || state.versions[0]).id;
      }
    } catch (e) {
      showToast('Could not load versions: ' + e.message, '#ef4444');
    }

    $$('#watch-table tbody tr').forEach(function (r) {
      var isMatch = +r.dataset.id === id;
      r.classList.toggle('sel', isMatch);
      r.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    await compare();
    renderMetrics(id);
    setTimeout(updateSegmentedThumb, 40);
  }

  // ==========================================================================
  // Comparison Engine (Screenshot Fade-In, Skeletons, 404 Fallback, Lightbox)
  // ==========================================================================
  async function compare() {
    var id = state.selected, a = $('#ver-a').value, b = $('#ver-b').value;
    var t = $('#tab-text'), s = $('#tab-shot'), p = $('#tab-side');

    if (!a || !b) {
      t.innerHTML = s.innerHTML = p.innerHTML = '<p class="empty-state">No versions recorded yet. Initial check in progress.</p>';
      return;
    }

    // Update URL hash without causing recursive hashchange
    var hashStr = '#diff/' + id + '/' + a + '/' + b;
    if (location.hash !== hashStr) {
      history.replaceState(null, '', hashStr);
    }

    // Set Slider Images with Smooth Fade-in & Skeleton Shimmer
    var imgBefore = $('#slider-img-before');
    var imgAfter = $('#slider-img-after');
    var skeleton = $('#slider-skeleton');
    if (skeleton) skeleton.classList.remove('hidden');

    function handleImgLoad() {
      if (skeleton) skeleton.classList.add('hidden');
      this.style.transition = 'opacity var(--dur-normal) var(--ease-standard)';
      this.style.opacity = '1';
      syncSliderWidth();
    }

    function handleImgErr() {
      if (skeleton) skeleton.classList.add('hidden');
      this.alt = 'Screenshot unavailable (404)';
      this.style.opacity = '0.5';
    }

    imgBefore.style.opacity = '0';
    imgAfter.style.opacity = '0';
    imgBefore.onload = handleImgLoad;
    imgAfter.onload = handleImgLoad;
    imgBefore.onerror = handleImgErr;
    imgAfter.onerror = handleImgErr;

    imgBefore.src = '/api/snapshots/' + a + '/screenshot';
    imgAfter.src = '/api/snapshots/' + b + '/screenshot';
    initSlider();

    try {
      var d = await api('/api/watches/' + id + '/diff?a=' + a + '&b=' + b);
      if (a !== b) {
        var addedStr = d.added + (d.added === 1 ? ' line added' : ' lines added');
        var removedStr = d.removed + (d.removed === 1 ? ' line removed' : ' lines removed');

        var diffLegend = '<div style="display:flex;align-items:center;gap:12px;font-size:12px;color:var(--text-tertiary);margin-bottom:10px;">' +
          '<span>Legend:</span>' +
          '<span style="display:inline-flex;align-items:center;gap:4px;"><span style="display:inline-block;width:10px;height:10px;background:var(--success);border-radius:2px;"></span> Added</span>' +
          '<span style="display:inline-flex;align-items:center;gap:4px;"><span style="display:inline-block;width:10px;height:10px;background:var(--danger);border-radius:2px;"></span> Removed</span>' +
          '</div>';

        t.innerHTML = '<div style="margin-bottom:12px;font-size:13px;color:var(--text-secondary)">' +
          '<strong>' + addedStr + '</strong>, <strong>' + removedStr + '</strong></div>' +
          diffLegend +
          (d.blocks.some(function (x) { return x.tag === 'add' || x.tag === 'del'; })
            ? '<div class="diff-view">' + d.blocks.map(function (x) { return '<div class="diff-line ' + x.tag + '">' + esc(x.text) + '</div>'; }).join('') + '</div>'
            : '<p class="empty-state">No text changes detected in watched selectors between these two snapshots.</p>');
      } else {
        t.innerHTML = '<p class="empty-state">Select two different versions above to compare text changes.</p>';
      }

      p.innerHTML = '<div class="side-by-side-wrap">' +
        '<div class="side-col"><div class="side-header">' + esc(when(d.a.created_at)) + ' (Older)</div><pre style="padding:12px;font-family:var(--mono);font-size:12px;overflow-x:auto">' + esc(d.old_text) + '</pre></div>' +
        '<div class="side-col"><div class="side-header">' + esc(when(d.b.created_at)) + ' (Newer)</div><pre style="padding:12px;font-family:var(--mono);font-size:12px;overflow-x:auto">' + esc(d.new_text) + '</pre></div></div>' +
        '<div class="side-by-side-wrap" style="margin-top:16px"><div class="side-col"><div class="side-header">Older Screenshot (Click to enlarge)</div><img class="zoomable-img" alt="Older snapshot" src="/api/snapshots/' + a + '/screenshot" onerror="this.alt=\'Screenshot 404\';"></div>' +
        '<div class="side-col"><div class="side-header">Newer Screenshot (Click to enlarge)</div><img class="zoomable-img" alt="Newer snapshot" src="/api/snapshots/' + b + '/screenshot" onerror="this.alt=\'Screenshot 404\';"></div></div>';

      s.innerHTML = a === b ? '<p class="empty-state">Select two different versions to compute visual pixel heatmap.</p>' :
        '<div style="margin-bottom:10px;font-size:12.5px;color:var(--text-tertiary)">Pixel modifications tinted red over newer snapshot (Click to enlarge):</div><div style="border:1px solid var(--border);border-radius:var(--radius-md);overflow:hidden;background:#fff"><img class="zoomable-img" style="width:100%;display:block" alt="Heatmap Diff" src="/api/diff/screenshot?a=' + a + '&b=' + b + '" onerror="this.alt=\'Screenshot heatmap 404\';"></div>';
    } catch (e) {
      t.innerHTML = '<p class="alert-banner error">' + esc(e.message) + '</p>';
    }
  }

  // ==========================================================================
  // Copy Link to Comparison
  // ==========================================================================
  var copyDiffBtn = $('#copy-diff-btn');
  if (copyDiffBtn) {
    copyDiffBtn.addEventListener('click', function () {
      if (!state.selected) return;
      var a = $('#ver-a').value, b = $('#ver-b').value;
      var url = location.origin + location.pathname + '#diff/' + state.selected + '/' + a + '/' + b;
      navigator.clipboard.writeText(url).then(function () {
        showToast('🔗 Comparison link copied to clipboard!');
      }).catch(function () {
        showToast('Comparison link: ' + url);
      });
    });
  }

  // ==========================================================================
  // Lightbox Click-to-Enlarge Screenshots
  // ==========================================================================
  var lightboxDlg = $('#lightbox-dialog');
  var lightboxImg = $('#lightbox-img');
  var lightboxClose = $('#lightbox-close');

  document.addEventListener('click', function (e) {
    var img = e.target.closest('.zoomable-img');
    if (img && lightboxDlg && lightboxImg && img.src) {
      lightboxImg.src = img.src;
      lightboxDlg.showModal();
    }
  });

  if (lightboxClose && lightboxDlg) {
    lightboxClose.addEventListener('click', function () { lightboxDlg.close(); });
  }
  if (lightboxDlg) {
    lightboxDlg.addEventListener('click', function (e) {
      var rect = lightboxDlg.getBoundingClientRect();
      var inBox = (rect.top <= e.clientY && e.clientY <= rect.top + rect.height
        && rect.left <= e.clientX && e.clientX <= rect.left + rect.width);
      if (!inBox) lightboxDlg.close();
    });
  }

  // ==========================================================================
  // Draggable Comparison Slider
  // ==========================================================================
  var sliderState = {
    isDragging: false,
    currentPct: 50,
    initialized: false
  };

  function syncSliderWidth() {
    var container = $('#visual-slider');
    var imgBefore = $('#slider-img-before');
    if (!container || !imgBefore) return;
    var w = container.clientWidth;
    if (w > 0) {
      imgBefore.style.width = w + 'px';
      imgBefore.style.maxWidth = w + 'px';
    }
  }

  function setSliderPosition(clientX) {
    var container = $('#visual-slider');
    var wrap = $('#slider-before-wrap');
    var handle = $('#slider-handle');
    if (!container || !wrap || !handle) return;
    var rect = container.getBoundingClientRect();
    if (!rect.width) return;
    var pct = ((clientX - rect.left) / rect.width) * 100;
    pct = Math.max(0, Math.min(100, pct));
    sliderState.currentPct = pct;
    wrap.style.width = pct + '%';
    handle.style.left = pct + '%';
    handle.setAttribute('aria-valuenow', Math.round(pct));
  }

  function initSlider() {
    var container = $('#visual-slider');
    var wrap = $('#slider-before-wrap');
    var handle = $('#slider-handle');
    var imgBefore = $('#slider-img-before');
    if (!container || !wrap || !handle || !imgBefore) return;

    sliderState.currentPct = 50;
    wrap.style.width = '50%';
    handle.style.left = '50%';
    handle.setAttribute('aria-valuenow', '50');

    syncSliderWidth();

    if (sliderState.initialized) return;
    sliderState.initialized = true;

    // Pointer Events for Mouse, Touch & Pen with PointerCapture
    container.addEventListener('pointerdown', function (e) {
      sliderState.isDragging = true;
      try {
        container.setPointerCapture(e.pointerId);
      } catch (err) {}
      setSliderPosition(e.clientX);
      e.preventDefault();
    });

    container.addEventListener('pointermove', function (e) {
      if (!sliderState.isDragging) return;
      setSliderPosition(e.clientX);
    });

    function stopSliderDrag(e) {
      if (sliderState.isDragging) {
        sliderState.isDragging = false;
        try {
          if (e.pointerId) container.releasePointerCapture(e.pointerId);
        } catch (err) {}
      }
    }

    container.addEventListener('pointerup', stopSliderDrag);
    container.addEventListener('pointercancel', stopSliderDrag);

    // Keyboard support: Arrow keys on slider handle
    handle.addEventListener('keydown', function (e) {
      var step = e.shiftKey ? 10 : 2;
      var rect = container.getBoundingClientRect();
      if (!rect.width) return;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
        e.preventDefault();
        var nextPct = Math.max(0, sliderState.currentPct - step);
        setSliderPosition(rect.left + (rect.width * nextPct) / 100);
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
        e.preventDefault();
        var nextPct = Math.min(100, sliderState.currentPct + step);
        setSliderPosition(rect.left + (rect.width * nextPct) / 100);
      } else if (e.key === 'Home') {
        e.preventDefault();
        setSliderPosition(rect.left);
      } else if (e.key === 'End') {
        e.preventDefault();
        setSliderPosition(rect.left + rect.width);
      }
    });

    window.addEventListener('resize', syncSliderWidth);
  }

  // ==========================================================================
  // Price & Metric History Chart
  // ==========================================================================
  async function renderMetrics(watchId) {
    var pane = $('#tab-chart');
    if (!pane) return;
    try {
      var res = await api('/api/watches/' + watchId + '/metrics');
      var list = res.metrics || [];
      var stats = res.stats || {};
      if (!list.length) {
        pane.innerHTML = '<p class="empty-state">No numerical price values detected for this target yet.</p>';
        return;
      }
      var vals = list.map(function (m) { return m.value; });
      var min = Math.min.apply(null, vals);
      var max = Math.max.apply(null, vals);
      var range = max - min || (max * 0.1) || 1;
      var padY = range * 0.18;
      var yMin = min - padY;
      var yMax = max + padY;
      var ySpan = yMax - yMin;

      var W = 640, H = 200, padL = 60, padR = 30, padT = 25, padB = 35;
      var plotW = W - padL - padR, plotH = H - padT - padB;
      var pts = list.map(function (m, idx) {
        var x = list.length === 1 ? padL + plotW / 2 : padL + (idx / (list.length - 1)) * plotW;
        var y = padT + (1 - (m.value - yMin) / ySpan) * plotH;
        return { x: x, y: y, val: m.value, time: m.created_at };
      });
      var pathD = pts.map(function (p, idx) { return (idx === 0 ? 'M' : 'L') + p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' ');

      var areaD = '';
      if (list.length > 1) {
        var firstPt = pts[0], lastPt = pts[pts.length - 1];
        var baseY = H - padB;
        areaD = pathD + ' L' + lastPt.x.toFixed(1) + ',' + baseY + ' L' + firstPt.x.toFixed(1) + ',' + baseY + ' Z';
      }

      var topY = padT + (1 - (max - yMin) / ySpan) * plotH;
      var botY = padT + (1 - (min - yMin) / ySpan) * plotH;

      var changeSign = stats.change > 0 ? '+' : '';
      var changeColor = stats.change > 0 ? 'color:var(--success)' : (stats.change < 0 ? 'color:var(--danger)' : '');

      var html = '<div class="chart-box">' +
        '<div class="chart-stats-grid">' +
        '<div class="chart-stat-card"><div class="chart-stat-label">Current Value</div><div class="chart-stat-val">$' + Number(stats.latest).toFixed(2) + '</div></div>' +
        '<div class="chart-stat-card"><div class="chart-stat-label">Total Change</div><div class="chart-stat-val" style="' + changeColor + '">' + changeSign + '$' + Number(stats.change).toFixed(2) + ' (' + changeSign + stats.change_pct + '%)</div></div>' +
        '<div class="chart-stat-card"><div class="chart-stat-label">Lowest Record</div><div class="chart-stat-val">$' + Number(stats.min).toFixed(2) + '</div></div>' +
        '<div class="chart-stat-card"><div class="chart-stat-label">Peak Record</div><div class="chart-stat-val">$' + Number(stats.max).toFixed(2) + '</div></div>' +
        '</div>';

      html += '<svg class="chart-svg" viewBox="0 0 ' + W + ' ' + H + '">' +
        '<defs>' +
        '<linearGradient id="chartAreaGrad" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="var(--accent)" stop-opacity="0.3"/>' +
        '<stop offset="100%" stop-color="var(--accent)" stop-opacity="0.0"/>' +
        '</linearGradient>' +
        '</defs>' +
        '<line x1="' + padL + '" y1="' + topY.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + topY.toFixed(1) + '" stroke="var(--border)" stroke-dasharray="3 3"/>' +
        '<text x="' + (padL - 10) + '" y="' + (topY + 4).toFixed(1) + '" text-anchor="end" fill="var(--text-tertiary)" font-size="11">$' + max.toFixed(2) + '</text>' +
        '<line x1="' + padL + '" y1="' + botY.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + botY.toFixed(1) + '" stroke="var(--border)" stroke-dasharray="3 3"/>' +
        '<text x="' + (padL - 10) + '" y="' + (botY + 4).toFixed(1) + '" text-anchor="end" fill="var(--text-tertiary)" font-size="11">$' + min.toFixed(2) + '</text>' +
        (areaD ? '<path d="' + areaD + '" fill="url(#chartAreaGrad)"/>' : '') +
        (list.length > 1 ? '<path d="' + pathD + '" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>' : '') +
        pts.map(function (p) {
          return '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="4.5" fill="var(--accent)" stroke="var(--bg-surface)" stroke-width="2">' +
            '<title>$' + p.val.toFixed(2) + ' (' + when(p.time) + ')</title></circle>' +
            '<text x="' + p.x.toFixed(1) + '" y="' + (p.y - 10).toFixed(1) + '" text-anchor="middle" fill="var(--text-primary)" font-size="11" font-weight="600">$' + p.val.toFixed(2) + '</text>';
        }).join('') +
        '</svg>';

      html += '<h4 style="margin:20px 0 8px;font-size:13.5px;font-weight:600">Recorded Metric Timeline</h4>' +
        '<table class="history-table"><thead><tr><th>Timestamp</th><th>Value</th><th>Movement</th></tr></thead><tbody>';
      html += list.slice().reverse().map(function (m, idx, arr) {
        var prev = arr[idx + 1];
        var diff = prev ? (m.value - prev.value) : 0;
        var diffTxt = prev ? ((diff >= 0 ? '+' : '') + '$' + diff.toFixed(2)) : 'Baseline';
        var diffCls = diff > 0 ? 'color:var(--success);font-weight:600;' : (diff < 0 ? 'color:var(--danger);font-weight:600;' : 'color:var(--text-tertiary);');
        return '<tr><td>' + esc(when(m.created_at)) + '</td><td><strong style="font-family:var(--mono)">$' + Number(m.value).toFixed(2) + '</strong></td><td style="' + diffCls + '">' + diffTxt + '</td></tr>';
      }).join('');
      html += '</tbody></table></div>';

      pane.innerHTML = html;
    } catch (e) {
      pane.innerHTML = '<p class="alert-banner error">Failed to load metrics: ' + esc(e.message) + '</p>';
    }
  }

  $('#compare').addEventListener('click', compare);

  // ==========================================================================
  // Visual Element Picker Modal (Robust Esc / Cancel Handling)
  // ==========================================================================
  var dlg = $('#picker-dialog'), frame = $('#picker-frame'), pickMode = 'watch';
  var pickerLoader = $('#picker-loader'), pickerLoaderText = $('#picker-loader-text');
  var pickerLoadTimer = null;

  function resetPicker() {
    clearTimeout(pickerLoadTimer);
    frame.src = 'about:blank';
    if (pickerLoader) {
      pickerLoader.classList.remove('hidden');
      pickerLoader.hidden = false;
      pickerLoader.style.display = 'flex';
    }
    $('#picker-note').textContent = 'Loading rendered DOM...';
  }

  dlg.addEventListener('cancel', resetPicker);
  dlg.addEventListener('close', resetPicker);

  $('#open-picker').addEventListener('click', function () {
    var urlInput = $('#form-url');
    var url = urlInput ? urlInput.value.trim() : '';
    if (!url) {
      showFieldError('form-url', 'Please enter a target URL first (e.g. http://localhost:8001/).');
      if (urlInput) urlInput.focus();
      showToast('⚠️ Please enter a URL first');
      return;
    }
    if (pickerLoader) {
      pickerLoader.classList.remove('hidden');
      pickerLoader.hidden = false;
      pickerLoader.style.display = 'flex';
      if (pickerLoaderText) pickerLoaderText.textContent = 'Rendering sandboxed snapshot...';
    }
    $('#picker-note').textContent = 'Loading rendered DOM...';
    dlg.showModal();
    frame.src = '/api/picker?url=' + encodeURIComponent(url);

    clearTimeout(pickerLoadTimer);
    pickerLoadTimer = setTimeout(function () {
      if (pickerLoader && !pickerLoader.classList.contains('hidden') && pickerLoaderText) {
        pickerLoaderText.textContent = 'Still rendering... Complex pages may take a few seconds.';
      }
    }, 7000);
  });

  frame.addEventListener('load', function () {
    if (!frame.src || frame.src === 'about:blank') return;
    clearTimeout(pickerLoadTimer);
    if (pickerLoader) {
      pickerLoader.classList.add('hidden');
      pickerLoader.hidden = true;
      pickerLoader.style.display = 'none';
    }
    $('#picker-note').textContent = 'Hover and click any element to grab its selector.';
    try {
      frame.contentWindow.postMessage({ type: 'mode', mode: pickMode }, '*');
    } catch (e) {}
  });

  $('.segmented-picker-mode').addEventListener('click', function (e) {
    var btn = e.target.closest('.picker-mode-btn');
    if (!btn) return;
    var m = btn.dataset.mode;
    if (!m) return;
    pickMode = m;
    $$('.picker-mode-btn').forEach(function (b) {
      var isMode = b === btn;
      b.classList.toggle('on', isMode);
      b.setAttribute('aria-pressed', isMode ? 'true' : 'false');
    });
    try {
      frame.contentWindow.postMessage({ type: 'mode', mode: m }, '*');
    } catch (e) {}
  });

  $('#picker-close').addEventListener('click', function () {
    dlg.close();
    resetPicker();
  });

  window.addEventListener('message', function (e) {
    if (e.source !== frame.contentWindow || !e.data || e.data.type !== 'pick') return;
    var box = $('#add-form')[e.data.mode === 'ignore' ? 'ignore' : 'watch'];
    box.value = (box.value ? box.value.trimEnd() + '\n' : '') + e.data.selector;
    $('#picker-note').textContent = '✓ Added: ' + e.data.selector;
    showToast('✓ Captured ' + (e.data.mode === 'ignore' ? 'Ignore' : 'Watch') + ': ' + (e.data.sample || e.data.selector).slice(0, 30));
  });

  // ==========================================================================
  // Deep Link Routing (#diff/<watch>/<a>/<b>)
  // ==========================================================================
  function route() {
    var m = location.hash.match(/^#diff\/(\d+)\/(\d+)\/(\d+)$/);
    if (m) {
      select(+m[1], m[2], m[3]);
      switchView('inspector');
    }
  }
  window.addEventListener('hashchange', route);

  // ==========================================================================
  // Tab Visibility & Polling Lifecycle
  // ==========================================================================
  function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(function () {
      if (!document.hidden) {
        refresh().catch(function () {});
      }
    }, 5000);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) {
      stopPolling();
    } else {
      refresh().catch(function () {});
      startPolling();
    }
  });

  // Initialize
  initTheme();
  refresh().then(route);
  startPolling();
})();
