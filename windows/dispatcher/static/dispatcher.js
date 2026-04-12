(function(){
  'use strict';

  /* M4: reduced-motion gate — checked once at startup */
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* =============================================================
     STATE
     ============================================================= */
  var allJobs = [];
  var expandedJobId = null;
  var currentFilter = 'all';
  var statsApiAnimated = false; /* M4: gates first-load counter animation from /api/stats */
  var initialLoad = true;
  var healthTimer = null;
  var restartTimers = {};

  /* restart card state machine: idle | verifying | executing */
  var restartState = 'idle';
  var restartRAF = null;

  var LIVE_STATES = new Set(['pending', 'running', 'cancel_requested']);
  var POLL_MS = 5000;
  var MAX_TOASTS = 3;
  var DISMISS_MS = 2500;
  var RESTART_CD_MS = 5000;
  var RING_C = 2 * Math.PI * 16; /* ~100.53 circumference */

  /* =============================================================
     DOM REFS
     ============================================================= */
  function $(id){ return document.getElementById(id); }

  var form           = $('job-form');
  var promptEl       = $('prompt');
  var charEl         = $('char-count');
  var statsEl        = $('stats-bar');
  var liveListEl     = $('live-list');
  var historyListEl  = $('history-list');
  var liveHeader     = $('live-header');
  var historyHeader  = $('history-header');
  var liveCountEl    = $('live-count');
  var historyCountEl = $('history-count');
  var jobEmptyEl     = $('job-empty');
  var skeletonsEl    = $('skeletons');
  var sidebar        = $('sidebar');
  var logDrawer      = $('log-drawer');
  var logBackdrop    = $('log-backdrop');
  var logContent     = $('log-content');
  var toastRack      = $('toast-rack');
  var submitWrap     = $('submit-wrap');

  /* health */
  var healthPM       = $('health-pm');
  var healthAI       = $('health-miru-ai');
  var dotPM          = $('dot-pm');
  var dotAI          = $('dot-ai');
  var healthDetailPM = $('health-detail-pm');
  var healthDetailAI = $('health-detail-miru-ai');

  /* restart card */
  var restartCard    = $('restart-card');
  var restartInner   = $('restart-card-inner');
  var restartLabel   = $('restart-label');
  var restartSub     = $('restart-sub');
  var restartClose   = $('restart-close');
  var ringFg         = $('ring-fg');

  /* =============================================================
     HELPERS
     ============================================================= */
  function esc(s){
    return (s || '').replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function fmtTime(iso){
    if(!iso) return '';
    try {
      var d = new Date(iso);
      var diff = (Date.now() - d.getTime()) / 1000;
      if(diff < 10)    return 'now';
      if(diff < 60)    return Math.floor(diff) + 's';
      if(diff < 3600)  return Math.floor(diff / 60) + 'm';
      if(diff < 86400) return Math.floor(diff / 3600) + 'h';
      return d.toLocaleDateString();
    } catch(e){ return iso; }
  }

  function dotClass(status){
    if(status === 'running')          return 'dot dot-running';
    if(status === 'done')             return 'dot dot-done';
    if(status === 'failed')           return 'dot dot-failed';
    if(status === 'cancelled')        return 'dot dot-cancelled';
    if(status === 'cancel_requested') return 'dot dot-cancel_requested';
    if(status === 'pending')          return 'dot dot-pending';
    return 'dot';
  }

  function icons(){
    try { if(window.lucide) lucide.createIcons(); } catch(e){ /* silent */ }
  }

  /* =============================================================
     TOAST — bottom-right, 320px max, 2px left stripe, stack max 3
     ============================================================= */
  function toast(msg, type){
    type = type || 'info';
    var prefix = type === 'ok' ? '\u2713 ' : type === 'err' ? '\u2717 ' : '';
    var el = document.createElement('div');
    el.className = 'toast t-' + type;
    el.textContent = prefix + msg;
    /* M4 Anim 4: slide up from below */
    if(!reduceMotion) el.style.animation = 'toast-in 220ms cubic-bezier(0.16,1,0.3,1)';
    toastRack.appendChild(el);
    requestAnimationFrame(function(){ el.classList.add('show'); });
    var all = toastRack.querySelectorAll('.toast');
    while(all.length > MAX_TOASTS){ all[0].remove(); all = toastRack.querySelectorAll('.toast'); }
    setTimeout(function(){
      el.classList.remove('show');
      setTimeout(function(){ if(el.parentNode) el.remove(); }, 300);
    }, DISMISS_MS);
  }

  /* =============================================================
     CLIPBOARD
     ============================================================= */
  function showCopyFeedback(btn, ok, msg){
    if(!btn){ toast(ok ? 'Copied' : (msg || 'Copy failed'), ok ? 'ok' : 'err'); return; }
    var orig = btn.textContent;
    var cls = ok ? 'copy-ok' : 'copy-err';
    btn.textContent = ok ? '\u2713 Copied' : (msg || '\u2717 Failed');
    btn.classList.add(cls);
    setTimeout(function(){ btn.textContent = orig; btn.classList.remove(cls); }, 2000);
  }

  function copyToClipboard(text, btn){
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(text).then(function(){
        showCopyFeedback(btn, true);
      }).catch(function(){ fallbackCopy(text, btn); });
    } else { fallbackCopy(text, btn); }
  }

  function fallbackCopy(text, btn){
    var ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', '');
    ta.className = 'sr-only-offscreen';
    document.body.appendChild(ta); ta.select();
    try { showCopyFeedback(btn, document.execCommand('copy'), 'Select text manually'); }
    catch(e){ showCopyFeedback(btn, false, 'Select text manually'); }
    document.body.removeChild(ta);
  }

  async function fetchAndCopy(id, btn){
    try {
      var r = await fetch('/api/jobs/' + id, {cache:'no-store'});
      if(!r.ok){ showCopyFeedback(btn, false, 'Load failed'); return; }
      var j = await r.json();
      if(!j.output){ showCopyFeedback(btn, false, 'No output'); return; }
      copyToClipboard(j.output, btn);
    } catch(e){ showCopyFeedback(btn, false, 'Network error'); }
  }

  /* =============================================================
     VIEW MANAGEMENT
     ============================================================= */
  function switchView(name){
    document.querySelectorAll('.view').forEach(function(v){ v.classList.remove('active'); });
    var target = $('view-' + name);
    if(target) target.classList.add('active');
    window.scrollTo(0, 0);
    /* sidebar nav highlight (preserved for rollback) */
    document.querySelectorAll('.nav-item[data-nav]').forEach(function(b){
      var nav = b.getAttribute('data-nav');
      if(nav === 'logs') return;
      b.classList.toggle('active', nav === name);
    });
    /* bottom nav highlight */
    document.querySelectorAll('.bnav-tab[data-nav]').forEach(function(b){
      b.classList.toggle('active', b.getAttribute('data-nav') === name);
    });
    if(name === 'health') fetchHealth();
  }

  /* sidebar nav (preserved for rollback) */
  document.querySelectorAll('.nav-item[data-nav]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var nav = btn.getAttribute('data-nav');
      if(!nav) return;
      if(nav === 'logs'){ toggleLogs(); return; }
      switchView(nav);
      closeSidebar();
    });
  });

  /* bottom nav (Phase 1) */
  document.querySelectorAll('.bnav-tab[data-nav]').forEach(function(btn){
    btn.addEventListener('click', function(){
      var nav = btn.getAttribute('data-nav');
      if(nav) switchView(nav);
    });
  });

  /* =============================================================
     MOBILE SIDEBAR TOGGLE
     ============================================================= */
  function openSidebar(){ sidebar.classList.add('open'); document.body.classList.add('sidebar-open'); }
  function closeSidebar(){ sidebar.classList.remove('open'); document.body.classList.remove('sidebar-open'); }

  var btnMobileMenu = $('btn-mobile-menu');
  if(btnMobileMenu){
    btnMobileMenu.addEventListener('click', function(){
      sidebar.classList.contains('open') ? closeSidebar() : openSidebar();
    });
  }
  document.addEventListener('click', function(ev){
    if(!document.body.classList.contains('sidebar-open')) return;
    if(sidebar.contains(ev.target)) return;
    if(ev.target.closest('#btn-mobile-menu')) return;
    closeSidebar();
  });

  /* =============================================================
     FILTER CHIPS
     ============================================================= */
  function setFilter(f){
    currentFilter = f;
    document.querySelectorAll('#filter-chips .chip').forEach(function(c){
      var active = c.getAttribute('data-filter') === f;
      c.classList.toggle('active', active);
      c.setAttribute('aria-checked', active ? 'true' : 'false');
    });
    renderJobs();
  }

  document.querySelectorAll('#filter-chips .chip').forEach(function(c){
    c.addEventListener('click', function(){ setFilter(c.getAttribute('data-filter')); });
  });

  function filterJobs(jobs){
    if(currentFilter === 'all')     return jobs;
    if(currentFilter === 'running') return jobs.filter(function(j){ return LIVE_STATES.has(j.status); });
    if(currentFilter === 'done')    return jobs.filter(function(j){ return j.status === 'done'; });
    if(currentFilter === 'failed')  return jobs.filter(function(j){ return j.status === 'failed' || j.status === 'cancelled'; });
    if(currentFilter === 'sim')     return jobs.filter(function(j){ return j.executor_mode === 'simulated'; });
    return jobs;
  }

  /* =============================================================
     FORM COLLAPSE TOGGLE
     ============================================================= */
  var btnToggleForm = $('btn-toggle-form');
  if(btnToggleForm){
    btnToggleForm.addEventListener('click', function(){
      var isOpen = submitWrap.classList.contains('open');
      submitWrap.classList.toggle('open');
      if(!isOpen){
        promptEl.focus({ preventScroll: true });
      } else {
        var mainEl = document.querySelector('.main');
        if(mainEl) mainEl.scrollTop = 0;
        window.scrollTo(0, 0);
      }
    });
  }

  /* =============================================================
     STATS KPI — computed from allJobs with 300ms countUp
     ============================================================= */
  function animateNum(el, target){
    if(!el || typeof target !== 'number' || target === 0){ if(el) el.textContent = target || '0'; return; }
    if(reduceMotion){ el.textContent = target; return; } /* M4: skip animation if reduced-motion */
    var dur = 600, start = performance.now(); /* M4: 600ms easeOut countup */
    function tick(now){
      var p = Math.min((now - start) / dur, 1);
      el.textContent = Math.round(target * (1 - Math.pow(1 - p, 3)));
      if(p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function updateStats(){
    /* derive 3-card metrics from allJobs — always snap, never animate.
       Animation is handled solely by fetchStatsEndpoint() via statsApiAnimated. */
    var total   = allJobs.length;
    var running = allJobs.filter(function(j){ return j.status === 'running'; }).length;
    var queued  = allJobs.filter(function(j){ return j.status === 'pending'; }).length;
    var runCls  = running > 0 ? ' kpi-green' : '';

    statsEl.innerHTML =
      '<div class="kpi"><span class="kpi-num">' + total + '</span><span class="kpi-label">Total</span></div>' +
      '<div class="kpi"><span class="kpi-num' + runCls + '">' + running + '</span><span class="kpi-label">Running</span></div>' +
      '<div class="kpi"><span class="kpi-num">' + queued + '</span><span class="kpi-label">Queued</span></div>';
  }

  /* /api/stats — real server totals; drives M4 counter animation + dispatch-pulse */
  async function fetchStatsEndpoint(){
    try {
      var r = await fetch('/api/stats', {cache:'no-store'});
      if(!r.ok) return;
      var s = await r.json();
      var total   = s.total_jobs    || 0;
      var running = s.running_count || 0;
      var queued  = s.pending_count || 0;
      var runCls  = running > 0 ? ' kpi-green' : '';

      /* M4 Anim 3: pulse Dispatch button while any job is running */
      var dispBtn = $('btn-dispatch');
      if(dispBtn){
        if(running > 0) dispBtn.classList.add('is-running');
        else dispBtn.classList.remove('is-running');
      }

      /* M4 Anim 1: count-up on first /api/stats response only */
      if(!statsApiAnimated){
        statsApiAnimated = true;
        statsEl.innerHTML =
          '<div class="kpi"><span class="kpi-num" data-n="' + total   + '">0</span><span class="kpi-label">Total</span></div>' +
          '<div class="kpi"><span class="kpi-num' + runCls + '" data-n="' + running + '">0</span><span class="kpi-label">Running</span></div>' +
          '<div class="kpi"><span class="kpi-num" data-n="' + queued  + '">0</span><span class="kpi-label">Queued</span></div>';
        statsEl.querySelectorAll('.kpi-num[data-n]').forEach(function(el){
          animateNum(el, parseInt(el.getAttribute('data-n'), 10));
        });
      } else {
        statsEl.innerHTML =
          '<div class="kpi"><span class="kpi-num">' + total   + '</span><span class="kpi-label">Total</span></div>' +
          '<div class="kpi"><span class="kpi-num' + runCls + '">' + running + '</span><span class="kpi-label">Running</span></div>' +
          '<div class="kpi"><span class="kpi-num">' + queued  + '</span><span class="kpi-label">Queued</span></div>';
      }
    } catch(e){ /* silent */ }
  }

  /* =============================================================
     JOB CARD RENDERING
     ============================================================= */
  function renderCardRow(j){
    var simBadge = j.executor_mode === 'simulated' ? '<span class="badge-sim">SIM</span>' : '';
    return '<div class="job-card-row">' +
      '<span class="' + dotClass(j.status) + '"></span>' +
      '<span class="jr-id">' + esc((j.id || '').substring(0, 8)) + '</span>' +
      '<span class="jr-prompt">' + esc(j.prompt ? j.prompt.substring(0, 80) : (j.output_preview || '(no output yet)')) + '</span>' +
      '<span class="jr-meta">' +
        '<span class="pill">' + esc(j.model) + '</span>' +
        '<span class="pill">' + esc(j.effort) + '</span>' +
        simBadge +
      '</span>' +
      '<span class="jr-time">' + fmtTime(j.created_at) + '</span>' +
      '<svg class="jr-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>' +
    '</div>' +
    '<div class="job-card-detail"><div class="job-card-detail-inner"></div></div>';
  }

  function updateCardRow(el, j){
    var dot = el.querySelector('.job-card-row .dot');
    if(dot){ var dc = dotClass(j.status); if(dot.className !== dc) dot.className = dc; }
    var prompt = el.querySelector('.jr-prompt');
    if(prompt){
      var t = j.prompt ? j.prompt.substring(0, 80) : (j.output_preview || '(no output yet)');
      if(prompt.textContent !== t) prompt.textContent = t;
    }
    var time = el.querySelector('.jr-time');
    if(time) time.textContent = fmtTime(j.created_at);
  }

  /* =============================================================
     JOB LIST — DOM RECONCILIATION (live + history split)
     ============================================================= */
  function renderJobs(){
    var filtered = filterJobs(allJobs);
    var live = filtered.filter(function(j){ return LIVE_STATES.has(j.status); });
    var history = filtered.filter(function(j){ return !LIVE_STATES.has(j.status); });

    reconcileList(liveListEl, live);
    reconcileList(historyListEl, history);

    /* section headers */
    liveHeader.classList.toggle('hidden', live.length === 0);
    historyHeader.classList.toggle('hidden', history.length === 0);
    liveCountEl.textContent = live.length;
    historyCountEl.textContent = history.length;

    /* empty state */
    jobEmptyEl.classList.toggle('hidden', filtered.length > 0);
  }

  function reconcileList(container, jobs){
    /* map existing cards */
    var existMap = {};
    container.querySelectorAll('.job-card[data-id]').forEach(function(el){
      existMap[el.getAttribute('data-id')] = el;
    });

    /* remove stale */
    var newIds = new Set(jobs.map(function(j){ return j.id; }));
    Object.keys(existMap).forEach(function(id){
      if(!newIds.has(id)) existMap[id].remove();
    });

    /* build ordered list */
    var ordered = [];
    jobs.forEach(function(j){
      var el = existMap[j.id];
      if(el){
        updateCardRow(el, j);
        ordered.push(el);
      } else {
        var card = document.createElement('div');
        card.className = 'job-card' + (expandedJobId === j.id ? ' expanded' : '');
        card.setAttribute('data-id', j.id);
        card.setAttribute('tabindex', '0');
        card.innerHTML = renderCardRow(j);
        ordered.push(card);
      }
    });

    /* reorder only if DOM order changed */
    var cur = Array.from(container.querySelectorAll('.job-card[data-id]'));
    var reorder = ordered.length !== cur.length;
    if(!reorder){
      for(var i = 0; i < ordered.length; i++){
        if(ordered[i] !== cur[i]){ reorder = true; break; }
      }
    }
    if(reorder){
      ordered.forEach(function(el){ container.appendChild(el); });
    }
  }

  /* =============================================================
     INLINE CARD EXPANSION (accordion)
     ============================================================= */
  function expandCard(id){
    /* collapse any previously expanded card */
    if(expandedJobId && expandedJobId !== id){
      collapseCard(expandedJobId);
    }
    expandedJobId = id;
    var card = document.querySelector('.job-card[data-id="' + id + '"]');
    if(!card) return;
    card.classList.add('expanded');
    loadCardDetail(id, card);
  }

  function collapseCard(id){
    if(!id) return;
    var card = document.querySelector('.job-card[data-id="' + id + '"]');
    if(card) card.classList.remove('expanded');
    if(expandedJobId === id) expandedJobId = null;
  }

  async function loadCardDetail(id, card){
    var inner = card.querySelector('.job-card-detail-inner');
    if(!inner) return;
    inner.innerHTML = '<div class="jcd-body"><span class="jcd-loading">Loading\u2026</span></div>';

    try {
      var r = await fetch('/api/jobs/' + id, {cache:'no-store'});
      if(!r.ok){ inner.innerHTML = '<div class="jcd-body"><span class="jcd-error">Failed to load</span></div>'; return; }
      var j = await r.json();
      var cancelable = j.status === 'pending' || j.status === 'running';

      var html = '<div class="jcd-body">';
      html += '<div class="jcd-field"><span class="jcd-label">Status</span><span class="jcd-value"><span class="' + dotClass(j.status) + '"></span> ' + esc(j.status) + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">ID</span><span class="jcd-value jcd-mono">' + esc(j.id) + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">Model</span><span class="jcd-value">' + esc(j.model) + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">Effort</span><span class="jcd-value">' + esc(j.effort) + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">Mode</span><span class="jcd-value">' + esc(j.executor_mode || '\u2014') + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">Created</span><span class="jcd-value">' + esc(j.created_at || '\u2014') + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">Finished</span><span class="jcd-value">' + esc(j.finished_at || '\u2014') + '</span></div>';
      html += '<div class="jcd-field"><span class="jcd-label">Duration</span><span class="jcd-value">' + esc(j.duration || '\u2014') + '</span></div>';

      if(j.input_tokens != null){
        html += '<div class="jcd-field"><span class="jcd-label">Tokens</span><span class="jcd-value">' +
          j.input_tokens + ' in / ' + (j.output_tokens || 0) + ' out</span></div>';
      }
      if(j.estimated_cost != null && j.estimated_cost > 0){
        html += '<div class="jcd-field"><span class="jcd-label">Cost</span><span class="jcd-value">$' +
          j.estimated_cost.toFixed(4) + '</span></div>';
      }

      html += '<div class="jcd-section"><span class="jcd-label">Prompt</span>' +
        '<pre class="jcd-pre">' + esc(j.prompt || '\u2014') + '</pre></div>';

      html += '<div class="jcd-section"><div class="jcd-section-head"><span class="jcd-label">Output</span>' +
        '<button class="btn-ghost btn-sm" data-copy="' + j.id + '">Copy</button></div>' +
        '<pre class="jcd-pre">' + esc(j.output || '(no output yet)') + '</pre></div>';

      html += '<div class="jcd-actions">';
      if(cancelable) html += '<button class="btn-ghost btn-sm danger" data-cancel="' + j.id + '">Cancel</button>';
      html += '<a href="/jobs/' + j.id + '" class="btn-ghost btn-sm" target="_blank">Open Page</a>';
      html += '</div></div>';

      inner.innerHTML = html;
    } catch(e){
      inner.innerHTML = '<div class="jcd-body"><span class="jcd-error">Network error</span></div>';
    }
  }

  /* =============================================================
     SKELETON MANAGEMENT
     ============================================================= */
  function hideSkeletons(){
    if(skeletonsEl) skeletonsEl.classList.add('hidden');
  }

  /* =============================================================
     FETCH JOBS (main polling loop)
     ============================================================= */
  async function fetchJobs(){
    try {
      var r = await fetch('/api/jobs', {cache:'no-store'});
      if(!r.ok) return;
      var data = await r.json();
      allJobs = data.jobs || [];

      if(initialLoad){
        initialLoad = false;
        hideSkeletons();
      }

      renderJobs();
      updateStats();
      fetchStatsEndpoint();

      /* refresh expanded card if still open */
      if(expandedJobId){
        var card = document.querySelector('.job-card[data-id="' + expandedJobId + '"]');
        if(card && card.classList.contains('expanded')){
          loadCardDetail(expandedJobId, card);
        }
      }
    } catch(e){ /* silent retry */ }
  }

  /* =============================================================
     JOB CLICK HANDLING (delegated)
     ============================================================= */
  document.body.addEventListener('click', function(ev){
    /* cancel button */
    var cancelBtn = ev.target.closest('[data-cancel]');
    if(cancelBtn){
      ev.stopPropagation();
      var cid = cancelBtn.getAttribute('data-cancel');
      fetch('/api/jobs/' + cid + '/cancel', {method:'POST'})
        .then(function(r){ return r.json(); })
        .then(function(d){ toast('Cancel: ' + (d.status || 'sent'), 'info'); fetchJobs(); })
        .catch(function(){ toast('Cancel failed', 'err'); });
      return;
    }

    /* copy button */
    var copyBtn = ev.target.closest('[data-copy]');
    if(copyBtn){
      ev.stopPropagation();
      fetchAndCopy(copyBtn.getAttribute('data-copy'), copyBtn);
      return;
    }

    /* job card row click → toggle inline expansion */
    var row = ev.target.closest('.job-card-row');
    if(row){
      var card = row.closest('.job-card');
      if(!card) return;
      var jid = card.getAttribute('data-id');
      if(expandedJobId === jid){
        collapseCard(jid);
      } else {
        expandCard(jid);
      }
      return;
    }
  });

  /* keyboard nav on job cards */
  document.body.addEventListener('keydown', function(ev){
    if((ev.key === 'Enter' || ev.key === ' ') && ev.target.closest('.job-card')){
      ev.preventDefault();
      var cardRow = ev.target.closest('.job-card').querySelector('.job-card-row');
      if(cardRow) cardRow.click();
    }
  });

  /* =============================================================
     FORM SUBMIT
     ============================================================= */
  form.addEventListener('submit', async function(ev){
    ev.preventDefault();
    var prompt = promptEl.value.trim();
    if(!prompt){ toast('Prompt required', 'err'); return; }
    var modelEl  = document.querySelector('.seg-group[data-field="model"] .seg.active');
    var effortEl = document.querySelector('.seg-group[data-field="effort"] .seg.active');
    var m3ModelInput  = $('m3-model');
    var m3EffortInput = $('m3-effort');
    var model  = m3ModelInput  ? m3ModelInput.value  : (modelEl  ? modelEl.textContent.trim()  : 'Claude');
    var effort = m3EffortInput ? m3EffortInput.value : (effortEl ? effortEl.textContent.trim() : 'Standard');
    var btn = $('btn-dispatch') || form.querySelector('.pc-dispatch') || form.querySelector('.btn-primary');
    btn.disabled = true;
    btn.textContent = 'Dispatching\u2026';
    try {
      var r = await fetch('/api/jobs', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({prompt: prompt, model: model, effort: effort})
      });
      if(!r.ok){
        var err = await r.json().catch(function(){ return {}; });
        toast('Error: ' + (err.error || r.status), 'err');
        return;
      }
      promptEl.value = '';
      charEl.textContent = '0 / 4000';
      toast('Dispatched', 'ok');
      fetchJobs();
      closeSidebar();
    } catch(e){ toast('Network error', 'err'); }
    finally {
      btn.disabled = false;
      btn.textContent = 'Dispatch';
    }
  });

  /* =============================================================
     SEGMENTED CONTROLS
     ============================================================= */
  document.querySelectorAll('.seg-group').forEach(function(g){
    g.addEventListener('click', function(e){
      var btn = e.target.closest('.seg');
      if(!btn) return;
      g.querySelectorAll('.seg').forEach(function(s){
        s.classList.remove('active');
        s.setAttribute('aria-checked', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-checked', 'true');
    });
  });

  /* =============================================================
     CHAR COUNT
     ============================================================= */
  promptEl.addEventListener('input', function(){
    charEl.textContent = promptEl.value.length + ' / 4000';
  });

  /* =============================================================
     HEALTH PANEL
     ============================================================= */
  async function fetchHealth(){
    try {
      var r = await fetch('/api/health', {cache:'no-store'});
      if(!r.ok) return;
      var data = await r.json();
      renderHealthStatus(dotPM, healthPM, healthDetailPM, data.pm);
      renderHealthStatus(dotAI, healthAI, healthDetailAI, data.miru_ai);
    } catch(e){
      if(healthPM) healthPM.textContent = 'error';
      if(dotPM)    dotPM.className = 'dot dot-down';
      if(healthAI) healthAI.textContent = 'error';
      if(dotAI)    dotAI.className = 'dot dot-down';
    }
  }

  function renderHealthStatus(dotEl, textEl, detailEl, info){
    if(!info){
      if(textEl)   textEl.textContent = 'unknown';
      if(dotEl)    dotEl.className = 'dot';
      if(detailEl){ detailEl.textContent = ''; detailEl.classList.add('hidden'); }
      return;
    }
    if(textEl) textEl.textContent = info.status;
    if(dotEl){
      var cls = 'dot ';
      if(info.status === 'up')              cls += 'dot-up';
      else if(info.status === 'restarting') cls += 'dot-restarting';
      else if(info.status === 'down' || info.status === 'timeout') cls += 'dot-down';
      dotEl.className = cls;
    }
    if(detailEl){
      if(info.detail){
        detailEl.textContent = info.detail;
        detailEl.classList.remove('hidden', 'detail-ok', 'detail-err');
        if(info.status === 'up') detailEl.classList.add('detail-ok');
        else if(info.status === 'down' || info.status === 'timeout') detailEl.classList.add('detail-err');
      } else {
        detailEl.textContent = '';
        detailEl.classList.add('hidden');
        detailEl.classList.remove('detail-ok', 'detail-err');
      }
    }
  }

  function startHealthPoll(){
    if(healthTimer) clearInterval(healthTimer);
    var count = 0;
    healthTimer = setInterval(function(){
      fetchHealth();
      if(++count >= 15){ clearInterval(healthTimer); healthTimer = null; }
    }, 2000);
  }

  /* =============================================================
     SERVICE RESTART (Health cards — PM / Miru AI)
     ============================================================= */
  var SVC_RESTART_CD = 10;

  function setupServiceRestart(btn){
    var svc = btn.getAttribute('data-restart');
    if(!svc) return;
    restartTimers[svc] = {state:'idle', timer:null, cd:0};

    btn.addEventListener('click', function(ev){
      ev.stopPropagation();
      var rs = restartTimers[svc];

      if(rs.state === 'idle'){
        rs.state = 'verifying';
        rs.cd = SVC_RESTART_CD;
        btn.textContent = 'Confirm? (' + rs.cd + ')';
        btn.classList.add('verifying');
        rs.timer = setInterval(function(){
          rs.cd--;
          if(rs.cd <= 0){
            clearInterval(rs.timer);
            rs.state = 'idle';
            resetServiceBtn(btn, svc);
          } else {
            btn.textContent = 'Confirm? (' + rs.cd + ')';
          }
        }, 1000);
      } else if(rs.state === 'verifying'){
        clearInterval(rs.timer);
        rs.state = 'executing';
        btn.textContent = 'Restarting\u2026';
        btn.classList.remove('verifying');
        btn.disabled = true;
        doServiceRestart(svc, btn);
      }
    });
  }

  function resetServiceBtn(btn, svc){
    btn.classList.remove('verifying');
    btn.disabled = false;
    btn.textContent = svc === 'pm' ? 'Restart PM' : 'Restart Miru AI';
  }

  async function doServiceRestart(svc, btn){
    try {
      var r = await fetch('/api/restart/' + svc, {method:'POST'});
      var data = await r.json();
      if(r.ok){
        var label = svc === 'pm' ? 'PM' : 'Miru AI';
        toast(label + ': restart initiated', 'info');
        if(svc === 'pm' && dotPM){ dotPM.className = 'dot dot-restarting'; healthPM.textContent = 'restarting'; }
        if(svc === 'miru_ai' && dotAI){ dotAI.className = 'dot dot-restarting'; healthAI.textContent = 'restarting'; }
        var detEl = svc === 'pm' ? healthDetailPM : healthDetailAI;
        if(detEl){ detEl.textContent = ''; detEl.classList.add('hidden'); }
        startHealthPoll();
      } else {
        toast('Error: ' + (data.error || 'restart failed'), 'err');
      }
    } catch(e){ toast('Restart failed: network error', 'err'); }
    restartTimers[svc].state = 'idle';
    setTimeout(function(){ resetServiceBtn(btn, svc); }, 2000);
  }

  /* init service restart buttons */
  document.querySelectorAll('.h-restart[data-restart]').forEach(setupServiceRestart);

  /* =============================================================
     DISPATCHER RESTART — floating card with 5s SVG countdown ring
     State machine: idle → verifying → executing
     ============================================================= */
  function showRestartCard(){
    if(restartState !== 'idle') return;
    restartState = 'verifying';
    restartCard.classList.add('visible');
    restartLabel.textContent = 'Restart Dispatcher?';
    restartSub.textContent = 'Click to confirm \u00b7 5s';
    ringFg.style.strokeDashoffset = '0';
    icons();

    var startTime = performance.now();

    function tickRing(now){
      if(restartState !== 'verifying') return;
      var elapsed = now - startTime;
      var progress = Math.min(elapsed / RESTART_CD_MS, 1);
      ringFg.style.strokeDashoffset = (RING_C * progress).toFixed(2);
      var remaining = Math.ceil((RESTART_CD_MS - elapsed) / 1000);
      restartSub.textContent = 'Click to confirm \u00b7 ' + Math.max(remaining, 0) + 's';
      if(progress < 1){
        restartRAF = requestAnimationFrame(tickRing);
      } else {
        hideRestartCard();
      }
    }
    restartRAF = requestAnimationFrame(tickRing);
  }

  function confirmRestart(){
    if(restartState !== 'verifying') return;
    if(restartRAF) cancelAnimationFrame(restartRAF);
    restartState = 'executing';
    restartLabel.textContent = 'Restarting\u2026';
    restartSub.textContent = '';
    ringFg.style.strokeDashoffset = String(RING_C);

    fetch('/admin/dispatcher/restart', {method:'POST'})
      .then(function(r){ return r.json(); })
      .then(function(d){ toast(d.status || 'Restarting\u2026', 'info'); })
      .catch(function(){ toast('Restart failed', 'err'); })
      .finally(function(){
        setTimeout(hideRestartCard, 2000);
      });
  }

  function hideRestartCard(){
    if(restartRAF) cancelAnimationFrame(restartRAF);
    restartState = 'idle';
    restartCard.classList.remove('visible');
  }

  /* restart card click → confirm */
  restartInner.addEventListener('click', function(ev){
    if(ev.target.closest('.restart-close')) return;
    if(restartState === 'verifying') confirmRestart();
  });

  /* restart card close button → cancel */
  restartClose.addEventListener('click', function(ev){
    ev.stopPropagation();
    hideRestartCard();
  });

  /* sidebar restart button triggers the floating card */
  var btnRestartDisp = $('btn-restart-dispatcher');
  if(btnRestartDisp){
    btnRestartDisp.addEventListener('click', function(){
      showRestartCard();
      closeSidebar();
    });
  }

  /* =============================================================
     LOG DRAWER — slides from right, keyboard L, Escape to close
     ============================================================= */
  function openLogs(){
    logDrawer.classList.add('open');
    loadLogs();
  }

  function closeLogs(){
    logDrawer.classList.remove('open');
  }

  function toggleLogs(){
    logDrawer.classList.contains('open') ? closeLogs() : openLogs();
  }

  async function loadLogs(){
    logContent.textContent = 'Loading\u2026';
    try {
      var r = await fetch('/admin/dispatcher/logs', {cache:'no-store'});
      var d = await r.json();
      logContent.textContent = (d.lines || []).join('\n') || '(no logs)';
    } catch(e){ logContent.textContent = 'Error loading logs'; }
  }

  $('btn-close-logs').addEventListener('click', closeLogs);
  logBackdrop.addEventListener('click', closeLogs);

  /* =============================================================
     THEME TOGGLE — system preference default, manual override
     ============================================================= */
  $('btn-theme').addEventListener('click', function(){
    var html = document.documentElement;
    var cur = html.getAttribute('data-theme') || 'dark';
    var next = cur === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    var meta = document.querySelector('meta[name="theme-color"]');
    if(meta) meta.setAttribute('content', next === 'dark' ? '#0D0D10' : '#f7f6f2');
    /* update mode pill label + icon */
    var lbl = document.getElementById('mode-label');
    var icon = document.getElementById('mode-icon');
    if(lbl) lbl.textContent = next === 'dark' ? 'Dark' : 'Light';
    if(icon) icon.innerHTML = next === 'dark'
      ? '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
      : '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>';
  });

  /* system theme change listener disabled — dark default in Phase 1 */

  /* =============================================================
     M2: WORKER SHEET
     ============================================================= */
  (function(){
    var sheet = document.getElementById('worker-sheet');
    var pill  = document.getElementById('btn-worker-pill');
    var avatarEl = document.getElementById('worker-avatar');
    var nameEl   = document.getElementById('worker-name');
    if(!sheet || !pill) return;

    pill.addEventListener('click', function(){ sheet.classList.add('open'); });
    sheet.addEventListener('click', function(e){ if(e.target === sheet) sheet.classList.remove('open'); });

    sheet.querySelectorAll('.ws-item').forEach(function(item){
      item.addEventListener('click', function(){
        sheet.querySelectorAll('.ws-item').forEach(function(i){
          i.classList.remove('selected');
          var chk = i.querySelector('.ws-check');
          if(chk) chk.outerHTML = '<div class="ws-empty"></div>';
        });
        item.classList.add('selected');
        var emp = item.querySelector('.ws-empty');
        if(emp) emp.outerHTML = '<div class="ws-check"><svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12" stroke="#fff" fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>';
        if(avatarEl){
          avatarEl.textContent = item.dataset.label || '?';
          avatarEl.style.background = 'linear-gradient(135deg,' + item.dataset.colorA + ',' + item.dataset.colorB + ')';
        }
        if(nameEl) nameEl.textContent = item.dataset.name || '';
        setTimeout(function(){ sheet.classList.remove('open'); }, 220);
      });
    });
  })();

  /* =============================================================
     KEYBOARD SHORTCUTS
     ============================================================= */
  document.addEventListener('keydown', function(ev){
    var tag = (document.activeElement || {}).tagName;
    if(tag === 'TEXTAREA' || tag === 'INPUT') return;

    if((ev.key === 'l' || ev.key === 'L') && !ev.ctrlKey && !ev.metaKey && !ev.altKey){
      ev.preventDefault();
      toggleLogs();
    }

    if(ev.key === 'Escape'){
      if(restartState === 'verifying'){ hideRestartCard(); return; }
      var wsheet = document.getElementById('worker-sheet');
      if(wsheet && wsheet.classList.contains('open')){ wsheet.classList.remove('open'); return; }
      if(logDrawer.classList.contains('open')){ closeLogs(); return; }
      if(expandedJobId){ collapseCard(expandedJobId); return; }
      if(sidebar.classList.contains('open')){ closeSidebar(); return; }
    }

    /* N key opens the form */
    if((ev.key === 'n' || ev.key === 'N') && !ev.ctrlKey && !ev.metaKey && !ev.altKey){
      ev.preventDefault();
      submitWrap.classList.add('open');
      promptEl.focus({ preventScroll: true });
    }
  });

  /* =============================================================
     M3: ADVANCED OPTIONS TOGGLE
     ============================================================= */
  (function(){
    var hd   = $('btn-adv');
    var body = $('adv-body');
    var chev = $('adv-chev');
    if(!hd) return;
    hd.addEventListener('click', function(){
      if(body) body.classList.toggle('open');
      if(chev) chev.classList.toggle('open');
    });
  })();

  /* =============================================================
     M3: RECENT COLLAPSIBLE + FETCH
     ============================================================= */
  function fmtTimeAgo(iso){
    if(!iso) return '';
    try {
      var diff = (Date.now() - new Date(iso).getTime()) / 1000;
      if(diff < 60)    return 'just now';
      if(diff < 3600)  return Math.floor(diff / 60) + 'm ago';
      if(diff < 86400) return Math.floor(diff / 3600) + 'h ago';
      return Math.floor(diff / 86400) + 'd ago';
    } catch(e){ return ''; }
  }

  function renderRecent(jobs){
    var countEl = $('rec-count');
    var bodyEl  = $('rec-body');
    if(countEl) countEl.textContent = jobs.length;
    if(!bodyEl) return;
    if(jobs.length === 0){
      bodyEl.innerHTML = '<div class="ri"><div class="rt"><div class="rtm" style="color:var(--text-faint)">No recent jobs</div></div></div>';
      return;
    }
    bodyEl.innerHTML = jobs.map(function(j){
      var rdClass = j.status === 'done' ? 'done' : (j.status === 'running' ? 'run' : (j.status === 'failed' ? 'failed' : ''));
      var promptText = esc(j.prompt ? j.prompt.substring(0, 60) : '(no prompt)');
      var worker = esc(j.model || 'Unknown');
      var ago    = fmtTimeAgo(j.created_at);
      var status = esc(j.status || '');
      return '<div class="ri">' +
        '<div class="rd ' + rdClass + '"></div>' +
        '<div class="rt">' +
          '<div class="rtt">' + promptText + '</div>' +
          '<div class="rtm">' + worker + (ago ? ' \u00b7 ' + ago : '') + '</div>' +
        '</div>' +
        '<span class="rtd">' + status + '</span>' +
      '</div>';
    }).join('');
  }

  async function fetchRecent(){
    try {
      var r = await fetch('/api/history', {cache:'no-store'});
      if(!r.ok) throw new Error('not ok');
      var data = await r.json();
      var jobs = data.jobs || (Array.isArray(data) ? data : []);
      renderRecent(jobs.slice(0, 5));
    } catch(e){
      /* fallback: use already-fetched allJobs history */
      renderRecent(allJobs.filter(function(j){ return !LIVE_STATES.has(j.status); }).slice(0, 5));
    }
  }

  (function(){
    var hd   = $('btn-rec');
    var body = $('rec-body');
    var chev = $('rec-chev');
    if(!hd) return;
    hd.addEventListener('click', function(){
      if(body) body.classList.toggle('open');
      if(chev) chev.classList.toggle('open');
    });
  })();

  /* =============================================================
     M3: PASTE TAG
     ============================================================= */
  (function(){
    var btn = $('btn-paste');
    if(!btn) return;
    btn.addEventListener('click', function(){
      if(navigator.clipboard && navigator.clipboard.readText){
        navigator.clipboard.readText().then(function(text){
          promptEl.value = text;
          charEl.textContent = text.length + ' / 4000';
          promptEl.focus({ preventScroll: true });
        }).catch(function(){ toast('Clipboard access denied', 'err'); });
      } else {
        toast('Clipboard not available', 'err');
      }
    });
  })();

  /* =============================================================
     INIT
     ============================================================= */
  icons();
  fetchJobs();
  fetchRecent();
  fetchStatsEndpoint();
  setInterval(fetchJobs, POLL_MS);
  setInterval(fetchStatsEndpoint, POLL_MS);
})();
