(function(){
  'use strict';

  /* M4: reduced-motion gate — checked once at startup */
  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* iOS detection — used for Web Share API vs anchor download */
  var isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);

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
    var prefix = type === 'ok' ? '\u2713 ' : type === 'err' ? '\u2717 ' : '\u2139 ';
    var el = document.createElement('div');
    el.className = 'toast t-' + type;
    el.textContent = prefix + msg;
    if(!reduceMotion){
      el.style.opacity = '0';
      el.style.transform = 'translateY(20px) scale(0.97)';
      toastRack.appendChild(el);
      requestAnimationFrame(function(){
        requestAnimationFrame(function(){
          el.style.transition = 'opacity 220ms cubic-bezier(0.16,1,0.3,1), transform 220ms cubic-bezier(0.16,1,0.3,1)';
          el.style.opacity = '1';
          el.style.transform = 'translateY(0) scale(1)';
        });
      });
    } else {
      toastRack.appendChild(el);
    }
    var all = toastRack.querySelectorAll('.toast');
    while(all.length > MAX_TOASTS){ all[0].remove(); all = toastRack.querySelectorAll('.toast'); }
    setTimeout(function(){
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
      setTimeout(function(){ if(el.parentNode) el.remove(); }, 220);
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

    /* recent row click → open job detail sheet */
    var ri = ev.target.closest('.ri[data-id]');
    if(ri){
      ev.stopPropagation();
      var rid = ri.getAttribute('data-id');
      if(rid) loadJobDetail(rid);
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

    function openWorkerSheet(){ sheet.classList.add('open'); document.body.classList.add('sheet-open'); }
    function closeWorkerSheet(){ sheet.classList.remove('open'); document.body.classList.remove('sheet-open'); }

    pill.addEventListener('click', function(){ openWorkerSheet(); });
    sheet.addEventListener('click', function(e){ if(e.target === sheet) closeWorkerSheet(); });

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
        setTimeout(function(){ closeWorkerSheet(); }, 220);
      });
    });

    /* Swipe down to dismiss worker sheet (Standard 2) — full header gesture */
    (function(){
      var wsInner = sheet.querySelector('.ws-inner');
      var wsHandle = sheet.querySelector('.ws-handle');
      var wsTitle = sheet.querySelector('.ws-title');
      if(!wsInner) return;
      var startY = 0, dragging = false;

      function onStart(e){ startY = e.touches[0].clientY; dragging = true; wsInner.style.transition = 'none'; }
      function onMove(e){ if(!dragging) return; var d = e.touches[0].clientY - startY; if(d > 0) wsInner.style.transform = 'translateY(' + d + 'px)'; }
      function onEnd(e){
        if(!dragging) return; dragging = false;
        var d = e.changedTouches[0].clientY - startY;
        wsInner.style.transition = 'transform 280ms cubic-bezier(0.16,1,0.3,1)';
        if(d > 60){ wsInner.style.transform = 'translateY(100%)'; setTimeout(function(){ closeWorkerSheet(); wsInner.style.transform = ''; wsInner.style.transition = ''; }, 280); }
        else { wsInner.style.transform = 'translateY(0)'; setTimeout(function(){ wsInner.style.transition = ''; }, 280); }
      }

      [wsHandle, wsTitle].forEach(function(el){
        if(!el) return;
        el.addEventListener('touchstart', onStart, {passive: true});
        el.addEventListener('touchmove', onMove, {passive: true});
        el.addEventListener('touchend', onEnd, {passive: true});
      });
    })();
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
      if(wsheet && wsheet.classList.contains('open')){ wsheet.classList.remove('open'); document.body.classList.remove('sheet-open'); return; }
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
      var jid    = j.id || j.job_id || '';  /* /api/jobs uses id; /api/history uses job_id */
      var rdClass = j.status === 'done' ? 'done' : (j.status === 'running' ? 'run' : (j.status === 'failed' ? 'failed' : ''));
      var promptText = esc(j.prompt ? j.prompt.substring(0, 60) : '(no prompt)');
      var worker = esc(j.model || 'Unknown');
      var ago    = fmtTimeAgo(j.created_at);
      var status = esc(j.status || '');
      return '<div class="ri" data-id="' + esc(jid) + '">' +
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
     M5: FILE BROWSER
     ============================================================= */
  var fbTreeEl    = $('fb-tree');
  var fbPinnedEl  = $('fb-pinned');
  var fbSearchEl  = $('fb-search');
  var fileSheetEl = $('file-sheet');
  var fsCodeEl    = $('fs-code');
  var fsTitleEl   = $('fs-title');
  var fsPathEl    = $('fs-path');
  var fsCopyBtn   = $('fs-copy');
  var fsDlBtn     = $('fs-dl');
  /* iOS: relabel Download → Share (Web Share API) */
  if(fsDlBtn && isIOS){
    var dlLabel = fsDlBtn.lastChild;
    if(dlLabel && dlLabel.nodeType === 3) dlLabel.textContent = ' Share';
  }
  var fsCloseBtn  = $('fs-close');

  var fbExpanded  = {};       /* path → true */
  var fbCache     = {};       /* path → entries[] */
  var fbActiveSet = new Set();
  var fbFileText  = '';
  var fbFileName  = '';
  var fbSearchQ   = '';
  var fbPollId    = null;
  var fbLoaded    = false;
  var fsSwipeEnabled = false;
  var fbRecentViewed = [];    /* E5: recently viewed files [{path, name, mtime}] */
  var fbRecentEl  = $('fb-recent-section');
  var fsLinesEl   = $('fs-lines');
  var fsMtimeEl   = $('fs-mtime');
  var fsWrapBtn   = $('fs-wrap');
  var fsWrapLabel = $('fs-wrap-label');
  var fsDispatchBtn = $('fs-open-dispatch');
  var fbLegendEl  = $('fb-legend');
  var fbLegendToggle = $('fb-legend-toggle');
  var fbWordWrap  = false;

  var FB_HIDE_DIR = new Set(['node_modules','.git','__pycache__','.venv','dist','build','.playwright-mcp','.mypy_cache','.pytest_cache','__snapshots__','.codex_checkpoints','.codex_playwright_tmp','.cursor','.docker-config','.edge-cdp-temp','.npm-cache','.npm-tmp','.pip-cache','.pip-tmp','.playwright-browsers','.tmp-pip','.tmp-pip-cache','.tmp-pip-temp','.tmp-pydeps','.tmp-ui-build','.wheelhouse','.pip-tmp-downloads']);
  var FB_HIDE_EXT = new Set(['pyc','log','pyo']);
  var FB_PIN      = ['.mcp.json','.env'];
  /* Synthetic pinned folders (external paths mounted via backend) */
  var FB_EXT_PINS = [
    {name:'Screenshots', path:'__screenshots__', is_dir:true, size:null, icon:'camera'}
  ];

  /* helpers */
  function fbHide(e){
    if(e.is_dir && FB_HIDE_DIR.has(e.name)) return true;
    if(!e.is_dir){
      var x = e.name.split('.').pop().toLowerCase();
      if(FB_HIDE_EXT.has(x)) return true;
      if(e.name.endsWith('~') || e.name.endsWith('.tmp') || e.name.endsWith('.bak')) return true;
    }
    return false;
  }
  function fbPin(e){ return !e.is_dir && FB_PIN.indexOf(e.name) !== -1; }
  function fmtSize(b){
    if(b == null) return '';
    if(b < 1024) return b + ' B';
    if(b < 1048576) return (b/1024).toFixed(1) + ' KB';
    return (b/1048576).toFixed(1) + ' MB';
  }
  function fmtRelTime(epoch){
    if(!epoch) return '';
    var diff = (Date.now()/1000) - epoch;
    if(diff < 60) return 'just now';
    if(diff < 3600) return Math.floor(diff/60) + 'm ago';
    if(diff < 86400) return Math.floor(diff/3600) + 'h ago';
    if(diff < 604800) return Math.floor(diff/86400) + 'd ago';
    var d = new Date(epoch * 1000);
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return months[d.getMonth()] + ' ' + d.getDate();
  }
  function fmtFullTime(epoch){
    if(!epoch) return '';
    var d = new Date(epoch * 1000);
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var h = d.getHours(), ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    var min = d.getMinutes();
    return 'Modified ' + months[d.getMonth()] + ' ' + d.getDate() + ' at ' + h + ':' + (min < 10 ? '0' : '') + min + ' ' + ampm;
  }
  function isRecentlyModified(epoch){ return epoch && (Date.now()/1000 - epoch) < 1800; }
  function extLang(name){
    var x = (name||'').split('.').pop().toLowerCase();
    return {py:'python',js:'javascript',ts:'typescript',tsx:'typescript',jsx:'javascript',
      css:'css',scss:'css',html:'xml',htm:'xml',json:'json',md:'markdown',
      yml:'yaml',yaml:'yaml',sh:'bash',bash:'bash',bat:'dos',ps1:'powershell',
      sql:'sql',xml:'xml',toml:'toml',ini:'ini',env:'ini',cfg:'ini',
      txt:'plaintext',csv:'plaintext',gitignore:'plaintext',dockerfile:'dockerfile'
    }[x] || '';
  }

  var FB_CHEVRON = '<svg viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"/></svg>';

  function fbIcon(e){
    if(e.icon==='camera') return '<span class="fb-icon fb-icon-camera"><svg viewBox="0 0 24 24"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg></span>';
    if(e.is_dir) return '<span class="fb-icon fb-icon-folder"><svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></span>';
    var n = e.name.toLowerCase(), x = n.split('.').pop();
    /* special file names */
    if(n==='.env'||n==='.env.local'||n==='.env.example') return '<span class="fb-icon fb-icon-env"><svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span>';
    if(n==='.mcp.json') return '<span class="fb-icon fb-icon-cfg"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg></span>';
    /* ext → icon class + svg */
    var codeIcon = '<svg viewBox="0 0 24 24"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
    var fileIcon = '<svg viewBox="0 0 24 24"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>';
    var textIcon = '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>';
    var clsMap = {py:'fb-icon-py',js:'fb-icon-js',jsx:'fb-icon-js',ts:'fb-icon-ts',tsx:'fb-icon-ts',
      css:'fb-icon-css',scss:'fb-icon-css',html:'fb-icon-html',htm:'fb-icon-html',
      json:'fb-icon-json',md:'fb-icon-md',env:'fb-icon-env'};
    var cls = clsMap[x] || 'fb-icon-default';
    var ico = fileIcon;
    if(x==='py'||x==='js'||x==='ts'||x==='jsx'||x==='tsx'||x==='css'||x==='scss'||x==='html'||x==='htm'||x==='json') ico = codeIcon;
    if(x==='md'||x==='txt'||x==='rst') ico = textIcon;
    return '<span class="fb-icon ' + cls + '">' + ico + '</span>';
  }

  function fbRow(e, depth){
    var pad = depth * 20;
    var active = fbActiveSet.has(e.name) || fbActiveSet.has(e.path);
    var recent = !e.is_dir && isRecentlyModified(e.mtime);
    var chCls = e.is_dir ? ('fb-chev' + (fbExpanded[e.path] ? ' open' : '')) : 'fb-chev empty';
    var h = '<div class="fb-row" data-path="' + esc(e.path) + '" data-dir="' + (e.is_dir?'1':'0') + '" data-name="' + esc(e.name) + '" data-mtime="' + (e.mtime||'') + '">';
    h += '<span class="fb-indent" style="width:' + pad + 'px"></span>';
    h += '<span class="' + chCls + '">' + (e.is_dir ? FB_CHEVRON : '') + '</span>';
    h += fbIcon(e);
    if(active) h += '<span class="fb-active-dot"></span>';
    else if(recent) h += '<span class="fb-recent-dot"></span>';
    h += '<span class="fb-name' + (e.is_dir?' fb-name-dir':'') + '">' + esc(e.name) + '</span>';
    /* E3: folder count badge (collapsed only) */
    if(e.is_dir && !fbExpanded[e.path] && fbCache[e.path]){
      h += '<span class="fb-count">' + fbCache[e.path].length + '</span>';
    }
    if(!e.is_dir && e.size != null) h += '<span class="fb-size">' + fmtSize(e.size) + '</span>';
    if(!e.is_dir && e.mtime) h += '<span class="fb-mtime">' + fmtRelTime(e.mtime) + '</span>';
    h += '</div>';
    if(e.is_dir) h += '<div class="fb-children' + (fbExpanded[e.path]?' open':'') + '" data-parent="' + esc(e.path) + '"></div>';
    return h;
  }

  function fbRenderInto(el, entries, depth){
    var vis = entries.filter(function(e){ return !fbHide(e); });
    if(fbSearchQ){
      var q = fbSearchQ.toLowerCase();
      vis = vis.filter(function(e){ return e.is_dir || e.name.toLowerCase().indexOf(q) !== -1; });
    }
    vis.sort(function(a,b){ if(a.is_dir!==b.is_dir) return a.is_dir?-1:1; return a.name.toLowerCase().localeCompare(b.name.toLowerCase()); });
    if(vis.length === 0 && fbSearchQ){
      el.innerHTML = depth === 0 ? '<div class="fb-no-results">No files match \u201c' + esc(fbSearchQ) + '\u201d</div>' : '';
      return;
    }
    el.innerHTML = vis.map(function(e){ return fbRow(e, depth); }).join('');
    vis.forEach(function(e){
      if(e.is_dir && fbExpanded[e.path] && fbCache[e.path]){
        var ch = el.querySelector('.fb-children[data-parent="' + CSS.escape(e.path) + '"]');
        if(ch) fbRenderInto(ch, fbCache[e.path], depth + 1);
      }
    });
  }

  function fbRenderRecent(){
    if(!fbRecentEl) return;
    if(fbRecentViewed.length === 0){ fbRecentEl.innerHTML = ''; return; }
    var h = '<div class="fb-recent-label">Recent</div>';
    fbRecentViewed.forEach(function(item){
      h += '<div class="fb-recent-row" data-path="' + esc(item.path) + '" data-name="' + esc(item.name) + '">';
      h += '<span class="fb-icon fb-icon-default"><svg viewBox="0 0 24 24"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg></span>';
      h += '<div class="fb-recent-info"><div class="fb-recent-name">' + esc(item.name) + '</div>';
      h += '<div class="fb-recent-path">' + esc(item.path) + '</div></div>';
      if(item.mtime) h += '<span class="fb-mtime">' + fmtRelTime(item.mtime) + '</span>';
      h += '</div>';
    });
    fbRecentEl.innerHTML = h;
  }

  function fbRender(){
    if(!fbTreeEl) return;
    var root = fbCache[''] || fbCache['.'];
    if(!root){ fbTreeEl.innerHTML = '<div class="fb-loading">Loading file tree\u2026</div>'; return; }
    fbRenderRecent();
    /* pinned */
    if(fbPinnedEl){
      var pinned = root.filter(fbPin).concat(FB_EXT_PINS);
      if(pinned.length > 0){
        fbPinnedEl.innerHTML = '<div class="fb-pin-label">Pinned</div>' + pinned.map(function(e){ return fbRow(e, 0); }).join('');
        /* render children for expanded pinned folders */
        pinned.forEach(function(e){
          if(e.is_dir && fbExpanded[e.path] && fbCache[e.path]){
            var ch = fbPinnedEl.querySelector('.fb-children[data-parent="' + CSS.escape(e.path) + '"]');
            if(ch) fbRenderInto(ch, fbCache[e.path], 1);
          }
        });
      } else { fbPinnedEl.innerHTML = ''; }
    }
    /* main tree */
    fbRenderInto(fbTreeEl, root.filter(function(e){ return !fbPin(e); }), 0);
  }

  async function fbFetch(path){
    try {
      var u = '/api/files' + (path ? '?path=' + encodeURIComponent(path) : '');
      var r = await fetch(u, {cache:'no-store'});
      if(!r.ok) return null;
      var d = await r.json();
      return d.entries || [];
    } catch(e){ return null; }
  }

  async function fbLoad(){
    var entries = await fbFetch('');
    if(entries){
      fbCache[''] = entries; fbCache['.'] = entries;
      /* restore expanded dirs */
      var keys = Object.keys(fbExpanded);
      for(var i = 0; i < keys.length; i++){
        if(!fbCache[keys[i]]){
          var sub = await fbFetch(keys[i]);
          if(sub) fbCache[keys[i]] = sub;
        }
      }
      fbRender();
    }
    fbLoaded = true;
  }

  async function fbToggle(path){
    if(fbExpanded[path]){
      delete fbExpanded[path];
      fbRender();
    } else {
      fbExpanded[path] = true;
      if(!fbCache[path]){
        var ch = document.querySelector('.fb-children[data-parent="' + CSS.escape(path) + '"]');
        if(ch){ ch.classList.add('open'); ch.innerHTML = '<div class="fb-loading" style="padding:8px 16px">Loading\u2026</div>'; }
        var entries = await fbFetch(path);
        if(entries) fbCache[path] = entries;
        else delete fbExpanded[path];
      }
      fbRender();
    }
  }

  var fbCurrentPath = '';  /* track current file for Open in Dispatch */
  var fbCurrentMtime = 0;

  async function fbOpen(path, name, mtime){
    if(!fileSheetEl) return;
    fbFileName = name; fbFileText = ''; fbCurrentPath = path; fbCurrentMtime = mtime || 0;
    if(fsTitleEl) fsTitleEl.textContent = name;
    var dir = path.replace(/[/\\][^/\\]*$/,'') || '.';
    if(fsPathEl) fsPathEl.textContent = dir;
    if(fsLinesEl) fsLinesEl.textContent = '';
    if(fsMtimeEl) fsMtimeEl.textContent = mtime ? fmtFullTime(mtime) : '';
    if(fsCodeEl){ fsCodeEl.textContent = 'Loading\u2026'; fsCodeEl.className = ''; }
    /* E5: track recently viewed */
    fbRecentViewed = fbRecentViewed.filter(function(r){ return r.path !== path; });
    fbRecentViewed.unshift({path: path, name: name, mtime: mtime || 0});
    if(fbRecentViewed.length > 4) fbRecentViewed = fbRecentViewed.slice(0, 4);
    /* reset wrap state */
    fbWordWrap = false;
    if(fsWrapLabel) fsWrapLabel.textContent = 'Wrap';
    if(fsWrapBtn) fsWrapBtn.classList.remove('active');
    var codeBlock = fileSheetEl.querySelector('.file-sheet-code');
    if(codeBlock) codeBlock.style.whiteSpace = 'pre';

    var fsInner = fileSheetEl.querySelector('.file-sheet-inner');
    if(fsInner){ fsInner.style.transition = 'none'; fsInner.style.transform = 'translateY(100%)'; }
    fileSheetEl.classList.add('open');
    document.body.classList.add('sheet-open');
    fsSwipeEnabled = false;
    requestAnimationFrame(function(){ requestAnimationFrame(function(){
      if(fsInner){ fsInner.style.transition = 'transform 300ms cubic-bezier(0.16,1,0.3,1)'; fsInner.style.transform = 'translateY(0)'; }
      setTimeout(function(){ fsSwipeEnabled = true; if(fsInner){ fsInner.style.transition = ''; } }, 300);
    }); });
    try {
      var r = await fetch('/api/file?path=' + encodeURIComponent(path), {cache:'no-store'});
      if(!r.ok){ if(fsCodeEl) fsCodeEl.textContent = 'Failed to load file'; return; }
      var d = await r.json();
      if(d.error){ if(fsCodeEl) fsCodeEl.textContent = d.error; return; }
      if(!d.is_text){ if(fsCodeEl) fsCodeEl.textContent = 'Binary file (' + fmtSize(d.size) + ')\nCannot preview binary files.'; return; }
      fbFileText = d.content || '';
      /* E4: line count */
      var lineCount = fbFileText.split('\n').length;
      if(fsLinesEl) fsLinesEl.textContent = lineCount + ' lines';
      if(fsCodeEl){
        fsCodeEl.textContent = fbFileText;
        var lang = extLang(name);
        if(lang && window.hljs){
          fsCodeEl.className = 'language-' + lang;
          try { hljs.highlightElement(fsCodeEl); } catch(e){}
        }
      }
    } catch(e){ if(fsCodeEl) fsCodeEl.textContent = 'Network error loading file'; }
  }

  function fbCloseSheet(){
    if(fileSheetEl){
      fileSheetEl.classList.remove('open');
      var fsInner = fileSheetEl.querySelector('.file-sheet-inner');
      if(fsInner){ fsInner.style.transform = ''; fsInner.style.transition = ''; }
    }
    document.body.classList.remove('sheet-open');
    fsSwipeEnabled = false;
  }

  /* sheet events */
  if(fileSheetEl) fileSheetEl.addEventListener('click', function(e){ if(e.target === fileSheetEl) fbCloseSheet(); });
  if(fsCloseBtn) fsCloseBtn.addEventListener('click', fbCloseSheet);
  if(fsCopyBtn) fsCopyBtn.addEventListener('click', function(){
    if(fbFileText) copyToClipboard(fbFileText, fsCopyBtn);
    else toast('Nothing to copy','err');
  });
  if(fsDlBtn) fsDlBtn.addEventListener('click', function(){
    if(!fbFileText){ toast('Nothing to download','err'); return; }
    var fname = fbFileName || 'file.txt';

    if(isIOS){
      /* iOS PWA: Web Share API with File object → native share sheet (Save to Files, AirDrop, etc.) */
      if(navigator.canShare){
        var file = new File([fbFileText], fname, {type:'text/plain'});
        if(navigator.canShare({files:[file]})){
          navigator.share({files:[file]}).then(function(){
            toast('Saved to Files or shared','ok');
          }).catch(function(err){
            if(err.name === 'AbortError') toast('Share cancelled','');
            else toast('Share failed \u2014 try copying instead','err');
          });
        } else {
          toast('Sharing not supported \u2014 copy the file instead','err');
        }
      } else {
        toast('Sharing not supported \u2014 copy the file instead','err');
      }
    } else {
      /* Desktop / Android: hidden anchor download */
      var blob = new Blob([fbFileText], {type:'text/plain'});
      var url = URL.createObjectURL(blob);
      try {
        var a = document.createElement('a');
        a.href = url; a.download = fname;
        document.body.appendChild(a); a.click();
        setTimeout(function(){ URL.revokeObjectURL(url); document.body.removeChild(a); }, 200);
        toast('Download started','ok');
      } catch(e){
        URL.revokeObjectURL(url);
        toast('Download blocked \u2014 try copying instead','err');
      }
    }
  });

  /* E4: Word wrap toggle */
  if(fsWrapBtn) fsWrapBtn.addEventListener('click', function(){
    fbWordWrap = !fbWordWrap;
    if(fsWrapLabel) fsWrapLabel.textContent = fbWordWrap ? 'Nowrap' : 'Wrap';
    fsWrapBtn.classList.toggle('active', fbWordWrap);
    var codeBlock = fileSheetEl ? fileSheetEl.querySelector('.file-sheet-code') : null;
    if(codeBlock) codeBlock.style.whiteSpace = fbWordWrap ? 'pre-wrap' : 'pre';
  });

  /* E4: Open in Dispatch */
  if(fsDispatchBtn) fsDispatchBtn.addEventListener('click', function(){
    fbCloseSheet();
    switchView('jobs');
    if(promptEl){
      promptEl.value = 'File: ' + fbCurrentPath + '\n\n';
      promptEl.dispatchEvent(new Event('input'));
      if(charEl) charEl.textContent = promptEl.value.length + ' / 4000';
    }
    toast('File path added to prompt', 'ok');
  });

  /* E6: Legend toggle */
  if(fbLegendToggle) fbLegendToggle.addEventListener('click', function(){
    if(fbLegendEl) fbLegendEl.classList.toggle('open');
  });

  /* E5: Recent file click delegation */
  if(fbRecentEl) fbRecentEl.addEventListener('click', function(ev){
    var row = ev.target.closest('.fb-recent-row');
    if(!row) return;
    fbOpen(row.getAttribute('data-path'), row.getAttribute('data-name'), 0);
  });

  /* Swipe down to dismiss file sheet (Standard 2) — 120px threshold + velocity */
  (function(){
    if(!fileSheetEl) return;
    var inner = fileSheetEl.querySelector('.file-sheet-inner');
    var handle = fileSheetEl.querySelector('.ws-handle');
    var header = fileSheetEl.querySelector('.file-sheet-hd');
    if(!inner) return;
    var startY = 0, startTime = 0, dragging = false;

    function onStart(e){
      if(!fsSwipeEnabled) return;
      startY = e.touches[0].clientY; startTime = Date.now(); dragging = true;
      inner.style.transition = 'none';
    }
    function onMove(e){
      if(!dragging) return;
      var d = e.touches[0].clientY - startY;
      if(d > 0) inner.style.transform = 'translateY(' + d + 'px)';
    }
    function onEnd(e){
      if(!dragging) return; dragging = false;
      var d = e.changedTouches[0].clientY - startY;
      var elapsed = Date.now() - startTime;
      var fastFlick = elapsed < 150 && d > 60;
      var longDrag = d > 120;
      inner.style.transition = 'transform 280ms cubic-bezier(0.16,1,0.3,1)';
      if(fastFlick || longDrag){
        inner.style.transform = 'translateY(100%)';
        setTimeout(function(){ fbCloseSheet(); }, 280);
      } else {
        inner.style.transform = 'translateY(0)';
        setTimeout(function(){ inner.style.transition = ''; }, 280);
      }
    }

    [handle, header].forEach(function(el){
      if(!el) return;
      el.addEventListener('touchstart', onStart, {passive: true});
      el.addEventListener('touchmove', onMove, {passive: true});
      el.addEventListener('touchend', onEnd, {passive: true});
    });
  })();

  /* Escape closes file sheet (capture phase — fires before other handlers) */
  document.addEventListener('keydown', function(ev){
    if(ev.key === 'Escape' && fileSheetEl && fileSheetEl.classList.contains('open')){
      fbCloseSheet(); ev.stopImmediatePropagation();
    }
  }, true);

  /* tree row click */
  document.addEventListener('click', function(ev){
    var row = ev.target.closest('.fb-row');
    if(!row) return;
    if(row.getAttribute('data-dir') === '1') fbToggle(row.getAttribute('data-path'));
    else fbOpen(row.getAttribute('data-path'), row.getAttribute('data-name'), parseFloat(row.getAttribute('data-mtime')) || 0);
  });

  /* search */
  if(fbSearchEl){
    var fbSto = null;
    fbSearchEl.addEventListener('input', function(){
      clearTimeout(fbSto);
      fbSto = setTimeout(function(){ fbSearchQ = fbSearchEl.value.trim(); fbRender(); }, 150);
    });
  }

  /* active file indicator — extract file references from running jobs */
  function fbUpdateActiveFiles(){
    var nxt = new Set();
    allJobs.forEach(function(j){
      if(j.status !== 'running' && j.status !== 'pending') return;
      var p = j.prompt || '';
      var m = p.match(/[\w\-\.\/\\]+\.\w{1,10}/g);
      if(m) m.forEach(function(f){
        var c = f.replace(/\\/g,'/').replace(/^[./]+/,'');
        nxt.add(c.split('/').pop());
        nxt.add(c);
      });
    });
    var changed = nxt.size !== fbActiveSet.size;
    if(!changed) nxt.forEach(function(f){ if(!fbActiveSet.has(f)) changed = true; });
    if(changed){ fbActiveSet = nxt; if(fbLoaded) fbRender(); }
  }

  /* poll file tree every 10s */
  async function fbPoll(){
    var entries = await fbFetch('');
    if(!entries) return;
    var old = fbCache[''] || fbCache['.'] || [];
    var diff = entries.length !== old.length;
    if(!diff) for(var i = 0; i < entries.length; i++){
      if(!old[i] || entries[i].name !== old[i].name || entries[i].size !== old[i].size){ diff = true; break; }
    }
    if(diff){
      fbCache[''] = entries; fbCache['.'] = entries;
      var ep = Object.keys(fbExpanded);
      for(var j = 0; j < ep.length; j++){
        var sub = await fbFetch(ep[j]);
        if(sub) fbCache[ep[j]] = sub;
      }
      fbRender();
    }
  }

  /* lazy init: load tree on first Files tab visit */
  document.querySelectorAll('[data-nav="files"]').forEach(function(btn){
    btn.addEventListener('click', function(){
      if(!fbLoaded) fbLoad();
      if(!fbPollId) fbPollId = setInterval(fbPoll, 10000);
    });
  });

  /* =============================================================
     JOB DETAIL SHEET (shared: History + Recent)
     iOS Bottom Sheet Standard: Fix 1 (scroll lock) + Fix 2 (swipe)
     ============================================================= */
  var jobSheetEl = $('job-sheet');
  var jsBodyEl   = $('js-body');
  var jsTitleEl  = $('js-title');
  var jsMetaEl   = $('js-meta');
  var jsCloseBtn = $('js-close');
  var jsCopyBtn  = $('js-copy-output');
  var jsOutputText = '';
  var jsSwipeEnabled = false;

  function openJobSheet(){
    if(!jobSheetEl) return;
    jsSwipeEnabled = false;
    var inner = jobSheetEl.querySelector('.job-sheet-inner');
    if(inner){ inner.style.transition = 'none'; inner.style.transform = 'translateY(100%)'; }
    jobSheetEl.classList.add('open');
    document.body.classList.add('sheet-open'); /* Fix 1 */
    requestAnimationFrame(function(){ requestAnimationFrame(function(){
      if(inner){ inner.style.transition = 'transform 300ms cubic-bezier(0.16,1,0.3,1)'; inner.style.transform = 'translateY(0)'; }
      setTimeout(function(){ jsSwipeEnabled = true; if(inner){ inner.style.transition = ''; } }, 300);
    }); });
  }

  function closeJobSheet(){
    if(jobSheetEl){
      jobSheetEl.classList.remove('open');
      var inner = jobSheetEl.querySelector('.job-sheet-inner');
      if(inner){ inner.style.transform = ''; inner.style.transition = ''; }
    }
    document.body.classList.remove('sheet-open'); /* Fix 1 */
    jsSwipeEnabled = false;
    jsOutputText = '';
  }

  async function loadJobDetail(id){
    if(!jsBodyEl) return;
    jsBodyEl.innerHTML = '<div class="js-loading">Loading\u2026</div>';
    if(jsTitleEl) jsTitleEl.textContent = 'Job Detail';
    if(jsMetaEl) jsMetaEl.textContent = '';
    jsOutputText = '';
    openJobSheet();
    try {
      var r = await fetch('/api/jobs/' + id, {cache:'no-store'});
      if(!r.ok){ jsBodyEl.innerHTML = '<div class="js-loading">Failed to load job</div>'; return; }
      var j = await r.json();
      jsOutputText = j.output || '';
      if(jsTitleEl) jsTitleEl.textContent = (j.prompt || '').substring(0, 50) || 'Job Detail';
      if(jsMetaEl) jsMetaEl.textContent = esc(j.model || '') + ' \u00b7 ' + esc(j.effort || '') + ' \u00b7 ' + fmtTimeAgo(j.created_at);
      var durationStr = j.duration || '';
      if(!durationStr && j.created_at && j.finished_at){
        try { var ds = (new Date(j.finished_at).getTime() - new Date(j.created_at).getTime()) / 1000; durationStr = ds.toFixed(1) + 's'; } catch(e){}
      }
      var html = '';
      html += '<div class="js-field"><span class="js-label">Status</span><span class="js-value"><span class="' + dotClass(j.status) + '"></span>' + esc(j.status) + '</span></div>';
      html += '<div class="js-field"><span class="js-label">Worker</span><span class="js-value">' + esc(j.model || '\u2014') + '</span></div>';
      html += '<div class="js-field"><span class="js-label">Effort</span><span class="js-value">' + esc(j.effort || '\u2014') + '</span></div>';
      html += '<div class="js-field"><span class="js-label">Started</span><span class="js-value">' + esc(j.created_at || '\u2014') + '</span></div>';
      if(durationStr) html += '<div class="js-field"><span class="js-label">Duration</span><span class="js-value">' + esc(durationStr) + '</span></div>';
      if(j.input_tokens != null) html += '<div class="js-field"><span class="js-label">Tokens</span><span class="js-value">' + j.input_tokens + ' in / ' + (j.output_tokens || 0) + ' out</span></div>';
      html += '<div class="js-section"><div class="js-section-label">Prompt</div><pre class="js-pre">' + esc(j.prompt || '\u2014') + '</pre></div>';
      html += '<div class="js-section"><div class="js-section-label">Output</div><pre class="js-pre">' + esc(j.output || '(no output yet)') + '</pre></div>';
      jsBodyEl.innerHTML = html;
    } catch(e){
      jsBodyEl.innerHTML = '<div class="js-loading">Network error</div>';
    }
  }

  /* Close events */
  if(jobSheetEl) jobSheetEl.addEventListener('click', function(e){ if(e.target === jobSheetEl) closeJobSheet(); });
  if(jsCloseBtn) jsCloseBtn.addEventListener('click', closeJobSheet);
  if(jsCopyBtn) jsCopyBtn.addEventListener('click', function(){
    if(jsOutputText) copyToClipboard(jsOutputText, jsCopyBtn);
    else toast('No output to copy', 'err');
  });

  /* Escape closes job sheet */
  document.addEventListener('keydown', function(ev){
    if(ev.key === 'Escape' && jobSheetEl && jobSheetEl.classList.contains('open')){
      closeJobSheet(); ev.stopImmediatePropagation();
    }
  }, true);

  /* Fix 2: Swipe down to dismiss — 80px threshold + fast flick */
  (function(){
    if(!jobSheetEl) return;
    var inner  = jobSheetEl.querySelector('.job-sheet-inner');
    var handle = jobSheetEl.querySelector('.ws-handle');
    var header = jobSheetEl.querySelector('.job-sheet-hd');
    if(!inner) return;
    var startY = 0, startTime = 0, dragging = false;

    function onStart(e){
      if(!jsSwipeEnabled) return;
      startY = e.touches[0].clientY; startTime = Date.now(); dragging = true;
      inner.style.transition = 'none';
    }
    function onMove(e){
      if(!dragging) return;
      var d = e.touches[0].clientY - startY;
      if(d > 0) inner.style.transform = 'translateY(' + d + 'px)';
    }
    function onEnd(e){
      if(!dragging) return; dragging = false;
      var d = e.changedTouches[0].clientY - startY;
      var elapsed = Date.now() - startTime;
      var fastFlick = elapsed < 150 && d > 60;
      var longDrag = d > 80;
      inner.style.transition = 'transform 280ms cubic-bezier(0.16,1,0.3,1)';
      if(fastFlick || longDrag){
        inner.style.transform = 'translateY(100%)';
        setTimeout(function(){ closeJobSheet(); }, 280);
      } else {
        inner.style.transform = 'translateY(0)';
        setTimeout(function(){ inner.style.transition = ''; }, 280);
      }
    }

    [handle, header].forEach(function(el){
      if(!el) return;
      el.addEventListener('touchstart', onStart, {passive: true});
      el.addEventListener('touchmove', onMove, {passive: true});
      el.addEventListener('touchend', onEnd, {passive: true});
    });
  })();

  /* =============================================================
     HISTORY TAB — full job list with filter pills + 10s poll
     ============================================================= */
  var histJobs = [];
  var histFilter = 'all';
  var histPollId = null;
  var histLoaded = false;

  function histDotClass(s){
    if(s === 'done')    return 'hist-dot done';
    if(s === 'running') return 'hist-dot running';
    if(s === 'failed' || s === 'cancelled') return 'hist-dot failed';
    if(s === 'pending') return 'hist-dot pending';
    return 'hist-dot';
  }

  function histFiltered(){
    if(histFilter === 'all') return histJobs;
    if(histFilter === 'running') return histJobs.filter(function(j){ return LIVE_STATES.has(j.status); });
    if(histFilter === 'done') return histJobs.filter(function(j){ return j.status === 'done'; });
    if(histFilter === 'failed') return histJobs.filter(function(j){ return j.status === 'failed' || j.status === 'cancelled'; });
    return histJobs;
  }

  function renderHistory(){
    var listEl = $('hist-list');
    if(!listEl) return;
    var jobs = histFiltered();
    if(jobs.length === 0){
      listEl.innerHTML = '<div class="hist-empty">' + (histJobs.length === 0 ? 'No history yet' : 'No matching jobs') + '</div>';
      return;
    }
    listEl.innerHTML = jobs.map(function(j){
      var jid = j.id || j.job_id || '';
      var status = j.status || '';
      var promptText = esc((j.prompt || '(no prompt)').substring(0, 60));
      var worker = esc(j.model || 'Unknown');
      var ago = fmtTimeAgo(j.created_at);
      return '<div class="hist-row" data-job-id="' + esc(jid) + '">' +
        '<div class="' + histDotClass(status) + '"></div>' +
        '<div class="hist-info">' +
          '<div class="hist-prompt">' + promptText + '</div>' +
          '<div class="hist-meta">' + worker + (ago ? ' \u00b7 ' + ago : '') + '</div>' +
        '</div>' +
        '<span class="hist-status">' + esc(status) + '</span>' +
      '</div>';
    }).join('');
  }

  async function fetchHistory(){
    try {
      var r = await fetch('/api/history', {cache:'no-store'});
      if(!r.ok) return;
      var data = await r.json();
      histJobs = data.jobs || (Array.isArray(data) ? data : []);
      renderHistory();
    } catch(e){ /* silent */ }
    histLoaded = true;
  }

  /* filter chip clicks */
  document.querySelectorAll('.hist-chip').forEach(function(c){
    c.addEventListener('click', function(){
      histFilter = c.getAttribute('data-hfilter') || 'all';
      document.querySelectorAll('.hist-chip').forEach(function(b){
        var isActive = b === c;
        b.classList.toggle('active', isActive);
        b.setAttribute('aria-checked', isActive ? 'true' : 'false');
      });
      renderHistory();
    });
  });

  /* history row click → open job detail sheet */
  document.addEventListener('click', function(ev){
    var row = ev.target.closest('.hist-row[data-job-id]');
    if(!row) return;
    var jid = row.getAttribute('data-job-id');
    if(jid) loadJobDetail(jid);
  });

  /* start/stop polling based on active tab */
  function histStartPoll(){
    if(histPollId) clearInterval(histPollId);
    fetchHistory();
    histPollId = setInterval(fetchHistory, 10000);
  }
  function histStopPoll(){
    if(histPollId){ clearInterval(histPollId); histPollId = null; }
  }

  /* =============================================================
     INIT
     ============================================================= */
  /* Extend switchView to manage history polling */
  var origSwitchView = switchView;
  switchView = function(name){
    origSwitchView(name);
    if(name === 'history'){
      histStartPoll();
    } else {
      histStopPoll();
    }
  };

  icons();
  fetchJobs();
  fetchRecent();
  fetchStatsEndpoint();
  setInterval(fetchJobs, POLL_MS);
  setInterval(fetchStatsEndpoint, POLL_MS);
  setInterval(fbUpdateActiveFiles, POLL_MS);
})();
