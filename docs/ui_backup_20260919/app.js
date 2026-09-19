(() => {
  const API_BASE = 'http://127.0.0.1:5000';
  const tokenPromise = window.nom.getToken();
  let backendOnline = false;
  let currentGame = null;

  // ------------------------------------------------------------------
  // Titlebar controls
  // ------------------------------------------------------------------
  document.getElementById('btn-min').addEventListener('click', () => window.nom.minimize());
  document.getElementById('btn-max').addEventListener('click', () => window.nom.maximize());
  document.getElementById('btn-close').addEventListener('click', () => window.nom.close());

  if (window.nom && window.nom.onBackendStatus) {
    window.nom.onBackendStatus((status) => {
      setBackendPill(status.ready);
    });
  }

  // ------------------------------------------------------------------
  // Navigation
  // ------------------------------------------------------------------
  document.querySelectorAll('.nav-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('view-' + btn.dataset.view).classList.add('active');
      activeView = btn.dataset.view;
      if (activeView === 'system') pollSystem();
      if (activeView === 'mods') loadMods();
      if (activeView === 'backups') { loadBackups(); loadTimeMachine(); }
      if (activeView === 'bench') loadBench();
      if (activeView === 'saves') loadSaves();
    });
  });
  let activeView = 'dashboard';

  // ------------------------------------------------------------------
  // Overlay (separate always-on-top window, see electron/main.js)
  // ------------------------------------------------------------------
  const overlayBtn = document.getElementById('btn-overlay');
  const setOverlayBtn = (on) => overlayBtn.classList.toggle('on', !!on);
  overlayBtn.addEventListener('click', async () => setOverlayBtn(await window.nom.toggleOverlay()));
  window.nom.onOverlayState(setOverlayBtn);

  // ------------------------------------------------------------------
  // Helpers
  // ------------------------------------------------------------------
  function setBackendPill(online) {
    backendOnline = online;
    const pill = document.getElementById('backend-pill');
    pill.classList.remove('online', 'offline');
    pill.classList.add(online ? 'online' : 'offline');
    pill.innerHTML = `<i class="dot"></i> ${online ? 'connected' : 'offline'}`;
    document.getElementById('settings-backend-status').textContent = online ? 'Connected' : 'Disconnected';
  }

  function logActivity(message, level = 'info') {
    const log = document.getElementById('activity-log');
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    // textContent: messages carry game names read from disk/Steam manifests.
    const ts = document.createElement('span');
    ts.className = 'ts';
    ts.textContent = new Date().toLocaleTimeString();
    const text = document.createElement('span');
    text.textContent = message;
    entry.append(ts, text);
    log.appendChild(entry);
    while (log.children.length > 60) log.removeChild(log.firstChild);
  }

  function setGauge(id, pct, mode) {
    const el = document.getElementById(id);
    el.style.setProperty('--pct', Math.max(0, Math.min(100, pct)));
    el.classList.remove('warn', 'bad', 'good');
    if (mode) el.classList.add(mode);
  }

  async function apiGet(path) {
    const res = await fetch(API_BASE + path, { headers: { 'X-Nexus-Token': await tokenPromise } });
    if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
    return res.json();
  }

  async function apiPost(path, body) {
    const res = await fetch(API_BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Nexus-Token': await tokenPromise },
      body: JSON.stringify(body || {})
    });
    if (!res.ok) throw new Error(`POST ${path} -> ${res.status}`);
    return res.json();
  }

  // ------------------------------------------------------------------
  // Telemetry polling
  // ------------------------------------------------------------------
  async function pollTelemetry() {
    try {
      const data = await apiGet('/telemetry');
      if (!backendOnline) { setBackendPill(true); logActivity('Backend connected.'); }
      renderTelemetry(data);
    } catch (err) {
      if (backendOnline) { setBackendPill(false); logActivity('Lost connection to backend.', 'error'); }
    }
  }

  function renderTelemetry(d) {
    document.getElementById('val-vram').textContent = d.vram_percent.toFixed(0);
    setGauge('gauge-vram', d.vram_percent, d.vram_percent > 85 ? 'bad' : d.vram_percent > 65 ? 'warn' : 'good');
    document.getElementById('val-vram-mb').textContent = typeof d.vram_used_mb === 'number'
      ? `${(d.vram_used_mb / 1024).toFixed(1)} / ${(d.vram_total_mb / 1024).toFixed(1)} GB` : '—';
    document.getElementById('val-gpu-temp-sub').textContent = typeof d.gpu_temp_c === 'number' ? `${d.gpu_temp_c} °C` : '—';
    const vramState = document.getElementById('hero-vram-state');
    vramState.textContent = d.vram_percent >= 92 ? 'VRAM critical' : d.vram_percent >= 85 ? 'VRAM high' : 'VRAM OK';
    vramState.className = 'badge ' + (d.vram_percent >= 92 ? 'bad' : d.vram_percent >= 85 ? 'warn' : 'good');
    const running = document.getElementById('hero-running');
    running.textContent = d.game_running ? 'Running' : 'Not running';
    running.className = d.game_running ? 'badge good' : 'badge';
    pushHistory(d);

    // FPS comes from PresentMon (ETW); without it show a dash + the reason, never a made-up number.
    const hasFps = typeof d.fps === 'number';
    document.getElementById('val-fps').textContent = hasFps ? d.fps.toFixed(0) : '—';
    document.getElementById('gauge-fps').title = hasFps ? 'PresentMon (ETW)' : (d.fps_status || '');
    setGauge('gauge-fps', hasFps ? (d.fps / 180) * 100 : 0, null);

    const hasUtil = typeof d.gpu_util === 'number';
    document.getElementById('val-gpu-util').textContent = hasUtil ? d.gpu_util.toFixed(0) : '—';
    setGauge('gauge-gpu-util', hasUtil ? d.gpu_util : 0, hasUtil ? 'good' : null);

    // latency_ms is null until the first probe completes, or when offline.
    const hasLatency = typeof d.latency_ms === 'number';
    document.getElementById('val-latency').textContent = hasLatency ? d.latency_ms.toFixed(0) : '—';
    setGauge('gauge-latency', hasLatency ? Math.min(100, d.latency_ms) : 0,
      !hasLatency ? null : d.latency_ms > 80 ? 'bad' : d.latency_ms > 40 ? 'warn' : 'good');

    document.getElementById('stat-gpu-name').textContent = d.gpu_name;
    document.getElementById('stat-gpu-temp').textContent = typeof d.gpu_temp_c === 'number' ? `${d.gpu_temp_c.toFixed(0)} °C` : '—';
    document.getElementById('stat-cpu').textContent = `${d.cpu_percent.toFixed(0)}%`;

    const statusEl = document.getElementById('stat-status');
    statusEl.textContent = d.status_message;
    statusEl.className = d.vram_percent >= 92 ? 'status-bad' : d.vram_percent >= 85 ? 'status-warn' : 'status-ok';

    document.getElementById('settings-game-running').textContent = d.game_running ? 'Yes' : 'No';

    // A rescan ran elsewhere.
    if (gamesLoaded && d.games_version !== undefined && d.games_version !== gamesVersion) loadGames();

    if (d.last_auto_action) {
      logActivity(d.last_auto_action);
    }
  }

  // ------------------------------------------------------------------
  // Game selection
  // ------------------------------------------------------------------
  const GAME_STORAGE_KEY = 'nom.selectedGame';
  const gameSelect = document.getElementById('game-select');
  const rescanBtn = document.getElementById('btn-rescan');
  const STORE_LABELS = { steam: 'Steam', epic: 'Epic Games' };
  let gamesLoaded = false;
  let gamesLoading = false;
  let gamesVersion = null;
  let launchRunning = false;

  function profileLabel(game) {
    return game.profile === 'unreal'
      ? 'Unreal: High CPU priority + manual Frame Generation cap'
      : 'Generic: High CPU priority';
  }

  function renderGame(game) {
    const changed = !currentGame || currentGame.id !== game.id;
    currentGame = game;
    if (gamesLoaded) gameSelect.value = game.id;
    document.getElementById('dash-game-name').textContent = game.name;
    document.getElementById('mods-game-name').textContent = game.name;
    document.getElementById('bench-game-name').textContent = game.name;
    document.getElementById('saves-game-name').textContent = game.name;
    document.getElementById('hero-engine').textContent = game.engine;
    if (changed && activeView === 'mods') loadMods();
    document.getElementById('settings-game-name').textContent = game.name;
    document.getElementById('settings-game-engine').textContent = game.engine;
    document.getElementById('settings-game-profile').textContent = profileLabel(game);
    document.getElementById('settings-game-store').textContent = STORE_LABELS[game.store] || game.store;
    document.getElementById('game-profile').textContent = `${game.engine} · ${game.profile} profile`;
    document.getElementById('settings-install-path').textContent =
      game.installed ? game.install_path : `${game.install_path} (not found)`;
    document.getElementById('settings-config-path').textContent = game.config_path;
    document.getElementById('settings-gus-path').textContent = game.gus_path || 'Not used by this game';

    const how = game.can_launch ? 'Launches' : 'Waits for you to start';
    document.getElementById('launch-opt-desc').textContent = game.has_server
      ? `${how} ${game.name} and isolates the local server to E-Cores to prevent frame drops.`
      : `${how} ${game.name} and runs it at High priority across all cores (no local server to isolate).`;

    const fgBtn = document.querySelector('[data-fix="fg_cap"]');
    fgBtn.disabled = !game.supports_fg_cap;
    fgBtn.title = game.supports_fg_cap ? '' : `${game.name} is not an Unreal Engine game (no GameUserSettings.ini)`;
    return changed;
  }

  async function pollGame() {
    try {
      const game = await apiGet('/game');
      // Another client (or a backend restart) may have changed the target.
      if (game) renderGame(game);
    } catch (err) { /* backend offline, retried by telemetry poll */ }
  }

  function fillGameSelect(res) {
    gameSelect.innerHTML = '';
    // One <optgroup> per discovery source, in STORE_LABELS order.
    Object.keys(STORE_LABELS).forEach((store) => {
      const list = res.games.filter((g) => g.store === store);
      if (!list.length) return;
      const group = document.createElement('optgroup');
      group.label = `${STORE_LABELS[store]} (${list.length})`;
      list.forEach((g) => {
        const opt = document.createElement('option');
        opt.value = g.id;
        opt.textContent = g.profile === 'unreal' ? `${g.name}  · UE` : g.name;
        group.appendChild(opt);
      });
      gameSelect.appendChild(group);
    });
    gamesVersion = res.version;
    gamesLoaded = true;
    gameSelect.value = res.active;
    gameSelect.disabled = launchRunning;
  }

  async function loadGames(restoreSaved) {
    if (gamesLoading) return;
    gamesLoading = true;
    try {
      const res = await apiGet('/games');
      const known = new Set(Array.from(gameSelect.options, (o) => o.value));
      const added = gamesLoaded ? res.games.filter((g) => !known.has(g.id)) : [];
      fillGameSelect(res);
      added.forEach((g) => logActivity(`Auto-Discovery: found ${g.name} (${g.engine}).`));

      // Restore the user's last choice after a backend restart.
      const saved = localStorage.getItem(GAME_STORAGE_KEY);
      if (restoreSaved && saved && saved !== res.active && res.games.some((g) => g.id === saved)) {
        await selectGame(saved, true);
      }
    } catch (err) {
      setTimeout(() => loadGames(restoreSaved), 3000); // backend still starting
    } finally {
      gamesLoading = false;
    }
  }

  rescanBtn.addEventListener('click', async () => {
    rescanBtn.disabled = true;
    try {
      const res = await apiPost('/games/rescan');
      fillGameSelect(res);
      logActivity(res.message);
    } catch (err) {
      logActivity('Rescan failed — is the backend running?', 'error');
    } finally {
      rescanBtn.disabled = false;
    }
  });

  async function selectGame(id, quiet) {
    const previous = currentGame ? currentGame.id : null;
    gameSelect.disabled = true;
    try {
      const res = await apiPost('/game', { id });
      if (res.status !== 'success') throw new Error(res.message);
      localStorage.setItem(GAME_STORAGE_KEY, id);
      renderGame(res.game);
      document.getElementById('optimizer-msg').textContent = '';
      if (!quiet) logActivity(res.message);
    } catch (err) {
      if (previous) gameSelect.value = previous;
      logActivity(`Could not switch game: ${err.message}`, 'error');
    } finally {
      gameSelect.disabled = launchRunning;
    }
  }

  gameSelect.addEventListener('change', () => selectGame(gameSelect.value));

  // ------------------------------------------------------------------
  // DLSS5 Server Optimizer (dashboard)
  // ------------------------------------------------------------------
  const launchBtn = document.getElementById('btn-launch-opt');
  const launchMsg = document.getElementById('launch-opt-msg');
  let launchPoll = null;
  let launchLogSeen = 0;

  function setLaunchMsg(text, isError) {
    launchMsg.textContent = text;
    launchMsg.className = isError ? 'inline-message error' : 'inline-message';
  }

  async function pollLaunchStatus() {
    try {
      const s = await apiGet('/launch_optimized/status');
      // Mirror new backend log lines into the activity log.
      if (s.log.length < launchLogSeen) launchLogSeen = 0;
      s.log.slice(launchLogSeen).forEach((line) => logActivity(line.replace(/^\S+\s+/, '')));
      launchLogSeen = s.log.length;

      if (s.phase !== 'idle') setLaunchMsg(s.message, s.phase === 'error');
      launchBtn.disabled = s.running;
      // Lock the game selector while a launch sequence owns a game.
      launchRunning = s.running;
      if (gamesLoaded) gameSelect.disabled = s.running;
      if (s.running && !launchPoll) launchPoll = setInterval(pollLaunchStatus, 2000);
      launchBtn.textContent = s.running ? 'Optimizing…' : 'Launch Server Optimized';
      if (!s.running && launchPoll) { clearInterval(launchPoll); launchPoll = null; }
    } catch (err) { /* backend offline, retried on the next tick */ }
  }

  function startLaunchPolling() {
    if (!launchPoll) launchPoll = setInterval(pollLaunchStatus, 2000);
    pollLaunchStatus();
  }

  launchBtn.addEventListener('click', async () => {
    launchBtn.disabled = true;
    setLaunchMsg('Starting optimized launch…');
    launchLogSeen = 0;
    try {
      const res = await apiPost('/launch_optimized');
      setLaunchMsg(res.message, res.status !== 'success');
      if (res.status === 'success') startLaunchPolling();
      else { logActivity(res.message, 'error'); launchBtn.disabled = false; }
    } catch (err) {
      setLaunchMsg('Launch failed — is the backend running?', true);
      launchBtn.disabled = false;
    }
  });

  // ------------------------------------------------------------------
  // Optimizer view
  // ------------------------------------------------------------------
  document.getElementById('toggle-auto-optimize').addEventListener('change', async (e) => {
    try {
      await apiPost('/autopilot', { enabled: e.target.checked });
      logActivity(`Autopilot ${e.target.checked ? 'enabled' : 'disabled'}.`);
    } catch (err) {
      e.target.checked = !e.target.checked;
    }
  });

  document.getElementById('toggle-power-plan').addEventListener('change', async (e) => {
    const msg = document.getElementById('optimizer-msg');
    try {
      const res = await apiPost('/optimize/power', { high_performance: e.target.checked });
      msg.textContent = res.message;
      msg.className = res.status === 'success' ? 'inline-message' : 'inline-message error';
      logActivity(res.message);
    } catch (err) {
      e.target.checked = !e.target.checked;
      msg.textContent = 'Could not change power plan.';
      msg.className = 'inline-message error';
    }
  });

  document.querySelectorAll('[data-fix]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const msg = document.getElementById('optimizer-msg');
      msg.textContent = 'Applying…';
      msg.className = 'inline-message';
      try {
        const res = await apiPost('/optimize/fix', { fix: btn.dataset.fix });
        msg.textContent = res.message;
        msg.className = res.status === 'success' ? 'inline-message' : 'inline-message error';
        logActivity(res.message);
      } catch (err) {
        msg.textContent = 'Fix failed — is the backend running?';
        msg.className = 'inline-message error';
      }
    });
  });

  // ------------------------------------------------------------------
  // Shared DOM helpers (textContent only: names and paths come from disk)
  // ------------------------------------------------------------------
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function row(cells, action) {
    const tr = document.createElement('tr');
    cells.forEach(([cls, text]) => tr.appendChild(el('td', cls, text)));
    const td = el('td');
    if (action) td.appendChild(action);
    tr.appendChild(td);
    return tr;
  }

  function emptyRow(tbody, cols, text) {
    const td = el('td', 'empty', text);
    td.colSpan = cols;
    tbody.replaceChildren(el('tr'));
    tbody.firstChild.appendChild(td);
  }

  function setMsg(id, res) {
    const m = document.getElementById(id);
    m.textContent = res.message;
    m.className = res.status === 'success' ? 'inline-message' : 'inline-message error';
  }

  const setBar = (id, pct) => {
    const bar = document.getElementById(id);
    bar.firstElementChild.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    bar.className = 'bar' + (pct >= 92 ? ' bad' : pct >= 85 ? ' warn' : '');
  };

  // ------------------------------------------------------------------
  // Dashboard sparkline: last 80 samples (~2 min at 1.5 s)
  // ------------------------------------------------------------------
  const HISTORY = 80;
  const history = { vram: [], gpu: [] };

  function sparkPath(values, closed) {
    const step = 100 / (HISTORY - 1);
    const pts = values.map((v, i) => `${(100 - (values.length - 1 - i) * step).toFixed(2)},${(100 - Math.max(0, Math.min(100, v))).toFixed(2)}`);
    if (!pts.length) return '';
    const line = 'M' + pts.join(' L');
    return closed ? `${line} L100,100 L${pts[0].split(',')[0]},100 Z` : line;
  }

  function pushHistory(d) {
    history.vram.push(d.vram_percent);
    if (typeof d.gpu_util === 'number') history.gpu.push(d.gpu_util);
    Object.values(history).forEach((a) => { while (a.length > HISTORY) a.shift(); });
    document.getElementById('spark-vram').setAttribute('d', sparkPath(history.vram, true));
    document.getElementById('spark-gpu').setAttribute('d', sparkPath(history.gpu, false));
  }

  // ------------------------------------------------------------------
  // System Resources
  // ------------------------------------------------------------------
  async function pollSystem() {
    if (activeView !== 'system') return;
    try {
      renderSystem(await apiGet('/system/resources'));
    } catch (err) { /* backend offline, retried on the next tick */ }
  }

  function renderSystem(s) {
    document.getElementById('sys-ram-pct').textContent = `${s.ram_percent.toFixed(0)}%`;
    setBar('sys-ram-bar', s.ram_percent);
    document.getElementById('sys-ram-text').textContent =
      `${(s.ram_used_mb / 1024).toFixed(1)} GB used of ${(s.ram_total_mb / 1024).toFixed(1)} GB`;

    const hasGpu = typeof s.vram_total_mb === 'number';
    document.getElementById('sys-vram-pct').textContent = hasGpu ? `${s.vram_percent.toFixed(0)}%` : '—';
    setBar('sys-vram-bar', hasGpu ? s.vram_percent : 0);
    document.getElementById('sys-vram-text').textContent = hasGpu
      ? `${(s.vram_used_mb / 1024).toFixed(1)} GB used of ${(s.vram_total_mb / 1024).toFixed(1)} GB` : s.gpu_name;
    document.getElementById('sys-gpu-util').textContent = hasGpu ? `${s.gpu_util}%` : '—';
    setBar('sys-gpu-bar', hasGpu ? s.gpu_util : 0);
    document.getElementById('sys-gpu-text').textContent =
      hasGpu ? `${s.gpu_name} · ${s.gpu_temp_c} °C` : s.gpu_name;

    const grid = document.getElementById('sys-cores');
    if (grid.children.length !== s.cpu_per_core.length) {
      grid.replaceChildren(...s.cpu_per_core.map((_, i) => {
        const type = s.core_types[i];
        const core = el('div', type === 'E' ? 'core e' : 'core');
        const label = el('span');
        label.append(el('span', '', `CPU ${i}`), el('span', '', type));
        core.append(el('div', 'fill'), label, el('b'));
        return core;
      }));
      const p = s.core_types.filter((t) => t === 'P').length;
      const e = s.core_types.filter((t) => t === 'E').length;
      document.getElementById('sys-core-legend').textContent = p && e ? `${p} P-core threads · ${e} E-core threads` : '';
    }
    s.cpu_per_core.forEach((pct, i) => {
      const core = grid.children[i];
      core.querySelector('.fill').style.height = `${pct}%`;
      core.querySelector('b').textContent = `${pct.toFixed(0)}%`;
    });

    const procs = document.getElementById('sys-procs');
    procs.replaceChildren(...s.top_processes.map((p) =>
      row([['hi', p.name], ['num', String(p.pid)], ['num', `${p.ram_mb.toLocaleString()} MB`]])));
    procs.querySelectorAll('tr').forEach((tr) => tr.lastChild.remove()); // no action column
  }

  // ------------------------------------------------------------------
  // Live Mods (file-based only)
  // ------------------------------------------------------------------
  const KIND_LABELS = { pak: 'Pak mod', reshade: 'ReShade preset', config: 'Config swap' };
  let modsInfo = null;
  let configTarget = null;

  async function loadMods() {
    loadArchiveMods();
    loadNexus();
    try {
      modsInfo = await apiGet('/mods/live');
    } catch (err) { return; }
    const info = modsInfo;
    const locked = !!info.anticheat;
    document.getElementById('mods-anticheat').hidden = !locked;
    document.getElementById('mods-anticheat-text').textContent = locked
      ? `Locked: this game ships ${info.anticheat}. Pak mods and ReShade are blocked to avoid anti-cheat bans. Config swaps are still allowed.` : '';
    document.getElementById('mods-pak-dir').textContent = info.pak_dir || 'Not an Unreal game with a Content/Paks folder';
    document.getElementById('mods-reshade-state').textContent = info.has_reshade ? 'ReShade detected' : 'ReShade not installed for this game';
    document.querySelector('[data-mod-kind="pak"]').disabled = locked || !info.pak_dir;
    document.querySelector('[data-mod-kind="reshade"]').disabled = locked || !info.has_reshade;
    if (configTarget && info.game_id !== configTarget.game) configTarget = null;
    document.getElementById('mods-config-target').textContent = configTarget ? configTarget.path : 'No target chosen';
    document.querySelector('[data-mod-kind="config"]').disabled = !configTarget;

    const list = document.getElementById('mods-list');
    if (!info.mods.length) return emptyRow(list, 5, 'No mods applied to this game yet.');
    list.replaceChildren(...info.mods.slice().reverse().map((m) => {
      const btn = el('button', 'btn-ghost danger', 'Remove');
      btn.addEventListener('click', () => removeMod(m, btn));
      return row([['hi', m.name], ['', KIND_LABELS[m.kind]], ['num', String(m.files.length)],
                  ['', new Date(m.applied * 1000).toLocaleString()]], btn);
    }));
  }

  async function removeMod(mod, btn) {
    btn.disabled = true;
    try {
      const res = await apiPost('/mods/live/remove', { id: mod.id });
      setMsg('mods-msg', res);
      logActivity(res.message);
    } catch (err) {
      setMsg('mods-msg', { status: 'error', message: 'Remove failed. Is the backend running?' });
    }
    loadMods();
  }

  document.getElementById('btn-config-target').addEventListener('click', async () => {
    if (!modsInfo || !modsInfo.game_id) return;
    const [path] = await window.nom.pickFiles('config', modsInfo.config_dir);
    if (path) configTarget = { game: modsInfo.game_id, path };
    loadMods();
  });

  document.querySelectorAll('[data-mod-kind]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const kind = btn.dataset.modKind;
      const paths = await window.nom.pickFiles(kind);
      if (!paths.length) return;
      btn.disabled = true;
      try {
        const res = await apiPost('/mods/live/apply', { kind, paths, target: kind === 'config' ? configTarget.path : null });
        setMsg('mods-msg', res);
        logActivity(res.message);
        if (res.status === 'success' && kind === 'config') configTarget = null;
      } catch (err) {
        setMsg('mods-msg', { status: 'error', message: 'Apply failed. Is the backend running?' });
      }
      loadMods();
    });
  });

  // ------------------------------------------------------------------
  // Archive mods (ON/OFF toggles) + Nexus Mods browser
  // ------------------------------------------------------------------
  // Like apiGet/apiPost, but 400 bodies ({status, message}) come back for display.
  async function api(path, body) {
    const opts = { headers: { 'X-Nexus-Token': await tokenPromise } };
    if (body !== undefined) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(API_BASE + path, opts);
    if (!res.ok && res.status !== 400) throw new Error(`${path} -> ${res.status}`);
    return res.json();
  }
  const fail = (id) => setMsg(id, { status: 'error', message: 'Request failed. Is the backend running?' });
  const report = (id, res) => { setMsg(id, res); logActivity(res.message, res.status === 'success' ? 'info' : 'error'); };
  const plain = (html) => new DOMParser().parseFromString(html || '', 'text/html').body.textContent;

  // "3 → ~mods · 1 → Windows": where each mod's files were routed.
  function routeSummary(files) {
    const byDir = {};
    files.forEach((f) => { const dir = f.target.split(/[\\/]/).slice(-2, -1)[0]; byDir[dir] = (byDir[dir] || 0) + 1; });
    return Object.entries(byDir).map(([d, n]) => `${n} → ${d}`).join(' · ');
  }

  function modSwitch(checked, onChange) {
    const label = el('label', 'switch');
    const input = el('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.addEventListener('change', () => onChange(input));
    label.append(input, el('span', 'switch-slider'));
    return label;
  }

  async function loadArchiveMods() {
    let res;
    try { res = await api('/mods/archive'); } catch (err) { return; }
    archiveMods = res.mods;
    loadBisect();
    const list = document.getElementById('archive-list');
    if (!res.mods.length) {
      list.replaceChildren(el('div', 'empty', res.game_id ? 'No mods installed yet. Install an archive, or grab one from Nexus below.' : 'Select a game first.'));
      return;
    }
    list.replaceChildren(...res.mods.slice().reverse().map((m) => {
      const row = el('div', 'mod-row' + (m.enabled ? ' on' : ''));
      const info = el('div', 'mod-info');
      const meta = [routeSummary(m.files)];
      if (m.source && m.source.nexus) meta.push(`Nexus #${m.source.mod_id}${m.source.version ? ' · v' + m.source.version : ''}`);
      if (m.skipped.length) meta.push(`${m.skipped.length} skipped`);
      const metaEl = el('div', 'mod-meta', meta.join(' · '));
      metaEl.title = m.skipped.map((s) => `${s.file}: ${s.reason}`).join('\n');
      info.append(el('div', 'mod-name', m.name), metaEl);

      const toggle = modSwitch(m.enabled, async (input) => {
        input.disabled = true;
        try { report('archive-msg', await api('/mods/archive/toggle', { id: m.id, enabled: input.checked })); } catch (err) { fail('archive-msg'); }
        loadArchiveMods();
      });
      const del = el('button', 'btn-ghost danger', 'Uninstall');
      del.addEventListener('click', async () => {
        if (!confirm(`Uninstall ${m.name}? Its files are removed from the game and originals restored.`)) return;
        del.disabled = true;
        try { report('archive-msg', await api('/mods/archive/uninstall', { id: m.id })); } catch (err) { fail('archive-msg'); }
        loadArchiveMods();
      });
      row.append(info, toggle, del);
      return row;
    }));
  }

  const installArchiveBtn = document.getElementById('btn-install-archive');
  installArchiveBtn.addEventListener('click', async () => {
    const [path] = await window.nom.pickFiles('archive');
    if (!path) return;
    installArchiveBtn.disabled = true;
    setMsg('archive-msg', { status: 'success', message: 'Unpacking and routing…' });
    try { report('archive-msg', await api('/mods/archive/install', { path })); } catch (err) { fail('archive-msg'); }
    installArchiveBtn.disabled = false;
    loadArchiveMods();
  });

  // ---- Mod Bisect: each step the server switches the half under test ON, the other suspects OFF
  let archiveMods = [];
  const bisectBtns = ['start', 'works', 'broken', 'abort'].map((k) => document.getElementById('btn-bisect-' + k));

  async function loadBisect() {
    let s;
    try { s = await api('/mods/bisect'); } catch (err) { return; }
    const on = !!s.active;
    bisectBtns.forEach((b, i) => { b.hidden = i === 0 ? on : !on; });
    const name = (id) => (archiveMods.find((m) => m.id === id) || {}).name || id;
    document.getElementById('bisect-badge').textContent = on ? `Step ${s.step} · ${s.bad_mods.length} suspects left` : 'Find the broken mod';
    document.getElementById('bisect-state').textContent = on ? `ON for this test: ${s.testing_mods.map(name).join(', ')}` : '';
  }

  async function bisectStep(path, body) {
    bisectBtns.forEach((b) => { b.disabled = true; });
    try { report('bisect-msg', await api(path, body)); } catch (err) { fail('bisect-msg'); }
    bisectBtns.forEach((b) => { b.disabled = false; });
    loadArchiveMods();
  }

  bisectBtns[0].addEventListener('click', () => {
    const ids = archiveMods.filter((m) => m.enabled).map((m) => m.id);
    if (ids.length < 2) return setMsg('bisect-msg', { status: 'error', message: 'Switch at least 2 mods ON first - Bisect searches the mods that are ON.' });
    bisectStep('/mods/bisect/start', { ids });
  });
  bisectBtns[1].addEventListener('click', () => bisectStep('/mods/bisect/report', { works: true }));
  bisectBtns[2].addEventListener('click', () => bisectStep('/mods/bisect/report', { works: false }));
  bisectBtns[3].addEventListener('click', () => bisectStep('/mods/bisect/abort', {}));

  // ---- Nexus
  const domainInput = document.getElementById('nexus-domain');
  const nxmToggle = document.getElementById('toggle-nxm');
  let nexusFeed = 'trending';
  let autoDomain = null;   // domain last filled in from the selected game; user edits win until the game changes

  function renderRate(l) {
    document.getElementById('nexus-rate').textContent =
      l && l.hourly != null ? `API quota left: ${l.hourly}/hour · ${l.daily}/day` : '';
  }

  async function loadNexus() {
    let st;
    try { st = await api('/nexus/status'); } catch (err) { return; }
    if (st.domain && st.domain !== autoDomain) { domainInput.value = st.domain; autoDomain = st.domain; }
    document.getElementById('nexus-user').textContent =
      st.user ? `${st.user.name}${st.user.is_premium ? ' · Premium' : ''}` : st.has_key ? 'Connected' : 'Not connected';
    document.getElementById('nexus-key').hidden = st.has_key;
    document.getElementById('btn-nexus-key').hidden = st.has_key;
    document.getElementById('btn-nexus-logout').hidden = !st.has_key;
    nxmToggle.checked = await window.nom.nxmStatus();
    renderRate(st.limits);
    if (st.has_key) browseNexus();
    else document.getElementById('nexus-grid').replaceChildren(el('div', 'empty', 'Connect your Nexus account to browse mods for this game.'));
  }

  async function browseNexus() {
    const grid = document.getElementById('nexus-grid');
    const domain = domainInput.value.trim();
    if (!domain) return;
    grid.replaceChildren(el('div', 'empty', 'Loading…'));
    let r;
    try { r = await api(`/nexus/mods?domain=${encodeURIComponent(domain)}&feed=${nexusFeed}`); } catch (err) { grid.replaceChildren(); return fail('nexus-msg'); }
    if (r.status === 'error') { grid.replaceChildren(); return setMsg('nexus-msg', r); }
    setMsg('nexus-msg', { status: 'success', message: '' });
    renderRate(r.limits);
    const mods = r.mods.filter((m) => m.available !== false && !m.contains_adult_content && (!m.status || m.status === 'published'));
    grid.replaceChildren(...(mods.length ? mods.map((m) => nexusCard(m, domain)) : [el('div', 'empty', 'Nothing in this feed.')]));
  }

  function nexusCard(m, domain) {
    const card = el('div', 'nx-card');
    const img = el('img', 'nx-img');
    img.loading = 'lazy';
    img.alt = '';
    if (typeof m.picture_url === 'string' && m.picture_url.startsWith('https://')) img.src = m.picture_url;
    const body = el('div', 'nx-body');
    body.append(
      el('div', 'nx-name', plain(m.name)),
      el('div', 'nx-meta', `${plain(m.author)} · ♥ ${(m.endorsement_count || 0).toLocaleString()} · v${m.version || '?'}`),
      el('div', 'nx-summary', plain(m.summary)));
    const files = el('div', 'nx-files');
    const actions = el('div', 'nx-actions');
    const filesBtn = el('button', 'btn-secondary', 'Files');
    filesBtn.addEventListener('click', () => showFiles(m, domain, files));
    const page = el('button', 'btn-ghost', 'Open page');
    page.addEventListener('click', () => window.nom.openNexus(`https://www.nexusmods.com/${domain}/mods/${m.mod_id}`));
    actions.append(filesBtn, page);
    body.append(actions, files);
    card.append(img, body);
    return card;
  }

  async function showFiles(m, domain, box) {
    if (box.childElementCount) { box.replaceChildren(); return; }
    box.replaceChildren(el('div', 'muted small', 'Loading files…'));
    let r;
    try { r = await api(`/nexus/files?domain=${encodeURIComponent(domain)}&mod_id=${m.mod_id}`); } catch (err) { box.replaceChildren(); return fail('nexus-msg'); }
    if (r.status === 'error') { box.replaceChildren(); return setMsg('nexus-msg', r); }
    const current = r.files.filter((f) => !['OLD_VERSION', 'ARCHIVED', 'DELETED'].includes(f.category_name))
      .sort((a, b) => (b.uploaded_timestamp || 0) - (a.uploaded_timestamp || 0));
    if (!current.length) return box.replaceChildren(el('div', 'muted small', 'No current files.'));
    box.replaceChildren(...current.map((f) => {
      const row = el('div', 'nx-file');
      const label = el('div', 'nx-file-name', plain(f.name));
      label.appendChild(el('small', null, ` v${f.version || '?'} · ${((f.size_in_bytes || (f.size_kb || 0) * 1024) / 1048576).toFixed(1)} MB · ${f.category_name || ''}`));
      const btn = el('button', 'btn-ghost', 'Install');
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        setMsg('nexus-msg', { status: 'success', message: `Downloading ${f.file_name || f.name}…` });
        try {
          const res = await api('/nexus/install', { domain, mod_id: m.mod_id, file_id: f.file_id });
          report('nexus-msg', res);
          // Free accounts: Nexus requires the website's "Mod Manager Download" button.
          if (res.status === 'error' && res.message.includes('Mod Manager Download')) {
            window.nom.openNexus(`https://www.nexusmods.com/${domain}/mods/${m.mod_id}?tab=files&file_id=${f.file_id}`);
          }
        } catch (err) { fail('nexus-msg'); }
        btn.disabled = false;
        loadArchiveMods();
      });
      row.append(label, btn);
      return row;
    }));
  }

  async function installNxm(url) {
    if (!domainInput.value) await loadNexus();
    setMsg('nexus-msg', { status: 'success', message: 'Downloading from Nexus…' });
    try { report('nexus-msg', await api('/nexus/install', { nxm: url, domain: domainInput.value.trim() })); } catch (err) { fail('nexus-msg'); }
    loadArchiveMods();
  }

  window.nom.onNxmLink(async (url) => {
    document.querySelector('.nav-item[data-view="mods"]').click();
    for (let i = 0; i < 60 && !backendOnline; i++) await new Promise((r) => setTimeout(r, 500));
    installNxm(url);
  });

  document.getElementById('btn-nexus-nxm').addEventListener('click', () => {
    const input = document.getElementById('nexus-nxm');
    if (input.value.trim()) installNxm(input.value.trim());
    input.value = '';
  });

  document.getElementById('btn-nexus-key').addEventListener('click', async () => {
    const input = document.getElementById('nexus-key');
    if (!input.value.trim()) return;
    try { setMsg('nexus-msg', await api('/nexus/key', { key: input.value.trim() })); } catch (err) { fail('nexus-msg'); }
    input.value = '';
    loadNexus();
  });

  document.getElementById('btn-nexus-logout').addEventListener('click', async () => {
    try { setMsg('nexus-msg', await api('/nexus/key', { key: '' })); } catch (err) { fail('nexus-msg'); }
    loadNexus();
  });

  document.querySelectorAll('#nexus-feeds button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('#nexus-feeds button').forEach((x) => x.classList.toggle('on', x === b));
    nexusFeed = b.dataset.feed;
    browseNexus();
  }));
  domainInput.addEventListener('change', browseNexus);

  nxmToggle.addEventListener('change', async () => {
    await window.nom.nxmRegister(nxmToggle.checked);
    nxmToggle.checked = await window.nom.nxmStatus();
  });

  // ------------------------------------------------------------------
  // Backup & Rollback
  // ------------------------------------------------------------------
  async function loadBackups() {
    const list = document.getElementById('backups-list');
    let res;
    try {
      res = await apiGet('/backups');
    } catch (err) { return emptyRow(list, 4, 'Backend offline.'); }
    if (!res.backups.length) return emptyRow(list, 4, 'Nothing backed up yet. Backups appear here the first time Nexus changes a file.');
    list.replaceChildren(...res.backups.map((b) => {
      const btn = el('button', 'btn-ghost', 'Restore');
      btn.addEventListener('click', () => restoreBackup(b, btn));
      return row([['', new Date(b.created * 1000).toLocaleString()], ['path', b.original],
                  ['num', `${Math.max(1, Math.round(b.size / 1024))} KB`]], btn);
    }));
  }

  async function restoreBackup(b, btn) {
    if (!confirm(`Restore this version of\n${b.original}?\n\nThe current file is backed up first.`)) return;
    btn.disabled = true;
    try {
      const res = await apiPost('/backups/restore', { id: b.id });
      setMsg('backups-msg', res);
      logActivity(res.message);
    } catch (err) {
      setMsg('backups-msg', { status: 'error', message: 'Restore failed. Is the backend running?' });
    }
    loadBackups();
  }

  document.getElementById('btn-backups-refresh').addEventListener('click', () => { loadBackups(); loadTimeMachine(); });

  // ------------------------------------------------------------------
  // Config Time Machine: lock / pin / restore the game's .ini files
  // ------------------------------------------------------------------
  async function loadTimeMachine() {
    const list = document.getElementById('tm-list');
    const dir = document.getElementById('tm-dir');
    let res;
    try { res = await api('/config/ini'); } catch (err) { return emptyRow(list, 4, 'Backend offline.'); }
    dir.textContent = res.dir ? res.dir.split(/[\\/]/).slice(-3).join('\\') : 'No config folder';
    dir.title = res.dir || '';
    if (!res.files.length) {
      return emptyRow(list, 4, res.dir ? 'No .ini files yet - launch the game once to create them.' : 'This game has no known config folder.');
    }
    list.replaceChildren(...res.files.map((f) => {
      const state = [f.locked ? 'Locked' : 'Writable',
                     f.pinned ? (f.reverted ? 'REVERTED since pin' : 'matches pin') : null].filter(Boolean).join(' · ');
      const versions = f.history.length ? `${f.history.length} · last ${new Date(f.history[0].created * 1000).toLocaleString()}` : 'none yet';
      const actions = el('div', 'btn-row');
      actions.style.marginTop = '0';
      const action = (label, fn, disabledWhy) => {
        const b = el('button', 'btn-ghost', label);
        if (disabledWhy) { b.disabled = true; b.title = disabledWhy; }
        b.addEventListener('click', async () => {
          b.disabled = true;
          try { const res = await fn(); if (res) report('tm-msg', res); } catch (err) { fail('tm-msg'); }
          loadTimeMachine();
          loadBackups();
        });
        actions.append(b);
      };
      action(f.locked ? 'Unlock' : 'Lock', () => api('/config/ini/lock', { name: f.name, locked: !f.locked }));
      action(f.pinned ? 'Re-pin' : 'Pin', () => api('/config/ini/pin', { name: f.name }));
      action('Restore previous', () => (confirm(`Restore ${f.name} to the copy from ${new Date(f.history[0].created * 1000).toLocaleString()}?\n\nThe current file is backed up first.`)
        ? api('/backups/restore', { id: f.history[0].id }) : null),
        !f.history.length ? 'No earlier version yet' : f.locked ? 'Unlock first' : '');
      const tr = row([['hi', f.name], [f.reverted ? 'bad' : '', state], ['', versions]], actions);
      tr.title = f.path;
      return tr;
    }));
  }

  // ------------------------------------------------------------------
  // Benchmark Lab (PresentMon frame times -> SQLite runs)
  // ------------------------------------------------------------------
  const benchBtn = document.getElementById('btn-bench-record');
  const fmt = (v, d = 1) => (v === null || v === undefined ? '—' : Number(v).toFixed(d));
  let benchRecording = null;

  async function loadBench() {
    const list = document.getElementById('bench-list');
    let res;
    try { res = await api('/bench'); } catch (err) { return emptyRow(list, 8, 'Backend offline.'); }
    benchRecording = res.recording;
    benchBtn.textContent = benchRecording ? `Stop “${benchRecording}”` : 'Start recording';
    benchBtn.className = benchRecording ? 'btn-secondary' : 'btn-primary';
    ['bench-a', 'bench-b'].forEach((id, i) => {
      const sel = document.getElementById(id);
      const keep = sel.value;
      sel.replaceChildren(...res.runs.map((r) => {
        const o = el('option', '', `${r.label} · ${new Date(r.created * 1000).toLocaleString()}`);
        o.value = r.id;
        return o;
      }));
      if (keep && res.runs.some((r) => String(r.id) === keep)) sel.value = keep;
      else if (res.runs[1 - i]) sel.value = res.runs[1 - i].id;   // default: older run = A, newest = B
    });
    if (!res.runs.length) return emptyRow(list, 8, 'No runs yet. Start the game, then record a run.');
    list.replaceChildren(...res.runs.map((r) => {
      const del = el('button', 'btn-ghost danger', 'Delete');
      del.addEventListener('click', async () => {
        if (!confirm(`Delete run “${r.label}”?`)) return;
        try { report('bench-msg', await api('/bench/delete', { id: r.id })); } catch (err) { fail('bench-msg'); }
        loadBench();
      });
      return row([['', new Date(r.created * 1000).toLocaleString()], ['hi', r.label], ['num', fmt(r.avg_fps)],
                  ['num', fmt(r.low1_fps)], ['num', fmt(r.low01_fps)], ['num', String(r.stutters)],
                  ['num', `${fmt(r.duration_s, 0)} s`]], del);
    }));
  }

  benchBtn.addEventListener('click', async () => {
    benchBtn.disabled = true;
    try {
      report('bench-msg', benchRecording ? await api('/bench/stop', {})
                                         : await api('/bench/start', { label: document.getElementById('bench-label').value }));
    } catch (err) { fail('bench-msg'); }
    benchBtn.disabled = false;
    loadBench();
  });

  document.getElementById('btn-bench-compare').addEventListener('click', async () => {
    const a = document.getElementById('bench-a').value, b = document.getElementById('bench-b').value;
    const out = document.getElementById('bench-compare');
    if (!a || !b) return emptyRow(out, 4, 'Record two runs first.');
    let c;
    try { c = await api(`/bench/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`); } catch (err) { return fail('bench-msg'); }
    if (c.status === 'error') return setMsg('bench-msg', c);
    const names = { avg_fps: 'Avg FPS', low1_fps: '1% low', low01_fps: '0.1% low', stutters: 'Stutters' };
    out.replaceChildren(...Object.entries(names).map(([k, name]) => {
      const d = c.delta[k], pct = c.delta_pct[k];
      const better = k === 'stutters' ? d < 0 : d > 0;
      const tr = row([['', name], ['num', fmt(c.a[k], k === 'stutters' ? 0 : 1)], ['num', fmt(c.b[k], k === 'stutters' ? 0 : 1)]]);
      const cell = tr.lastChild;
      cell.className = `num ${d === 0 ? '' : better ? 'up' : 'down'}`;
      cell.textContent = `${d > 0 ? '+' : ''}${fmt(d, k === 'stutters' ? 0 : 1)}${pct === null ? '' : ` (${pct > 0 ? '+' : ''}${fmt(pct)}%)`}`;
      return tr;
    }));
  });

  document.getElementById('btn-bench-refresh').addEventListener('click', loadBench);

  // ------------------------------------------------------------------
  // Save-game backups (zipped on game exit)
  // ------------------------------------------------------------------
  async function loadSaves() {
    const list = document.getElementById('saves-list');
    let res;
    try { res = await api('/saves'); } catch (err) { return emptyRow(list, 4, 'Backend offline.'); }
    document.getElementById('saves-dirs').textContent = res.dirs.length ? res.dirs.join('\n')
      : 'No save folder known for this game — choose it below.';
    if (!res.snapshots.length) return emptyRow(list, 4, 'No save backups yet. One is made automatically when the game closes.');
    list.replaceChildren(...res.snapshots.map((s) => {
      const btn = el('button', 'btn-ghost', 'Restore');
      btn.addEventListener('click', async () => {
        if (!confirm(`Restore saves from ${new Date(s.created * 1000).toLocaleString()}?\n\nYour current saves are backed up first.`)) return;
        btn.disabled = true;
        try { report('saves-msg', await api('/saves/restore', { id: s.id })); } catch (err) { fail('saves-msg'); }
        loadSaves();
      });
      return row([['', new Date(s.created * 1000).toLocaleString()], ['num', String(s.files)],
                  ['num', `${Math.max(1, Math.round(s.size / 1024))} KB`]], btn);
    }));
  }

  document.getElementById('btn-saves-backup').addEventListener('click', async () => {
    try { report('saves-msg', await api('/saves/backup', {})); } catch (err) { fail('saves-msg'); }
    loadSaves();
  });
  document.getElementById('btn-saves-folder').addEventListener('click', async () => {
    const [path] = await window.nom.pickFiles('folder');
    if (!path) return;
    try { report('saves-msg', await api('/saves/folder', { path })); } catch (err) { fail('saves-msg'); }
    loadSaves();
  });
  document.getElementById('btn-saves-auto').addEventListener('click', async () => {
    try { report('saves-msg', await api('/saves/folder', { path: null })); } catch (err) { fail('saves-msg'); }
    loadSaves();
  });

  // ------------------------------------------------------------------
  // Boot
  // ------------------------------------------------------------------
  pollTelemetry();
  loadGames(true).then(pollGame);
  pollLaunchStatus(); // resumes progress display if a launch is already running
  setInterval(pollTelemetry, 1500);
  setInterval(pollGame, 10000);
  setInterval(pollSystem, 2000);
})();
