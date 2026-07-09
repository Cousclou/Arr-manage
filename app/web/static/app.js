// Settings sidebar navigation
const activeTabInput = document.getElementById('active-tab-input');
const settingsNavItems = document.querySelectorAll('.settings-nav-item');
const settingsPanels = document.querySelectorAll('.settings-panel');

function activateSettingsTab(tabId) {
  settingsNavItems.forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === tabId);
  });
  settingsPanels.forEach((panel) => {
    panel.classList.toggle('active', panel.id === `tab-${tabId}`);
  });
  if (activeTabInput) activeTabInput.value = tabId;
  history.replaceState(null, '', `?tab=${tabId}`);
}

settingsNavItems.forEach((btn) => {
  btn.addEventListener('click', () => activateSettingsTab(btn.dataset.tab));
});

// Restore tab from URL
const urlTab = new URLSearchParams(window.location.search).get('tab');
if (urlTab && document.getElementById(`tab-${urlTab}`)) {
  activateSettingsTab(urlTab);
} else if (settingsNavItems.length) {
  activateSettingsTab(settingsNavItems[0].dataset.tab);
}

// Legacy settings tabs (if any remain)
document.querySelectorAll('.tab-btn').forEach((btn) => {
  btn.addEventListener('click', () => {
    const tabId = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach((b) => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(`tab-${tabId}`)?.classList.add('active');
  });
});

function toggleLogRow(btn, logId) {
  const row = document.getElementById(`row-details-${logId}`);
  const logRow = btn.closest('.log-row');
  if (!row) return;

  const isHidden = row.hidden;
  row.hidden = !isHidden;
  logRow?.classList.toggle('expanded', isHidden);
  btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

document.body.addEventListener('click', (e) => {
  const btn = e.target.closest('[hx-get*="/logs/"]');
  if (!btn) return;
  const logId = btn.getAttribute('onclick')?.match(/\d+/)?.[0];
  if (!logId) return;

  document.querySelectorAll('.log-details-row').forEach((row) => {
    if (row.id !== `row-details-${logId}`) {
      row.hidden = true;
      const otherId = row.id.replace('row-details-', '');
      document.querySelector(`[onclick*="${otherId}"]`)?.closest('.log-row')?.classList.remove('expanded');
    }
  });
});

function activateWantedTab(tabId, pushState = true) {
  document.querySelectorAll('.wanted-tab').forEach((btn) => {
    const active = btn.dataset.wantedTab === tabId;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('.wanted-panel').forEach((panel) => {
    const active = panel.id === `wanted-panel-${tabId}`;
    panel.classList.toggle('active', active);
    panel.hidden = !active;
  });
  if (pushState) {
    const url = new URL(window.location.href);
    url.searchParams.set('tab', tabId);
    history.replaceState(null, '', url);
  }
}

function initWantedTabs() {
  const tabs = document.querySelectorAll('.wanted-tab');
  if (!tabs.length) return;

  tabs.forEach((btn) => {
    btn.addEventListener('click', () => activateWantedTab(btn.dataset.wantedTab));
  });

  const urlTab = new URLSearchParams(window.location.search).get('tab');
  if (urlTab && document.getElementById(`wanted-panel-${urlTab}`)) {
    activateWantedTab(urlTab, false);
  }
}

initWantedTabs();

document.body.addEventListener('htmx:afterSwap', (event) => {
  if (event.detail.target?.id === 'wanted-preview') {
    initWantedTabs();
    initWantedFilters();
  }
  const progressLog = event.detail.target?.querySelector?.('.search-progress-log')
    || document.querySelector('.search-progress-log');
  if (progressLog) {
    progressLog.scrollTop = progressLog.scrollHeight;
  }
});

function initWantedFilters() {
  document.querySelectorAll('.wanted-filter').forEach((box) => {
    const panel = box.closest('.wanted-panel');
    if (!panel) return;

    const rows = panel.querySelectorAll('.wanted-row');
    const emptyRow = panel.querySelector('.wanted-row-none');
    const countEl = panel.querySelector('.wanted-visible-count');
    const typeSelect = box.querySelector('.wanted-filter-type');
    const input = box.querySelector('.wanted-filter-input');
    if (!typeSelect || !input) return;

    const applyFilter = () => {
      const field = typeSelect.value;
      const query = input.value.trim().toLowerCase();
      let visible = 0;

      rows.forEach((row) => {
        const value = (row.dataset[field] || '').toString().toLowerCase();
        const match = !query || value.includes(query);
        row.hidden = !match;
        if (match) visible += 1;
      });

      if (countEl) countEl.textContent = String(visible);
      if (emptyRow) emptyRow.hidden = visible > 0 || rows.length === 0;
    };

    input.addEventListener('input', applyFilter);
    typeSelect.addEventListener('change', applyFilter);
    applyFilter();
  });
}

initWantedFilters();
