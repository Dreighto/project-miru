(function(){
  const form = document.getElementById('job-form');
  const liveEl = document.getElementById('live-jobs');
  const histEl = document.getElementById('hist-jobs');
  const toastEl = document.getElementById('toast');
  const expanded = new Set();
  let toastTimer = null;

  const LIVE_STATES = new Set(['pending','running','cancel_requested']);

  function toast(msg, type){
    type = type || 'info';
    const prefix = type==='ok' ? '\u2713 ' : type==='err' ? '\u2717 ' : '';
    toastEl.textContent = prefix + msg;
    toastEl.className = 'toast t-' + type + ' show';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.className = 'toast'; }, 2500);
  }

  function esc(s){
    return (s||'').replace(/[&<>"']/g, c => ({
      '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
  }

  function fmtTime(iso){
    if(!iso) return '';
    try {
      const d = new Date(iso);
      const diff = (Date.now() - d.getTime()) / 1000;
      if(diff < 10) return 'just now';
      if(diff < 60) return Math.floor(diff) + 's ago';
      if(diff < 3600) return Math.floor(diff/60) + 'm ago';
      if(diff < 86400) return Math.floor(diff/3600) + 'h ago';
      return d.toLocaleDateString();
    } catch(e){ return iso; }
  }

  function renderJob(j, isLive){
    const open = expanded.has(j.id);
    const cancelable = isLive && (j.status === 'pending' || j.status === 'running');
    const runCls = (isLive && j.status === 'running') ? ' job-running' : '';
    const simTag = j.executor_mode === 'simulated'
      ? '<span class="chip-sim">SIM</span>'
      : '';
    return `
      <div class="job${runCls}" data-id="${j.id}" tabindex="0" role="button" aria-expanded="${open}">
        <header>
          <span class="badge b-${j.status}">${j.status}</span>
          <span class="chip">${esc(j.model)}</span>
          <span class="chip">${esc(j.effort)}</span>
          <span class="meta">${fmtTime(j.created_at)}</span>
          ${simTag}
        </header>
        <div class="preview">${esc(j.output_preview || '(no output yet)')}</div>
        <div class="detail" style="display:${open ? 'block' : 'none'}">
          <pre id="full-${j.id}">loading...</pre>
          <div class="actions">
            ${cancelable ? `<button class="ghost" data-cancel="${j.id}">Cancel</button>` : ''}
            <button class="ghost" data-refresh="${j.id}">Refresh</button>
            <a href="/jobs/${j.id}" class="ghost" style="text-decoration:none;display:inline-flex;align-items:center">Detail</a>
          </div>
        </div>
      </div>
    `;
  }

  async function fetchJobs(){
    try {
      const r = await fetch('/api/jobs', {cache:'no-store'});
      if(!r.ok) return;
      const data = await r.json();
      const all = data.jobs || [];
      const live = all.filter(j => LIVE_STATES.has(j.status));
      const hist = all.filter(j => !LIVE_STATES.has(j.status));
      liveEl.innerHTML = live.length
        ? live.map(j => renderJob(j, true)).join('')
        : '<div class="empty"><div class="empty-icon">&#9673;</div>No active jobs</div>';
      histEl.innerHTML = hist.length
        ? hist.map(j => renderJob(j, false)).join('')
        : '<div class="empty"><div class="empty-icon">&#9776;</div>No completed jobs yet</div>';
      const today = new Date().toDateString();
      const todayJobs = all.filter(j => new Date(j.created_at).toDateString() === today);
      const doneToday = todayJobs.filter(j => j.status === 'done').length;
      const runningNow = live.length;
      const statsEl = document.getElementById('stats-bar');
      if(todayJobs.length > 0){
        statsEl.innerHTML = '<span class="stat-num">' + todayJobs.length + '</span> job' +
          (todayJobs.length!==1?'s':'') + ' today \u00b7 <span class="stat-num">' +
          doneToday + '</span> done \u00b7 <span class="stat-num">' +
          runningNow + '</span> active';
      } else { statsEl.textContent = 'No jobs today'; }
      expanded.forEach(id => loadDetail(id));
    } catch(e){ /* network error - silent retry on next interval */ }
  }

  async function loadDetail(id){
    try {
      const r = await fetch('/api/jobs/' + id, {cache:'no-store'});
      if(!r.ok) return;
      const j = await r.json();
      const pre = document.getElementById('full-' + id);
      if(pre){
        pre.textContent = (j.prompt ? 'PROMPT:\n' + j.prompt + '\n\nOUTPUT:\n' : '') + (j.output || '');
      }
    } catch(e){ /* swallow */ }
  }

  document.body.addEventListener('click', async (ev) => {
    const cancelBtn = ev.target.closest('[data-cancel]');
    const refreshBtn = ev.target.closest('[data-refresh]');
    const jobEl = ev.target.closest('.job');
    if(cancelBtn){
      ev.stopPropagation();
      const id = cancelBtn.getAttribute('data-cancel');
      try {
        const r = await fetch('/api/jobs/' + id + '/cancel', {method:'POST'});
        const data = await r.json();
        toast('Cancel: ' + (data.status || 'sent'), 'info');
      } catch(e){ toast('Cancel failed', 'err'); }
      fetchJobs();
      return;
    }
    if(refreshBtn){
      ev.stopPropagation();
      loadDetail(refreshBtn.getAttribute('data-refresh'));
      return;
    }
    if(jobEl){
      const id = jobEl.getAttribute('data-id');
      const detail = jobEl.querySelector('.detail');
      if(!detail) return;
      const isOpen = detail.style.display === 'block';
      if(isOpen){
        detail.style.display = 'none';
        expanded.delete(id);
        jobEl.setAttribute('aria-expanded', 'false');
      } else {
        detail.style.display = 'block';
        expanded.add(id);
        jobEl.setAttribute('aria-expanded', 'true');
        loadDetail(id);
      }
    }
  });

  document.body.addEventListener('keydown', (ev) => {
    if((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest('.job')){
      ev.preventDefault();
      ev.target.closest('.job').click();
    }
  });

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const promptInput = document.getElementById('prompt');
    const prompt = promptInput.value.trim();
    if(!prompt){ toast('Prompt required', 'err'); return; }
    const modelEl = document.querySelector('.seg-group[data-field="model"] .seg.active');
    const effortEl = document.querySelector('.seg-group[data-field="effort"] .seg.active');
    const model = modelEl ? modelEl.textContent.trim() : 'Claude Code';
    const effort = effortEl ? effortEl.textContent.trim() : 'Standard';
    const btn = form.querySelector('.primary');
    btn.disabled = true;
    btn.textContent = 'Dispatching\u2026';
    try {
      const r = await fetch('/api/jobs', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({prompt, model, effort})
      });
      if(!r.ok){
        const err = await r.json().catch(() => ({}));
        toast('Error: ' + (err.error || r.status), 'err');
        return;
      }
      promptInput.value = '';
      document.getElementById('char-count').textContent = '0 / 2000';
      toast('Dispatched', 'ok');
      fetchJobs();
    } catch(e){
      toast('Network error', 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Dispatch';
    }
  });

  document.querySelectorAll('.seg-group').forEach(g => {
    g.addEventListener('click', e => {
      const btn = e.target.closest('.seg');
      if(!btn) return;
      g.querySelectorAll('.seg').forEach(s => {
        s.classList.remove('active');
        s.setAttribute('aria-checked', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-checked', 'true');
    });
  });

  const promptEl = document.getElementById('prompt');
  const charEl = document.getElementById('char-count');
  promptEl.addEventListener('input', () => {
    charEl.textContent = promptEl.value.length + ' / 2000';
  });

  document.getElementById('btn-logs').addEventListener('click', async () => {
    const lv = document.getElementById('log-view');
    const pre = lv.querySelector('pre');
    if(lv.style.display === 'block'){ lv.style.display='none'; return; }
    try {
      const r = await fetch('/admin/dispatcher/logs', {cache:'no-store'});
      const d = await r.json();
      pre.textContent = (d.lines||[]).join('\n') || '(no logs)';
      lv.style.display = 'block';
    } catch(e){ pre.textContent='Error loading logs'; lv.style.display='block'; }
  });

  document.getElementById('btn-restart').addEventListener('click', async () => {
    if(!confirm('Restart dispatcher?')) return;
    try {
      const r = await fetch('/admin/dispatcher/restart', {method:'POST'});
      const d = await r.json();
      toast(d.status || 'Restarting\u2026', 'info');
    } catch(e){ toast('Restart failed', 'err'); }
  });

  fetchJobs();
  setInterval(fetchJobs, 5000);
})();
