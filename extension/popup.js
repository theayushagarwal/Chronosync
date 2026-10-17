document.addEventListener('DOMContentLoaded', async () => {
  const urlInput = document.getElementById('url');
  const nameInput = document.getElementById('name');
  const intervalSelect = document.getElementById('interval');
  const sensitivitySelect = document.getElementById('sensitivity');
  const selectorInput = document.getElementById('selector');
  const keywordsInput = document.getElementById('keywords');
  const pickBtn = document.getElementById('pick-btn');
  const form = document.getElementById('watch-form');
  const statusDiv = document.getElementById('status');
  const submitBtn = document.getElementById('submit-btn');

  const API_BASE = 'http://localhost:8000';

  // Get active tab info
  let activeTab;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    activeTab = tabs[0];
    if (activeTab && activeTab.url) {
      urlInput.value = activeTab.url;
      nameInput.value = activeTab.title ? activeTab.title.slice(0, 80) : 'Monitored Page';
    }
  } catch (err) {
    statusDiv.className = 'msg error';
    statusDiv.textContent = 'Could not access active tab.';
  }

  // Interactive in-page picker
  pickBtn.addEventListener('click', async () => {
    if (!activeTab || !activeTab.id) return;
    statusDiv.className = 'msg';
    statusDiv.textContent = 'Click an element on the webpage...';

    try {
      await chrome.scripting.executeScript({
        target: { tabId: activeTab.id },
        files: ['picker.js']
      });
      window.close(); // Close popup so user can click the page
    } catch (e) {
      statusDiv.className = 'msg error';
      statusDiv.textContent = 'Cannot run picker on this page.';
    }
  });

  // Listen for message from picker
  chrome.runtime.onMessage.addListener((message) => {
    if (message && message.type === 'ELEMENT_PICKED') {
      selectorInput.value = message.selector;
    }
  });

  // Form submission
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    statusDiv.className = 'msg';
    statusDiv.textContent = 'Submitting to Change Detector...';
    submitBtn.disabled = true;

    const payload = {
      url: urlInput.value.trim(),
      name: nameInput.value.trim() || 'Watch',
      interval_minutes: parseInt(intervalSelect.value, 10),
      sensitivity: sensitivitySelect.value,
      watch_selectors: selectorInput.value.trim() ? [selectorInput.value.trim()] : [],
      ignore_selectors: [],
      keywords: keywordsInput.value.split(',').map(s => s.trim()).filter(Boolean),
      notify_email: null,
      digest: false
    };

    try {
      const res = await fetch(`${API_BASE}/api/watches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        let errText = 'Failed to add watch';
        try {
          const data = await res.json();
          errText = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
        } catch (_) {}
        throw new Error(errText);
      }

      statusDiv.className = 'msg success';
      statusDiv.textContent = '✓ Added! Running first check now.';
      submitBtn.textContent = 'Added ✓';
      setTimeout(() => window.close(), 1800);
    } catch (err) {
      statusDiv.className = 'msg error';
      statusDiv.textContent = err.message || 'Error connecting to local server';
      submitBtn.disabled = false;
    }
  });
});
