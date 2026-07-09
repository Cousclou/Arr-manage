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

const manualLookupType = document.getElementById('manual-lookup-type');
const manualQueryHint = document.getElementById('manual-query-hint');

const lookupHints = {
  id: 'ID interne de la série ou du film dans Sonarr/Radarr.',
  title: 'Titre exact ou partiel tel qu\'il apparaît dans la bibliothèque.',
  tvdb: 'Identifiant TheTVDB.',
  tmdb: 'Identifiant TMDb.',
};

function updateManualSearchHint() {
  if (!manualLookupType || !manualQueryHint) return;
  manualQueryHint.textContent = lookupHints[manualLookupType.value] || lookupHints.id;
}

manualLookupType?.addEventListener('change', updateManualSearchHint);
updateManualSearchHint();
