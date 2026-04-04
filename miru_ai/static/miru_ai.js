(function () {
    const config = window.MIRU_AI_CONFIG || {};
    const RUN_API_PATH = "/api/run";
    let devMonitorIntervalId = 0;
    let pendingApprovalsInFlight = null;
    let latestRuntimeStatus = null;

    function getMainContent() {
        return document.getElementById("miruMainContent");
    }

    function getRunApiUrl() {
        return RUN_API_PATH;
    }

    config.runUrl = getRunApiUrl();

    function syncPageConfig() {
        const mainContent = getMainContent();
        if (!mainContent) {
            return;
        }
        if (mainContent.dataset.pageKey) {
            config.pageKey = mainContent.dataset.pageKey;
        }
        if (typeof mainContent.dataset.runDisabled === "string") {
            config.runDisabled = mainContent.dataset.runDisabled === "true";
        }
        config.runUrl = getRunApiUrl();
    }

    function resolveLearnerMode(payload) {
        if (!payload || typeof payload !== "object") {
            return "";
        }
        return String(
            (payload.learning_engine || {}).learner_mode
            ?? (payload.learner || {}).mode
            ?? (payload.learner || {}).learner_mode
            ?? payload.learner_mode
            ?? ""
        ).trim().toUpperCase();
    }

    function stopDevMonitorPolling() {
        if (devMonitorIntervalId) {
            window.clearInterval(devMonitorIntervalId);
            devMonitorIntervalId = 0;
        }
    }

    function setText(id, text) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = text || "";
        }
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function buildControlHealthMarkup(item) {
        const tone = escapeHtml(item && item.tone ? item.tone : "neutral");
        return `
            <article class="devControlHealthItem" data-health-key="${escapeHtml(item && item.key ? item.key : "")}">
                <div class="devControlHealthHead">
                    <span class="devMetricLabel">${escapeHtml(item && item.label ? item.label : "System")}</span>
                    <span class="statusPill statusPill--${tone}">${escapeHtml(item && item.status ? item.status : "INCONCLUSIVE")}</span>
                </div>
                <p>${escapeHtml(item && item.detail ? item.detail : "Waiting for live status.")}</p>
            </article>
        `;
    }

    function resolveControlHealthItems(payload) {
        const control = payload && payload.control_layer ? payload.control_layer : {};
        const items = Array.isArray(control.system_health)
            ? control.system_health.map((item) => ({ ...item }))
            : [];
        const runtime = latestRuntimeStatus || {};
        const checkedAt = runtime.checked_at ? formatTimeLA(runtime.checked_at) : "";
        const applyRuntimeObservation = (key, portLabel) => {
            const runtimeValue = runtime[portLabel];
            if (!runtimeValue) {
                return;
            }
            const index = items.findIndex((item) => item.key === key);
            const confirmed = runtimeValue === "ok";
            const detail = confirmed
                ? `${portLabel} answered the live runtime probe${checkedAt ? ` at ${checkedAt}` : ""}.`
                : `${portLabel} failed the live runtime probe${checkedAt ? ` at ${checkedAt}` : ""}.`;
            const nextValue = {
                key,
                label: key === "miru_ai" ? "18765 Dev surface" : "18080 Project Miru",
                status: confirmed ? "CONFIRMED WORKING" : "FAILED",
                tone: confirmed ? "good" : "warn",
                detail,
                source: "Observed from /api/runtime/status.",
            };
            if (index >= 0) {
                items[index] = { ...items[index], ...nextValue };
            } else {
                items.push(nextValue);
            }
        };
        applyRuntimeObservation("miru_ai", "18765");
        applyRuntimeObservation("project_miru", "18080");
        return items;
    }

    function summarizeControlHeadline(items) {
        const rows = Array.isArray(items) ? items : [];
        if (rows.some((item) => item.status === "FAILED")) {
            return { label: "FAILED", tone: "warn" };
        }
        if (rows.some((item) => item.status === "INCONCLUSIVE")) {
            return { label: "INCONCLUSIVE", tone: "neutral" };
        }
        return { label: "CONFIRMED WORKING", tone: "good" };
    }

    function buildControlSummaryText(payload, items, issues) {
        const control = payload && payload.control_layer ? payload.control_layer : {};
        if (control.summary) {
            return control.summary;
        }
        const issueHeadline = issues && issues.headline ? issues.headline : "No active issue surfaced";
        const itemStatus = (Array.isArray(items) ? items : []).map((item) => `${item.label}: ${item.status}`).join(". ");
        return `${itemStatus}. Latest concern: ${issueHeadline}.`;
    }

    function renderControlLayer(payload) {
        const control = payload && payload.control_layer ? payload.control_layer : {};
        const items = resolveControlHealthItems(payload);
        const issues = control.recent_issues || {};
        const sources = control.status_sources || {};
        const headline = summarizeControlHeadline(items);

        const summaryNode = document.getElementById("devControlLayerSummary");
        const healthHeadline = document.getElementById("devControlHealthHeadline");
        const healthList = document.getElementById("devControlHealthList");

        if (summaryNode) {
            summaryNode.textContent = buildControlSummaryText(payload, items, issues);
        }
        if (healthHeadline) {
            healthHeadline.textContent = headline.label;
            healthHeadline.className = `statusPill statusPill--${headline.tone}`;
        }
        if (healthList) {
            healthList.innerHTML = items.length
                ? items.map((item) => buildControlHealthMarkup(item)).join("")
                : '<p class="devValidationEmpty">Health status will appear after the first live refresh.</p>';
        }
        const healthSource = document.getElementById("devControlHealthSource");
        if (healthSource) {
            healthSource.textContent = sources.health || "Health source detail unavailable.";
        }
    }

    function flashButtonState(button, text) {
        if (!button) {
            return;
        }
        const original = button.dataset.originalLabel || button.textContent || "";
        if (!button.dataset.originalLabel) {
            button.dataset.originalLabel = original;
        }
        button.textContent = text;
        window.clearTimeout(button._resetLabelTimer);
        button._resetLabelTimer = window.setTimeout(() => {
            button.textContent = button.dataset.originalLabel || original;
        }, 1800);
    }

    function isDevCockpitMinimalSurface() {
        const main = document.getElementById("miruMainContent");
        return Boolean(main && main.getAttribute("data-page-key") === "dev");
    }

    function renderOperatorHandoff(payload) {
        const handoff = payload && payload.operator_handoff;
        const setText = (id, value) => {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = value == null || value === "" ? "—" : String(value);
            }
        };
        const ta = document.getElementById("devHandoffPromptText");
        const pill = document.getElementById("devHandoffStatePill");
        const resolveBtn = document.getElementById("devHandoffResolveBtn");
        const clearAckBtn = document.getElementById("devHandoffClearAckBtn");
        const resHint = document.getElementById("devHandoffResolutionHint");
        function hideHandoffResolutionUi() {
            if (resolveBtn) {
                resolveBtn.hidden = true;
            }
            if (clearAckBtn) {
                clearAckBtn.hidden = true;
            }
            if (resHint) {
                resHint.hidden = true;
                resHint.textContent = "";
            }
        }
        function updateHandoffResolutionUi(h) {
            if (!resolveBtn && !clearAckBtn && !resHint) {
                return;
            }
            const res = h && h.resolution;
            const showResolve = Boolean(h && h.has_active_handoff === true);
            const showClearAck = Boolean(
                res && res.operator_acknowledged_for_signature && res.underlying_need_still_present
            );
            if (resolveBtn) {
                resolveBtn.hidden = !showResolve;
            }
            if (clearAckBtn) {
                clearAckBtn.hidden = !showClearAck;
            }
            if (resHint) {
                if (showClearAck && res.resolved_at) {
                    resHint.hidden = false;
                    resHint.textContent =
                        "Handoff acknowledged at " +
                        res.resolved_at +
                        ". Self-report may still show a catalog gap until metrics change.";
                } else {
                    resHint.hidden = true;
                    resHint.textContent = "";
                }
            }
        }
        if (!handoff || typeof handoff !== "object") {
            setText("devHandoffWhat", "—");
            setText("devHandoffWhy", "—");
            setText("devHandoffWorker", "—");
            setText("devHandoffTarget", "—");
            if (ta) {
                ta.value = "";
            }
            if (pill) {
                pill.textContent = "No data";
                pill.className = "statusPill statusPill--neutral";
            }
            hideHandoffResolutionUi();
            return;
        }
        setText("devHandoffWhat", handoff.what_miru_needs);
        setText("devHandoffWhy", handoff.why);
        setText("devHandoffWorker", handoff.recommended_worker);
        setText("devHandoffTarget", handoff.target_environment);
        if (ta) {
            ta.value = handoff.prompt_text != null ? String(handoff.prompt_text) : "";
        }
        if (pill) {
            let active = false;
            if (typeof handoff.has_active_handoff === "boolean") {
                active = handoff.has_active_handoff;
            } else {
                active = handoff.state !== "clear";
            }
            pill.textContent = active ? "Active handoff" : "No active handoff";
            pill.className = "statusPill statusPill--" + (active ? "warn" : "good");
        }
        updateHandoffResolutionUi(handoff);
    }

    function formatTimeLA(str) {
        if (!str || String(str).trim() === "" || String(str).trim() === "—") return str;
        const s = String(str).trim();
        let iso = s;
        if (/^\d{4}-\d{2}-\d{2}\s+\d/.test(s)) {
            iso = s.replace(/\s+/, "T");
            if (!/[Z+-]/.test(iso)) iso += "Z";
        } else if (/^\d{4}-\d{2}-\d{2}T\d/.test(s) && s.indexOf("Z") < 0 && !/[+-]\d{2}/.test(s)) {
            iso = s + "Z";
        } else if (!/^\d{4}/.test(s)) {
            iso = s.replace(/\s+/, "T") + "Z";
        }
        const d = new Date(iso);
        if (isNaN(d.getTime())) return str;
        return d.toLocaleString("en-US", {
            timeZone: "America/Los_Angeles",
            dateStyle: "short",
            timeStyle: "short",
            hour12: true,
        });
    }

    function renderDevStatus(payload) {
        if (!payload) {
            return;
        }
        if (isDevCockpitMinimalSurface()) {
            const updatedAt = document.getElementById("devUpdatedAt");
            if (updatedAt) {
                updatedAt.textContent = payload.updated_at_display || payload.updated_at || "—";
            }
            const devEnv = payload.dev_environment || {};
            setText("devStripEnvironment", devEnv.environment || "—");
            setText("devStripRuntimeTarget", devEnv.runtime_target || "—");
            const osr = payload.operator_self_report || {};
            const met = osr.metrics || {};
            const covPct = met.coverage_pct != null ? Number(met.coverage_pct).toFixed(1) : "—";
            setText("devSnapshotCoveragePct", covPct === "—" ? "—" : covPct + "%");
            const covBar = document.getElementById("devSnapshotCovBarFill");
            if (covBar) {
                const w = Math.max(0, Math.min(100, Number(met.coverage_pct) || 0));
                covBar.style.width = (Number.isFinite(w) ? w : 0) + "%";
            }
            const total = met.cards_total != null ? String(met.cards_total) : "—";
            const withInsight = met.cards_with_any_insight != null ? String(met.cards_with_any_insight) : "—";
            setText("devSnapshotInsightLine", withInsight + " cards with insight out of " + total + " total");
            const strong =
                met.cards_with_strong_insight != null ? String(met.cards_with_strong_insight) : "—";
            setText("devSnapshotStrongLine", strong === "—" ? "—" : strong + " strong insights");
            const lis = payload.last_insight_sync || {};
            const at = lis.at || lis.synced_at || "";
            setText("devSnapshotLastSync", at ? "Last insight sync (UTC): " + String(at) : "Last insight sync: —");
            renderOperatorHandoff(payload);
            return;
        }
        const activity = payload.activity || {};
        const trainingSource = payload.training_progress || payload.training || {};
        const voyage = payload.voyage || (payload.training && payload.training.voyage) || {};
        const learningStageLabel = String(voyage.sea_label || "Early structured learning");
        const coverageClarification = "This measures verified card knowledge coverage, not Miru's full strategic intelligence.";
        const progressPercentValue = trainingSource.training_progress_percent ?? trainingSource.progress_percent ?? 0;
        const activityHero = document.getElementById("devActivityHero");
        const progressFill = document.getElementById("devProgressFill");
        if (activityHero) {
            activityHero.className = `devActivityHero devActivityHero--${activity.visual || "sleeping"}`;
        }
        const title = document.getElementById("devActivityTitle");
        const description = document.getElementById("devActivityDescription");
        const detail = document.getElementById("devActivityDetail");
        const updatedAt = document.getElementById("devUpdatedAt");
        const updatedAtLocal = document.getElementById("devUpdatedAtLocal");
        const progressPercent = document.getElementById("devProgressPercent");
        const progressSummary = document.getElementById("devProgressSummary");
        const progressDetail = document.getElementById("devProgressDetail");
        const progressStageLabel = document.getElementById("devProgressStageLabel");
        if (title) {
            title.textContent = activity.title || "Sleeping";
        }
        if (description) {
            description.textContent = activity.description || "Miru is idle and ready.";
        }
        if (detail) {
            detail.textContent = activity.detail || "Miru is waiting for the next question.";
        }
        if (updatedAt) {
            updatedAt.textContent = payload.updated_at_display || (payload.updated_at ? formatTimeLA(payload.updated_at) : "—");
        }
        if (updatedAtLocal) {
            updatedAtLocal.textContent = `Local time ${formatBrowserLocalTimestamp(new Date())}`;
        }
        const currentMode = resolveLearnerMode(payload) || "REVIEW_REQUIRED";
        const learningEngine = payload.learning_engine || {};
        const workerLastRun = payload.worker_last_run || {};
        const modeBadge = document.getElementById("devLearnerModeBadge");
        if (modeBadge) {
            modeBadge.textContent = currentMode;
            modeBadge.className = "devModeBadge devModeBadge--" + currentMode.toLowerCase().replace(/_/g, "");
        }
        const devEnv = payload.dev_environment || {};
        const stripEnvironment = document.getElementById("devStripEnvironment");
        const stripRuntimeTarget = document.getElementById("devStripRuntimeTarget");
        const stripLearnerState = document.getElementById("devStripLearnerState");
        const stripMiruStatus = document.getElementById("devStripMiruStatus");
        const stripWorkerStatus = document.getElementById("devStripWorkerStatus");
        const stripMode = document.getElementById("devStripMode");
        const stripHeartbeat = document.getElementById("devStripHeartbeat");
        const stripHeartbeatFreshness = document.getElementById("devStripHeartbeatFreshness");
        const stripQueue = document.getElementById("devStripQueue");
        if (stripEnvironment) {
            stripEnvironment.textContent = devEnv.environment || "â€”";
        }
        if (stripRuntimeTarget) {
            stripRuntimeTarget.textContent = devEnv.runtime_target || "â€”";
        }
        const learnerStateDisplay = learningEngine.learner_state_display;
        const learnerStateRaw = learningEngine.learner_state || "â€”";
        const queueLen = Number(learningEngine.queue_length || 0);
        const hasPid = learningEngine.learner_pid != null && String(learningEngine.learner_pid).trim() !== "";
        let simpleStatus = learnerStateDisplay || "â€”";
        if (simpleStatus === "â€”") {
            if (learnerStateRaw === "Running" || learnerStateRaw === "Starting") simpleStatus = "Learning";
            else if (learnerStateRaw === "Running (waiting)" || (learnerStateRaw === "Idle" && hasPid)) simpleStatus = "Waiting for work";
            else if (learnerStateRaw === "Idle") simpleStatus = queueLen === 0 ? "Idle" : "Idle, tasks waiting";
            else if (learnerStateRaw !== "â€”") simpleStatus = learnerStateRaw;
        }
        const currentStatusLine = document.getElementById("devCurrentStatusLine");
        if (currentStatusLine) {
            currentStatusLine.textContent = simpleStatus;
        }
        if (stripLearnerState) {
            stripLearnerState.textContent = simpleStatus;
        }
        const stripLearnerPid = document.getElementById("devStripLearnerPid");
        if (stripLearnerPid) {
            const pid = learningEngine.learner_pid;
            if (pid != null && pid !== "") {
                stripLearnerPid.textContent = "PID " + String(pid);
                stripLearnerPid.classList.remove("isHidden");
            } else {
                stripLearnerPid.textContent = "";
                stripLearnerPid.classList.add("isHidden");
            }
        }
        if (stripMiruStatus) {
            stripMiruStatus.textContent = simpleStatus !== "â€”" ? simpleStatus : (activity.title || "Sleeping");
        }
        if (stripWorkerStatus) {
            const wAction = workerLastRun.action;
            const wDisplay = workerLastRun.action_display;
            const wTime = workerLastRun.timestamp_display || (workerLastRun.timestamp ? formatTimeLA(workerLastRun.timestamp) : "");
            if (wAction && wAction !== "no_run_recorded") {
                stripWorkerStatus.textContent = (wDisplay || wAction) + (wTime ? " Â· " + wTime : "");
            } else {
                stripWorkerStatus.textContent = "â€”";
            }
        }
        if (stripMode) {
            stripMode.textContent = currentMode;
        }
        if (stripHeartbeat) {
            stripHeartbeat.textContent = formatTimeLA(learningEngine.last_heartbeat || "â€”");
        }
        if (stripHeartbeatFreshness) {
            const freshness = learningEngine.heartbeat_freshness || "â€”";
            stripHeartbeatFreshness.textContent = freshness !== "â€”" ? `(${freshness})` : "";
        }
        if (stripQueue) {
            stripQueue.textContent = `${Number(learningEngine.queue_length || 0)} waiting Â· ${Number(learningEngine.running_count || 0)} running`;
        }
        const snapIn = payload.snapshot_inputs || learningEngine.snapshot_inputs || {};
        const snapStrip = document.getElementById("devSnapshotStrip");
        const snapPill = document.getElementById("devSnapshotStripPill");
        const snapSummary = document.getElementById("devSnapshotStripSummary");
        if (snapStrip && snapPill && snapSummary) {
            const worst = String(snapIn.worst_status || "fresh").toLowerCase();
            const items = Array.isArray(snapIn.items) ? snapIn.items : [];
            if (!items.length) {
                snapStrip.classList.add("isHidden");
            } else {
                snapStrip.classList.remove("isHidden");
                snapStrip.classList.remove("devSnapshotStrip--good", "devSnapshotStrip--warn", "devSnapshotStrip--bad");
                let tone = "good";
                let pillLabel = "Fresh";
                if (worst === "missing") {
                    tone = "bad";
                    pillLabel = "Missing";
                } else if (worst === "stale") {
                    tone = "warn";
                    pillLabel = "Stale";
                } else if (worst === "aging") {
                    tone = "warn";
                    pillLabel = "Aging";
                }
                snapStrip.classList.add(`devSnapshotStrip--${tone}`);
                snapPill.textContent = pillLabel;
                snapPill.className = `statusPill statusPill--${tone === "good" ? "good" : "warn"}`;
                snapSummary.textContent = snapIn.summary || "";
            }
        }
        const stripDossiers = document.getElementById("devStripDossiers");
        if (stripDossiers) {
            stripDossiers.textContent = `${Number(learningEngine.dossier_verified_count || 0)} verified Â· ${Number(learningEngine.dossier_source_backed_count || 0)} source-backed`;
        }
        const workerLastRunWrap = document.getElementById("devWorkerLastRunWrap");
        const workerLastRunAction = document.getElementById("devWorkerLastRunAction");
        const workerLastRunTime = document.getElementById("devWorkerLastRunTime");
        const workerLastRunDetail = document.getElementById("devWorkerLastRunDetail");
        if (workerLastRunWrap) {
            const action = workerLastRun.action || "â€”";
            workerLastRunWrap.style.display = action === "no_run_recorded" ? "none" : "";
            if (workerLastRunAction) workerLastRunAction.textContent = workerLastRun.action_display || action;
            if (workerLastRunTime) workerLastRunTime.textContent = workerLastRun.timestamp_display || (workerLastRun.timestamp ? formatTimeLA(workerLastRun.timestamp) : "â€”");
            if (workerLastRunDetail) {
                const parts = [];
                if (workerLastRun.blocker) parts.push(workerLastRun.blocker);
                if (workerLastRun.no_new_work_reason) parts.push(workerLastRun.no_new_work_reason);
                if (workerLastRun.overlap_count != null) parts.push("overlap " + workerLastRun.overlap_count);
                if (workerLastRun.insight_count_after != null) parts.push(workerLastRun.insight_count_after + " insights");
                workerLastRunDetail.textContent = parts.join(" Â· ") || "â€”";
            }
        }
        const startLearnerBtn = document.getElementById("devStartLearnerBtn");
        const alreadyRunningHint = document.getElementById("devAlreadyRunningHint");
        const isLearnerRunning = learnerStateRaw === "Running" || learnerStateRaw === "Starting" || learnerStateRaw === "Running (waiting)";
        if (startLearnerBtn) {
            startLearnerBtn.disabled = isLearnerRunning;
        }
        if (alreadyRunningHint) {
            alreadyRunningHint.classList.toggle("isHidden", !isLearnerRunning);
        }
        const dossiersHint = document.getElementById("devDossiersHint");
        if (dossiersHint && payload.learning_engine) {
            const v = Number(payload.learning_engine.dossier_verified_count || 0);
            const s = Number(payload.learning_engine.dossier_source_backed_count || 0);
            if (v > 0 || s > 0) {
                dossiersHint.textContent = "Dossiers: " + v + " verified, " + s + " source-backed.";
                dossiersHint.classList.remove("isHidden");
            } else {
                dossiersHint.textContent = "";
                dossiersHint.classList.add("isHidden");
            }
        }
        const lastSyncHint = document.getElementById("devLastSyncHint");
        if (lastSyncHint && payload.last_insight_sync) {
            const sync = payload.last_insight_sync;
            if (sync.at) {
                const trigger = sync.trigger === "manual" ? " (manual)" : (sync.trigger === "after_stop" ? " (after stop)" : "");
                const atLA = formatTimeLA(sync.at);
                lastSyncHint.textContent = "Sync complete: " + Number(sync.synced_cards || 0) + " cards, " + Number(sync.inserted_insights || 0) + " inserted, " + Number(sync.replaced_insights || 0) + " replaced Â· " + atLA + trigger;
            } else {
                lastSyncHint.textContent = "Last insight sync: â€” (runs after Stop Learner; or use Sync Insights)";
            }
        }
        const worktreeBlock = document.getElementById("devWorktreeUpdateBlock");
        const worktreeSummary = payload.worktree_update_summary;
        if (worktreeBlock) {
            if (worktreeSummary && worktreeSummary.show) {
                worktreeBlock.classList.remove("isHidden");
                const msgEl = document.getElementById("devWorktreeUpdateMessage");
                const titleEl = document.getElementById("devWorktreeUpdateTitle");
                const pillEl = document.getElementById("devWorktreeUpdatePill");
                const countsEl = document.getElementById("devWorktreeUpdateCounts");
                const listEl = document.getElementById("devWorktreeUpdateList");
                if (msgEl) msgEl.textContent = worktreeSummary.message || "No verified updates on worktree site yet.";
                if (titleEl) titleEl.textContent = worktreeSummary.awaiting_review ? "Awaiting review" : (worktreeSummary.status === "updated" ? "Updated" : "Update status");
                if (pillEl) {
                    pillEl.textContent = worktreeSummary.awaiting_review ? "Review" : (worktreeSummary.status === "updated" ? "Updated" : "â€”");
                    pillEl.className = "statusPill statusPill--" + (worktreeSummary.awaiting_review ? "warn" : (worktreeSummary.status === "updated" ? "good" : "neutral"));
                }
                if (countsEl) {
                    const parts = [
                        `${Number(worktreeSummary.dossier_count || 0)} verified dossiers`,
                        `${Number(worktreeSummary.cards_updated || 0)} recent cards on worktree site`,
                    ];
                    if (Number(worktreeSummary.review_count || 0) > 0) parts.push(`${worktreeSummary.review_count} awaiting review`);
                    countsEl.innerHTML = parts.map((p) => `<span class="devWorktreeUpdateCountItem">${escapeHtml(p)}</span>`).join("");
                }
                const additions = worktreeSummary.recent_additions || [];
                if (listEl) {
                    listEl.classList.toggle("isHidden", additions.length === 0);
                    listEl.innerHTML = additions.slice(0, 6).map((item) => {
                        const code = escapeHtml(String(item.card_code || ""));
                        const name = escapeHtml(String(item.card_name || "Unknown card"));
                        const ts = escapeHtml(formatTimeLA(item.verified_at || ""));
                        return `<li class="devWorktreeUpdateListItem">${code} Â· ${name} <span class="devWorktreeUpdateTime">${ts}</span></li>`;
                    }).join("");
                }
            } else {
                worktreeBlock.classList.add("isHidden");
            }
        }
        const modeToDataMode = { DRY_RUN: "DRY_RUN", SANDBOX: "SANDBOX", REVIEW_REQUIRED: "REVIEW_REQUIRED", ACTIVE: null };
        const activeDataMode = modeToDataMode[currentMode] || null;
        document.querySelectorAll(".devControlButton[data-dev-action='set-mode']").forEach((btn) => {
            const btnMode = (btn.getAttribute("data-mode") || "").trim().toUpperCase();
            btn.classList.toggle("devControlButton--active", activeDataMode !== null && btnMode === activeDataMode);
        });
        const startHint = document.getElementById("devStartLearnerHint");
        if (startHint) {
            startHint.textContent = "Start runs in " + currentMode + " mode.";
        }
        const liveActivityTitle = document.getElementById("devLiveActivityTitle");
        const liveActivityDetail = document.getElementById("devLiveActivityDetail");
        const overviewStatePill = document.getElementById("devOverviewStatePill");
        if (liveActivityTitle) {
            liveActivityTitle.textContent = simpleStatus !== "â€”" ? simpleStatus : (activity.title || "Sleeping");
        }
        if (overviewStatePill) {
            overviewStatePill.textContent = simpleStatus !== "â€”" ? simpleStatus : (activity.title || "Sleeping");
        }
        if (liveActivityDetail) {
            liveActivityDetail.textContent = activity.detail || activity.description || "Miru is waiting for the next question.";
        }
        const activityFeedList = document.getElementById("devActivityFeedList");
        if (activityFeedList) {
            const feed = payload.activity_feed || payload.monitor?.recent_activity || [];
            const items = Array.isArray(feed) ? feed.slice(0, 8) : [];
            activityFeedList.innerHTML = items.length
                ? items.map((item) => {
                    const tone = item.tone || "neutral";
                    const title = item.title || "Event";
                    const detail = item.detail || "";
                    const ts = item.timestamp || "";
                    const tsLA = ts ? formatTimeLA(ts) : "";
                    return `<div class="devActivityFeedItem devActivityFeedItem--${tone}" data-timestamp="${String(ts).replace(/"/g, "&quot;")}"><span class="devActivityFeedTitleText">${escapeHtml(title)}</span><span class="devActivityFeedDetail">${escapeHtml(detail)}</span><span class="devActivityFeedTime">${escapeHtml(tsLA)}</span></div>`;
                }).join("")
                : '<p class="devActivityFeedEmpty">No recent activity.</p>';
        }
        if (progressPercent) {
            progressPercent.textContent = `${Number(progressPercentValue).toFixed(1)}%`;
        }
        if (progressSummary) {
            progressSummary.textContent = trainingSource.summary ?? trainingSource.verified_summary ?? "Training summary unavailable.";
        }
        if (progressDetail) {
            progressDetail.textContent = coverageClarification;
        }
        if (progressStageLabel) {
            progressStageLabel.textContent = `Current learning stage: ${learningStageLabel}`;
        }
        if (progressFill) {
            progressFill.style.width = `${Math.max(0, Math.min(Number(progressPercentValue), 100))}%`;
        }
        document.querySelectorAll(".devStateCard[data-activity-key]").forEach((card) => {
            card.classList.toggle("isCurrent", card.dataset.activityKey === activity.key);
        });
        renderPushoverStatus(payload.pushover || {});
        renderIntelligenceStatus(payload.intelligence_status || {});
        renderOperatorHandoff(payload);
        renderControlLayer(payload);
    }

    function formatBrowserLocalTimestamp(date) {
        const value = date instanceof Date ? date : new Date(date);
        if (Number.isNaN(value.getTime())) {
            return "unavailable";
        }
        try {
            return new Intl.DateTimeFormat(undefined, {
                dateStyle: "medium",
                timeStyle: "medium",
                timeZoneName: "short",
            }).format(value);
        } catch (error) {
            return value.toLocaleString();
        }
    }

    function renderPushoverStatus(pushover) {
        const status = document.getElementById("devPushoverStatus");
        const detail = document.getElementById("devPushoverDetail");
        const items = document.getElementById("devPushoverItems");
        const card = document.querySelector('.devIssueCard[data-issue-key="pushover"]');
        if (!status || !detail || !items || !card) {
            return;
        }
        const enabled = Boolean(pushover && pushover.enabled);
        const configured = Boolean(pushover && pushover.configured);
        const missingKeys = Array.isArray(pushover && pushover.missing_required_keys) ? pushover.missing_required_keys : [];
        const tone = enabled && configured ? "good" : (!enabled ? "neutral" : "warn");
        const label = enabled && configured ? "Ready" : (!enabled ? "Disabled" : "Needs attention");
        status.textContent = label;
        status.className = `statusPill statusPill--${tone}`;
        card.className = `panelCard devIssueCard devIssueCard--${tone}`;
        if (enabled && configured) {
            detail.textContent = "Notifications are enabled and the live server can use the configured local credentials.";
        } else if (!enabled) {
            detail.textContent = "Pushover notifications are currently disabled.";
        } else {
            detail.textContent = "Pushover is enabled but still missing required credentials.";
        }
        const envPath = pushover.env_path || "Unavailable";
        const defaultPriority = String((pushover && pushover.default_priority) || "0") || "0";
        const availabilityText = missingKeys.length
            ? `Missing keys: ${missingKeys.join(", ")}`
            : "Required keys are present.";
        items.innerHTML = [
            `<li>Env file: ${escapeHtml(envPath)}</li>`,
            `<li>Default priority: ${escapeHtml(defaultPriority)}</li>`,
            `<li>${escapeHtml(availabilityText)}</li>`,
        ].join("");
    }

    function renderIntelligenceStatus(intelligence) {
        const payload = intelligence || {};
        const worker = payload.worker || {};
        const queue = payload.queue || {};
        const lastMeaningful = payload.last_meaningful_activity || {};
        const pushover = payload.pushover || {};
        const stages = Array.isArray(payload.stages) ? payload.stages : [];
        const coverages = Array.isArray(payload.coverages) ? payload.coverages : [];

        const workerPill = document.getElementById("devIntelligenceWorkerPill");
        const sentence = document.getElementById("devIntelligenceSentence");
        const hint = document.getElementById("devIntelligenceHint");
        const workerState = document.getElementById("devIntelligenceWorkerState");
        const workerDetail = document.getElementById("devIntelligenceWorkerDetail");
        const queueSummary = document.getElementById("devIntelligenceQueueSummary");
        const queueDetail = document.getElementById("devIntelligenceQueueDetail");
        const lastMeaningfulNode = document.getElementById("devIntelligenceLastMeaningful");
        const lastMeaningfulDetail = document.getElementById("devIntelligenceLastMeaningfulDetail");
        const pushoverState = document.getElementById("devIntelligencePushoverState");
        const pushoverDetail = document.getElementById("devIntelligencePushoverDetail");
        const coverageGrid = document.getElementById("devIntelligenceCoverageGrid");
        const stageList = document.getElementById("devIntelligenceStageList");

        if (workerPill) {
            workerPill.textContent = worker.label || "Idle";
            workerPill.className = `statusPill statusPill--${worker.tone || "neutral"}`;
        }
        if (sentence) {
            sentence.textContent = payload.status_sentence || "Miru is idle. No learning tasks are waiting.";
        }
        if (hint) {
            hint.textContent = payload.activity_hint || "";
        }
        if (workerState) {
            workerState.textContent = worker.label || "Idle";
        }
        if (workerDetail) {
            workerDetail.textContent = worker.detail || "Worker status detail unavailable.";
        }
        if (queueSummary) {
            queueSummary.textContent = queue.summary || "0 pending, 0 running, 0 completed";
        }
        if (queueDetail) {
            queueDetail.textContent = queue.detail || "No queue detail yet.";
        }
        if (lastMeaningfulNode) {
            lastMeaningfulNode.textContent = lastMeaningful.label || "Unavailable";
        }
        if (lastMeaningfulDetail) {
            lastMeaningfulDetail.textContent = lastMeaningful.detail || "No recent meaningful learning signal is visible.";
        }
        if (pushoverState) {
            pushoverState.textContent = pushover.label || "Quiet because idle";
        }
        if (pushoverDetail) {
            pushoverDetail.textContent = pushover.detail || "Pushover state detail unavailable.";
        }
        if (coverageGrid) {
            coverageGrid.innerHTML = coverages.length
                ? coverages.map((item) => `
                    <article class="devMonitorListItem">
                        <span class="devMetricLabel">${escapeHtml(item.label || "")}</span>
                        <strong>${escapeHtml(item.value || "")}</strong>
                        <p>${escapeHtml(item.detail || "")}</p>
                    </article>
                `).join("")
                : '<p class="devValidationEmpty">Coverage visibility is unavailable right now.</p>';
        }
        if (stageList) {
            stageList.innerHTML = stages.length
                ? stages.map((item) => `
                    <article class="devMonitorListItem devIntelligenceStageItem">
                        <div class="devIntelligenceStageHead">
                            <span class="devMetricLabel">${escapeHtml(item.label || "")}</span>
                            <span class="statusPill statusPill--${escapeHtml(item.tone || "neutral")}">${escapeHtml(item.status || "")}</span>
                        </div>
                        <p>${escapeHtml(item.detail || "")}</p>
                    </article>
                `).join("")
                : '<p class="devValidationEmpty">Stage visibility is unavailable right now.</p>';
        }
    }

    function initializeDevMonitor() {
        stopDevMonitorPolling();
        const root = document.getElementById("devMonitor");
        if (!root) {
            return;
        }
        const devRefreshButtons = (function collectDevRefreshButtons() {
            const list = Array.from(root.querySelectorAll('[data-dev-refresh="status"]'));
            const legacy = document.getElementById("devRefreshButton");
            if (!list.length && legacy && root.contains(legacy)) {
                list.push(legacy);
            }
            return list;
        })();
        const apiUrl = (root.dataset.devStatusUrl || config.devStatusUrl || "/api/dev-status").trim() || "/api/dev-status";
        const mainContentEl = document.getElementById("miruMainContent");
        const pageKeyForDev = (mainContentEl && mainContentEl.getAttribute("data-page-key")) || "";
        const isDevCockpitPage = pageKeyForDev === "dev";
        const summaryUrl = apiUrl
            ? `${apiUrl}${apiUrl.includes("?") ? "&" : "?"}view=summary${isDevCockpitPage ? "&surface=cockpit" : ""}`
            : "";
        const devControlFeedback = document.getElementById("devControlFeedback");
        const leanRefresh = Boolean(
            (window.matchMedia && window.matchMedia("(max-width: 820px)").matches)
            || (navigator.connection && navigator.connection.saveData)
        );
        const monitorRefreshMs = isDevCockpitPage
            ? (leanRefresh ? 60000 : 45000)
            : (leanRefresh ? 30000 : 20000);
        if (!apiUrl) {
            return;
        }

        let lastDevStatusPayload = null;

        function formatTimeLA(str) {
            if (!str || String(str).trim() === "" || String(str).trim() === "â€”") return str;
            const s = String(str).trim();
            let iso = s;
            if (/^\d{4}-\d{2}-\d{2}\s+\d/.test(s)) {
                iso = s.replace(/\s+/, "T");
                if (!/[Z+-]/.test(iso)) iso += "Z";
            } else if (/^\d{4}-\d{2}-\d{2}T\d/.test(s) && s.indexOf("Z") < 0 && !/[+-]\d{2}/.test(s)) {
                iso = s + "Z";
            } else if (!/^\d{4}/.test(s)) {
                iso = s.replace(/\s+/, "T") + "Z";
            }
            const d = new Date(iso);
            if (isNaN(d.getTime())) return str;
            return d.toLocaleString("en-US", {
                timeZone: "America/Los_Angeles",
                dateStyle: "short",
                timeStyle: "short",
                hour12: true,
            });
        }

        function mergeLearnerModeIntoPayload(payload, mode) {
            if (!mode) return payload;
            const m = String(mode).trim().toUpperCase();
            if (!payload) {
                return { learning_engine: { learner_mode: m }, learner: { mode: m, learner_mode: m } };
            }
            return {
                ...payload,
                learning_engine: { ...(payload.learning_engine || {}), learner_mode: m },
                learner: { ...(payload.learner || {}), mode: m, learner_mode: m },
            };
        }

        function setDevControlFeedback(text, isError) {
            if (!devControlFeedback) {
                return;
            }
            devControlFeedback.textContent = text || "";
            devControlFeedback.className = "devControlFeedback " + (isError ? "devControlFeedback--error" : "");
            if (text) {
                window.clearTimeout(devControlFeedback._clearTimer);
                devControlFeedback._clearTimer = window.setTimeout(() => {
                    devControlFeedback.textContent = "";
                    devControlFeedback.className = "devControlFeedback";
                }, 6000);
            }
        }

        let lastDevControlHandled = { btn: null, at: 0 };
        const DEV_CONTROL_DEBOUNCE_MS = 600;

        function getDevControlTarget(event) {
            const rawTarget = event && event.target;
            if (!rawTarget) {
                return null;
            }
            const target = rawTarget.nodeType === 1 ? rawTarget : rawTarget.parentElement;
            if (!target || typeof target.closest !== "function") {
                return null;
            }
            return target.closest(".devControlButton[data-dev-action], .devControlButton[data-action]");
        }

        function handleDevControlClick(event) {
            const btn = getDevControlTarget(event);
            if (!btn) {
                return;
            }
            if (btn.disabled) {
                return;
            }
            const now = Date.now();
            if (lastDevControlHandled.btn === btn && (now - lastDevControlHandled.at) < DEV_CONTROL_DEBOUNCE_MS) {
                if (event && typeof event.preventDefault === "function" && event.cancelable) {
                    event.preventDefault();
                }
                return;
            }
            lastDevControlHandled = { btn: btn, at: now };
            const action = btn.getAttribute("data-dev-action") || btn.getAttribute("data-action");
            const mode = btn.getAttribute("data-mode");
            if (!action) {
                return;
            }
            if (typeof console !== "undefined" && console.log) {
                console.log("[dev] control button detected:", action, mode || "");
            }
            (async () => {
                btn.disabled = true;
                setDevControlFeedback(
                    action === "set-mode" && mode
                        ? `Tapped ${mode}. Sending requestâ€¦`
                        : "Tap detected. Sending requestâ€¦",
                    false,
                );
                const url = action === "set-mode" ? "/api/dev/set-learner-mode" : `/api/dev/${action}`;
                const body = action === "set-mode" && mode ? JSON.stringify({ mode }) : "{}";
                if (typeof console !== "undefined" && console.log) {
                    console.log("[dev] fetch start:", url);
                }
                try {
                    const response = await fetch(url, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            Accept: "application/json",
                            "X-Requested-With": "miru-client-nav",
                        },
                        credentials: "same-origin",
                        body: body,
                    });
                    const data = await response.json().catch(() => ({}));
                    const isSuccess = response.ok && data.ok !== false;
                    const alreadyRunning = !!data.already_running;
                    let message = data.message || data.error || (response.ok ? "Done." : `Request failed (${response.status}).`);
                    if (action === "set-mode" && isSuccess && mode) {
                        const confirmMsg = {
                            DRY_RUN: "Dry Run enabled â€“ Miru will simulate learning without publishing.",
                            SANDBOX: "Sandbox mode enabled â€“ Miru can verify data but will not publish.",
                            REVIEW_REQUIRED: "Review mode enabled â€“ Miru will queue items for approval.",
                        }[mode] || message;
                        message = confirmMsg;
                    }
                    if (!isSuccess && (data.error || data.message)) {
                        message = (data.error || data.message) + (response.status ? ` (${response.status})` : "");
                    }
                    if (alreadyRunning) {
                        message = data.message || "Learner is already running. Duplicate start blocked.";
                    }
                    if (action === "stop-learner" && isSuccess && data.insight_sync && data.insight_sync.ran) {
                        const sync = data.insight_sync;
                        if (sync.error) {
                            message = (message || "Learner stopped.") + " Insight sync error: " + sync.error;
                        } else {
                            message = (message || "Learner stopped.") + " Insight sync: " + Number(sync.synced_cards || 0) + " cards, " + Number(sync.inserted_insights || 0) + " inserted, " + Number(sync.replaced_insights || 0) + " replaced.";
                        }
                    }
                    if (action === "sync-insights" && data.insight_sync) {
                        const sync = data.insight_sync;
                        if (sync.error) {
                            message = "Sync error: " + sync.error;
                        } else {
                            message = "Insight sync: " + Number(sync.synced_cards || 0) + " cards, " + Number(sync.inserted_insights || 0) + " inserted, " + Number(sync.replaced_insights || 0) + " replaced.";
                        }
                    }
                    if (action === "seed-review-task" && isSuccess && data.message) {
                        message = data.added_to_review_queue
                            ? (data.message + " Check Pending Approvals.")
                            : data.message;
                    }
                    setDevControlFeedback(message, !isSuccess && !alreadyRunning);
                    const alreadyRunningHintEl = document.getElementById("devAlreadyRunningHint");
                    if (alreadyRunningHintEl) {
                        alreadyRunningHintEl.classList.toggle("isHidden", !alreadyRunning);
                    }
                    if (alreadyRunning) {
                        void loadDevStatus({ manual: true, lightweight: true });
                    }
                    if (action === "set-mode" && isSuccess) {
                        const resolvedMode = resolveLearnerMode(data);
                        if (resolvedMode) {
                            if (typeof console !== "undefined" && console.log) {
                                console.log("[dev] set-learner-mode resolved:", resolvedMode);
                            }
                            const merged = mergeLearnerModeIntoPayload(lastDevStatusPayload, resolvedMode);
                            if (merged) {
                                renderDevStatus(merged);
                            }
                        }
                    }
                    if ((action === "refresh-status" || action === "set-mode" || action === "start-learner" || action === "stop-learner" || action === "restart" || action === "sync-insights" || action === "seed-review-task") && isSuccess) {
                        const delayMs = (action === "start-learner" || action === "stop-learner" || action === "restart") ? 450 : 0;
                        if (delayMs > 0) {
                            window.setTimeout(function () {
                                void loadDevStatus({ manual: true, lightweight: true });
                            }, delayMs);
                        } else {
                            void loadDevStatus({ manual: true, lightweight: true });
                        }
                        if (action === "seed-review-task" && typeof loadPendingApprovals === "function") {
                            void loadPendingApprovals();
                        }
                    }
                } catch (err) {
                    setDevControlFeedback(err && err.message ? err.message : "Request failed.", true);
                } finally {
                    btn.disabled = false;
                }
            })();
        }

        root.addEventListener("click", handleDevControlClick, true);
        root.addEventListener("pointerup", function (e) {
            const btn = getDevControlTarget(e);
            if (btn) {
                handleDevControlClick(e);
            }
        }, true);
        root.addEventListener("touchend", function (e) {
            const btn = getDevControlTarget(e);
            if (btn) {
                handleDevControlClick(e);
            }
        }, { passive: true, capture: true });

        function shouldRefreshDevMonitor() {
            return !(document.visibilityState && document.visibilityState !== "visible");
        }

        function scheduleNonCriticalDevLoads() {
            if (isDevCockpitPage) {
                return;
            }
            const run = () => {
                if (!shouldRefreshDevMonitor()) {
                    return;
                }
                void loadPendingApprovals();
                void loadTaskQueue();
            };
            if (typeof window.requestIdleCallback === "function") {
                window.requestIdleCallback(run, { timeout: leanRefresh ? 900 : 500 });
                return;
            }
            window.setTimeout(run, leanRefresh ? 260 : 140);
        }

        async function loadDevStatus({ manual = false, lightweight = false } = {}) {
            if (manual) {
                devRefreshButtons.forEach((btn) => {
                    btn.disabled = true;
                    btn.classList.add("is-loading");
                });
            }
            try {
                const response = await fetch(lightweight ? summaryUrl : apiUrl, {
                    headers: {
                        Accept: "application/json",
                        "X-Requested-With": "miru-client-nav",
                    },
                    credentials: "same-origin",
                });
                if (!response.ok) {
                    throw new Error(`Dev status failed with ${response.status}`);
                }
                const payload = await response.json();
                lastDevStatusPayload = payload;
                renderDevStatus(payload);
            } catch (error) {
                const updatedAt = document.getElementById("devUpdatedAt");
                if (updatedAt) {
                    updatedAt.textContent = "Live data unavailable";
                }
            } finally {
                devRefreshButtons.forEach((btn) => {
                    btn.disabled = false;
                    btn.classList.remove("is-loading");
                });
            }
        }

        let taskQueueInFlight = null;
        function normalizeTaskStatus(raw) {
            const v = String(raw || "").trim().toLowerCase();
            if (v === "queued") return "queued";
            if (v === "in progress" || v === "in_progress" || v === "inprogress") return "in_progress";
            if (v === "done" || v === "complete" || v === "completed") return "done";
            if (v === "failed" || v === "error") return "failed";
            return "queued";
        }

        function formatTaskStatusLabel(status) {
            const v = normalizeTaskStatus(status);
            if (v === "in_progress") return "In Progress";
            if (v === "done") return "Done";
            if (v === "failed") return "Failed";
            return "Queued";
        }

        function formatTaskTimestamp(raw) {
            const s = String(raw || "").trim();
            if (!s) return "—";
            const d = new Date(s);
            if (Number.isNaN(d.getTime())) return s;
            return d.toLocaleString("en-US", {
                timeZone: "America/Los_Angeles",
                dateStyle: "short",
                timeStyle: "short",
                hour12: true,
            });
        }

        function renderTaskQueue(items) {
            const listEl = document.getElementById("devTaskQueueList");
            if (!listEl) {
                return;
            }
            const emptyEl = document.getElementById("devTaskQueueEmpty");
            const feedbackEl = document.getElementById("devTaskQueueFeedback");
            if (feedbackEl && !feedbackEl.dataset.sticky) {
                feedbackEl.textContent = "";
            }
            const list = Array.isArray(items) ? items : [];

            listEl.querySelectorAll(".devTaskQueueItem").forEach((el) => el.remove());
            if (!list.length) {
                if (emptyEl) {
                    emptyEl.textContent = "No queued tasks.";
                    emptyEl.classList.remove("isHidden");
                }
                listEl.setAttribute("aria-busy", "false");
                return;
            }
            if (emptyEl) {
                emptyEl.classList.add("isHidden");
            }

            function esc(s) {
                return String(s)
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;");
            }

            const html = list
                .map((row) => {
                    const id = String(row.task_id || row.id || "");
                    const label = String(row.label || "").trim() || "(untitled)";
                    const scope = String(row.scope || "").trim();
                    const status = normalizeTaskStatus(row.status);
                    const createdAt = row.created_at || row.timestamp || "";
                    const stamp = formatTaskTimestamp(createdAt);
                    const statusLabel = formatTaskStatusLabel(status);
                    const tone = status === "done" ? "good" : status === "failed" ? "warn" : status === "in_progress" ? "warn" : "neutral";
                    return `
                    <article class="devTaskQueueItem" data-task-id="${esc(id)}" data-status="${esc(status)}" role="button" tabindex="0" aria-label="Update task status">
                        <div class="devTaskQueueItemTop">
                            <div class="devTaskQueueItemLabel">${esc(label)}</div>
                            <div class="devTaskQueueItemStatus"><span class="statusPill statusPill--${tone}">${esc(statusLabel)}</span></div>
                        </div>
                        <div class="devTaskQueueItemMeta">
                            <span class="devTaskQueueItemScope">${esc(scope || "—")}</span>
                            <span class="devTaskQueueItemTime">${esc(stamp)}</span>
                        </div>
                    </article>`;
                })
                .join("");

            if (emptyEl) {
                emptyEl.insertAdjacentHTML("beforebegin", html);
            } else {
                listEl.insertAdjacentHTML("beforeend", html);
            }
            listEl.setAttribute("aria-busy", "false");
        }

        async function loadTaskQueue() {
            const listEl = document.getElementById("devTaskQueueList");
            if (!listEl) {
                return;
            }
            if (taskQueueInFlight) {
                return taskQueueInFlight;
            }
            listEl.setAttribute("aria-busy", "true");
            const emptyEl = document.getElementById("devTaskQueueEmpty");
            if (emptyEl) {
                emptyEl.textContent = "Loading tasks…";
                emptyEl.classList.remove("isHidden");
            }
            const timeoutMs = 12000;
            const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
            const timeoutId = ac ? window.setTimeout(() => ac.abort(), timeoutMs) : null;
            const req = (async () => {
                try {
                    const r = await fetch("/api/dev/tasks", {
                        headers: { Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                        credentials: "same-origin",
                        signal: ac ? ac.signal : undefined,
                    });
                    const data = await r.json().catch(() => ({}));
                    if (!r.ok || data.ok === false) {
                        throw new Error(data.error || "Tasks request failed");
                    }
                    renderTaskQueue(Array.isArray(data.items) ? data.items : []);
                } catch (e) {
                    renderTaskQueue([]);
                    const feedbackEl = document.getElementById("devTaskQueueFeedback");
                    if (feedbackEl) {
                        feedbackEl.textContent = e && e.name === "AbortError" ? "Task queue timed out." : "Task queue unavailable.";
                    }
                } finally {
                    if (timeoutId) window.clearTimeout(timeoutId);
                    listEl.setAttribute("aria-busy", "false");
                }
            })();
            taskQueueInFlight = req;
            try {
                return await req;
            } finally {
                if (taskQueueInFlight === req) {
                    taskQueueInFlight = null;
                }
            }
        }

        async function submitTaskQueueForm() {
            const labelEl = document.getElementById("devTaskQueueLabel");
            const scopeEl = document.getElementById("devTaskQueueScope");
            const promptEl = document.getElementById("devTaskQueuePrompt");
            const submitBtn = document.getElementById("devTaskQueueSubmit");
            const feedbackEl = document.getElementById("devTaskQueueFeedback");
            const label = labelEl && typeof labelEl.value === "string" ? labelEl.value.trim() : "";
            const scope = scopeEl && typeof scopeEl.value === "string" ? scopeEl.value.trim() : "";
            const prompt = promptEl && typeof promptEl.value === "string" ? promptEl.value.trimEnd() : "";
            if (feedbackEl) {
                feedbackEl.dataset.sticky = "";
                feedbackEl.textContent = "";
            }
            if (!label) {
                if (feedbackEl) feedbackEl.textContent = "Label is required.";
                return;
            }
            if (!prompt) {
                if (feedbackEl) feedbackEl.textContent = "Prompt is required.";
                return;
            }
            if (submitBtn) {
                submitBtn.disabled = true;
            }
            try {
                const r = await fetch("/api/dev/tasks", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                    credentials: "same-origin",
                    body: JSON.stringify({ label, scope, prompt }),
                });
                const data = await r.json().catch(() => ({}));
                if (!r.ok || data.ok === false) {
                    throw new Error(data.error || `Queue failed (${r.status})`);
                }
                if (labelEl) labelEl.value = "";
                if (promptEl) promptEl.value = "";
                if (feedbackEl) {
                    feedbackEl.textContent = "Task queued.";
                }
                void loadTaskQueue();
            } catch (e) {
                if (feedbackEl) {
                    feedbackEl.textContent = e && e.message ? e.message : "Queue failed.";
                    feedbackEl.dataset.sticky = "1";
                }
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                }
            }
        }

        async function cycleTaskStatus(taskId, currentStatus) {
            const id = String(taskId || "").trim();
            if (!id) return;
            const order = ["queued", "in_progress", "done", "failed"];
            const cur = normalizeTaskStatus(currentStatus);
            const idx = Math.max(0, order.indexOf(cur));
            const next = order[(idx + 1) % order.length];
            const feedbackEl = document.getElementById("devTaskQueueFeedback");
            try {
                const r = await fetch(`/api/dev/tasks/${encodeURIComponent(id)}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                    credentials: "same-origin",
                    body: JSON.stringify({ status: next }),
                });
                const data = await r.json().catch(() => ({}));
                if (!r.ok || data.ok === false) {
                    throw new Error(data.error || "Update failed");
                }
                if (feedbackEl) {
                    feedbackEl.textContent = `Status: ${formatTaskStatusLabel(next)}`;
                    feedbackEl.dataset.sticky = "";
                }
                void loadTaskQueue();
            } catch (e) {
                if (feedbackEl) {
                    feedbackEl.textContent = e && e.message ? e.message : "Status update failed.";
                    feedbackEl.dataset.sticky = "1";
                }
            }
        }

        const taskQueueForm = document.getElementById("devTaskQueueForm");
        if (taskQueueForm && taskQueueForm.dataset.devBound !== "true") {
            taskQueueForm.dataset.devBound = "true";
            taskQueueForm.addEventListener("submit", (e) => {
                e.preventDefault();
                void submitTaskQueueForm();
            });
        }
        const taskQueueListEl = document.getElementById("devTaskQueueList");
        if (taskQueueListEl && taskQueueListEl.dataset.devBound !== "true") {
            taskQueueListEl.dataset.devBound = "true";
            taskQueueListEl.addEventListener("click", (e) => {
                const t = e.target instanceof HTMLElement ? e.target.closest(".devTaskQueueItem") : null;
                if (!t) return;
                void cycleTaskStatus(t.dataset.taskId || "", t.dataset.status || "queued");
            });
            taskQueueListEl.addEventListener("keydown", (e) => {
                if (e.key !== "Enter" && e.key !== " ") return;
                const t = e.target instanceof HTMLElement ? e.target.closest(".devTaskQueueItem") : null;
                if (!t) return;
                e.preventDefault();
                void cycleTaskStatus(t.dataset.taskId || "", t.dataset.status || "queued");
            });
        }

        function renderPendingApprovals(approvals) {
            const container = document.getElementById("devPendingApprovalsList") || document.getElementById("pendingApprovals");
            if (!container) {
                return;
            }
            const emptyEl = document.getElementById("devPendingApprovalsEmpty");
            const countHint = document.getElementById("devPendingApprovalsCountHint");
            const approveAllBtn = document.getElementById("devPendingApprovalsApproveAll");
            const list = Array.isArray(approvals) ? approvals : [];

            const filteredItems = list.filter((item) => {
                const qRaw =
                    item && item.queue_kind != null && String(item.queue_kind).trim() !== ""
                        ? item.queue_kind
                        : item && item.queue_type != null && String(item.queue_type).trim() !== ""
                            ? item.queue_type
                            : "publication";
                const queueKind = String(qRaw).trim().toLowerCase();
                if (queueKind !== "publication") {
                    return false;
                }
                const status = String(item && item.status ? item.status : "pending").trim().toLowerCase();
                if (status !== "pending") {
                    return false;
                }
                const approvalState = String(item && item.approval_state != null ? item.approval_state : "").trim().toLowerCase();
                return approvalState === "" || approvalState === "pending_review";
            });

            if (!filteredItems.length) {
                if (emptyEl) {
                    emptyEl.textContent = "No pending approvals.";
                    emptyEl.classList.remove("isHidden");
                }
                container.querySelectorAll(".devPendingApprovalRow").forEach((el) => el.remove());
                if (countHint) {
                    countHint.textContent = "0 card(s) waiting for approval or review";
                }
                if (approveAllBtn) {
                    approveAllBtn.disabled = true;
                }
                return;
            }
            if (emptyEl) {
                emptyEl.classList.add("isHidden");
            }
            if (countHint) {
                countHint.textContent = filteredItems.length + " card(s) waiting for approval or review";
            }
            if (approveAllBtn) {
                approveAllBtn.disabled = filteredItems.length === 0;
            }

            container.querySelectorAll(".devPendingApprovalRow").forEach((el) => el.remove());

            function escAttr(s) {
                return String(s)
                    .replace(/&/g, "&amp;")
                    .replace(/"/g, "&quot;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;");
            }
            function escHtml(s) {
                return String(s)
                    .replace(/&/g, "&amp;")
                    .replace(/</g, "&lt;")
                    .replace(/>/g, "&gt;");
            }
            function formatUnbanDateForApproval(iso) {
                const s = String(iso || "").trim();
                if (!s) {
                    return "";
                }
                const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
                if (!m) {
                    return s;
                }
                const dt = new Date(
                    parseInt(m[1], 10),
                    parseInt(m[2], 10) - 1,
                    parseInt(m[3], 10),
                    12, 0, 0, 0,
                );
                if (Number.isNaN(dt.getTime())) {
                    return s;
                }
                return dt.toLocaleDateString("en-US", {
                    month: "long",
                    day: "numeric",
                    year: "numeric",
                });
            }

            const htmlParts = [];
            for (const item of filteredItems) {

                const qk = String(
                    item.queue_kind != null && String(item.queue_kind).trim() !== ""
                        ? item.queue_kind
                        : item.queue_type != null && String(item.queue_type).trim() !== ""
                            ? item.queue_type
                            : "learner",
                );
                const cardCode = String(item.card_code || item.target_id || "").trim();
                const rowId = qk === "publication" ? "" : String(item.id != null ? item.id : "");
                const sourceId = String(item.source_id || "");
                const card = {
                    id: cardCode || rowId || "—",
                    image_url: typeof item.image_url === "string" ? item.image_url.trim() : "",
                    name: item.name || item.card_name || cardCode || "—",
                };
                const idEsc = escHtml(card.id);
                const nameEsc = escHtml(card.name);
                const reviewReasonRaw = String(item.review_reason || "").trim();
                const summaryRaw = String(item.summary_text || item.insight_preview || item.review_reason || "").trim();
                const confidenceNum = Number(item.confidence_score != null ? item.confidence_score : item.confidence);
                let plainSummary;
                if (summaryRaw) {
                    plainSummary = summaryRaw;
                } else if (reviewReasonRaw === "legality_sensitive") {
                    plainSummary = "Flagged: this card appears on a ban or restriction list — please verify before approving.";
                } else if (reviewReasonRaw === "guarded_publish_review") {
                    plainSummary = "Flagged: Miru wants a confidence check before publishing this insight.";
                } else if (reviewReasonRaw) {
                    plainSummary = "Flagged for review: " + reviewReasonRaw;
                } else {
                    plainSummary = "Flagged for review.";
                }
                const confidenceLabel = Number.isFinite(confidenceNum)
                    ? Math.round(Math.max(0, Math.min(1, confidenceNum)) * 100) + "%"
                    : "—";
                const reasonLabel = reviewReasonRaw || "—";

                const thumbHtml = card.image_url
                    ? `<div class="devPendingApprovalThumb"><img src="${escAttr(card.image_url)}" alt="" width="80" loading="lazy" decoding="async" onerror="this.onerror=null;var p=this.closest('.devPendingApprovalThumb');if(p)p.style.display='none';"></div>`
                    : "";

                const cit = item.ruling_citation;
                const urlBase = cit && cit.source_url ? String(cit.source_url).trim() : "";
                const anchorPart = cit && cit.source_anchor ? String(cit.source_anchor).trim() : "";
                const sourceHref = urlBase ? escAttr(urlBase + anchorPart) : "";
                const citeTitle = cit && cit.source_title ? String(cit.source_title).trim() : "";
                const sourceLinkInner = citeTitle
                    ? `View official source: ${escHtml(citeTitle)} →`
                    : "View Miru's source →";
                const sourceLinkHtml = sourceHref
                    ? `<a class="devPendingApprovalSourceLink" href="${sourceHref}" target="_blank" rel="noopener noreferrer">${sourceLinkInner}</a>`
                    : "";

                const unbanRaw =
                    cit && cit.unban_effective_at != null && cit.unban_effective_at !== ""
                        ? String(cit.unban_effective_at).trim()
                        : "";
                const unbanDisp = unbanRaw ? formatUnbanDateForApproval(unbanRaw) : "";
                const unbanLineHtml = unbanDisp
                    ? `<p class="devPendingApprovalUnbanNote" style="font-size:0.72rem;color:rgba(200,195,220,0.55);margin:0 0 0.45rem;line-height:1.35;">Note: this card is scheduled to be unbanned on ${escHtml(unbanDisp)} per official notice.</p>`
                    : "";

                const insightsArr = item.insights && item.insights.length > 0 ? item.insights : [];
                const insight0Text =
                    insightsArr.length && insightsArr[0] && insightsArr[0].insight_text
                        ? String(insightsArr[0].insight_text).trim()
                        : "";
                const insightLineHtml = insight0Text
                    ? `<p class="devPendingApprovalInsightLine">${escHtml(insight0Text)}</p>`
                    : "";

                let learnerIdVal = null;
                if (rowId && String(rowId).trim() !== "") {
                    const nLearner = parseInt(rowId, 10);
                    if (!Number.isNaN(nLearner)) {
                        learnerIdVal = nLearner;
                    }
                }
                let actionsHtml = "";
                if (qk === "publication") {
                    actionsHtml =
                        '<button type="button" class="btn-approve devPendingApprovalBtn devPendingApprovalBtn--approve" data-approval-action="approve">Approve</button>' +
                        '<button type="button" class="btn-reject devPendingApprovalBtn devPendingApprovalBtn--reject" data-approval-action="reject">Reject</button>';
                } else {
                    const ccJs = JSON.stringify(cardCode);
                    const learnerApproveBodyEscaped =
                        "JSON.stringify({id:" +
                        (learnerIdVal === null ? "null" : JSON.stringify(learnerIdVal)) +
                        ",card_code:" +
                        JSON.stringify(cardCode) +
                        ",source_id:" +
                        JSON.stringify(sourceId) +
                        "})";
                    const learnerRejectBodyEscaped =
                        "JSON.stringify({id:" +
                        (learnerIdVal === null ? "null" : JSON.stringify(learnerIdVal)) +
                        "})";
                    const pendingClickPrefix =
                        "var btn=this;var rowEl=btn.closest(\".devPendingApprovalRow\");if(rowEl){var xs=rowEl.querySelectorAll(\".devPendingApprovalBtn\");for(var bi=0;bi<xs.length;bi++){xs[bi].disabled=true;}}fetch(\"";
                    const pendingClickMid =
                        "\",{method:\"POST\",headers:{\"Content-Type\":\"application/json\",\"Accept\":\"application/json\",\"X-Requested-With\":\"miru-client-nav\"},credentials:\"same-origin\",body:";
                    const pendingClickPostBody = "})";
                    const pendingClickTail =
                        ".then(function(r){return r.json();}).then(function(d){if(d&&d.ok!==false){var row=document.querySelector(\"[data-card-code=\"+JSON.stringify(" +
                        ccJs +
                        ")+\"]\");if(row){row.style.transition=\"opacity 0.3s\";row.style.opacity=\"0\";setTimeout(function(){row.remove();var el=document.getElementById(\"devPendingApprovalsCountHint\");if(el){var n=parseInt(el.textContent,10)||1;var nv=Math.max(0,n-1);el.textContent=nv+\" card(s) waiting for approval or review\";if(nv===0){var emptyEl=document.getElementById(\"devPendingApprovalsEmpty\");if(emptyEl){emptyEl.textContent=\"No pending approvals.\";emptyEl.classList.remove(\"isHidden\");}var ab=document.getElementById(\"devPendingApprovalsApproveAll\");if(ab){ab.disabled=true;}}},300);}}else{var err=document.createElement(\"p\");err.style.color=\"red\";err.textContent=(d&&d.error)||\"Action failed\";var act=btn.parentNode;if(act){var ex=act.querySelector(\".devPendingApprovalError\");if(ex){ex.remove();}err.className=\"devPendingApprovalError\";act.appendChild(err);}if(rowEl){var xsE=rowEl.querySelectorAll(\".devPendingApprovalBtn\");for(var bj=0;bj<xsE.length;bj++){xsE[bj].disabled=false;}}}}).catch(function(){var errC=document.createElement(\"p\");errC.style.color=\"red\";errC.textContent=\"Network error. Please check your connection and try again.\";var act2=btn.parentNode;if(act2){var ex2=act2.querySelector(\".devPendingApprovalError\");if(ex2){ex2.remove();}errC.className=\"devPendingApprovalError\";act2.appendChild(errC);}if(rowEl){var xsC=rowEl.querySelectorAll(\".devPendingApprovalBtn\");for(var bk=0;bk<xsC.length;bk++){xsC[bk].disabled=false;}}});return false;";
                    const approveClickAttr =
                        "onclick='" +
                        pendingClickPrefix +
                        "/api/dev/approve-validation" +
                        pendingClickMid +
                        learnerApproveBodyEscaped +
                        pendingClickPostBody +
                        pendingClickTail +
                        "'";
                    const rejectClickAttr =
                        "onclick='" +
                        pendingClickPrefix +
                        "/api/dev/reject-validation" +
                        pendingClickMid +
                        learnerRejectBodyEscaped +
                        pendingClickPostBody +
                        pendingClickTail +
                        "'";
                    actionsHtml =
                        '<button type="button" class="btn-approve devPendingApprovalBtn devPendingApprovalBtn--approve" ' +
                        approveClickAttr +
                        ">Approve</button>" +
                        '<button type="button" class="btn-reject devPendingApprovalBtn devPendingApprovalBtn--reject" ' +
                        rejectClickAttr +
                        ">Reject</button>";
                }

                htmlParts.push(`
        <div class="approval-item devPendingApprovalRow devPendingApprovalRow--compact" data-id="${escAttr(rowId)}" data-item-key="${escAttr(String(item.item_key || ""))}" data-card-code="${escAttr(cardCode)}" data-source-id="${escAttr(sourceId)}" data-queue-kind="${escAttr(qk)}">
            <div class="devPendingApprovalRowMain">
                ${thumbHtml}
                <div class="devPendingApprovalBody">
                    <div class="devPendingApprovalTitleRow">
                        <strong>${nameEsc}</strong>
                        <span class="devPendingApprovalCode">${idEsc}</span>
                    </div>
                    <p class="devPendingApprovalSummary">${escHtml(plainSummary)}</p>
                    <p class="devPendingApprovalInsightLine">Confidence: ${escHtml(confidenceLabel)} · Reason: ${escHtml(reasonLabel)}</p>
                    ${insightLineHtml}
                    ${sourceLinkHtml}
                    ${unbanLineHtml}
                </div>
            </div>
            <div class="devPendingApprovalActions approval-actions">
                ${actionsHtml}
            </div>
        </div>
    `);
            }

            const html = htmlParts.join("");

            if (emptyEl && typeof container.contains === "function" && container.contains(emptyEl)) {
                emptyEl.insertAdjacentHTML("beforebegin", html);
            } else {
                container.insertAdjacentHTML("beforeend", html);
            }

            ensurePendingApprovalsBindings();
        }

        function initDevPublicationApprovalFlash() {
            try {
                const params = new URLSearchParams(window.location.search || "");
                const approved = (params.get("approved") || "").trim();
                const rejected = (params.get("rejected") || "").trim();
                if (!approved && !rejected) {
                    return;
                }
                const section = document.querySelector(".devPendingApprovals");
                if (!section) {
                    return;
                }
                const bar = document.createElement("div");
                bar.className = "devPendingApprovalsFlash";
                bar.setAttribute("role", "status");
                bar.textContent = approved
                    ? "\u2713 " + approved + " approved"
                    : "\u2717 " + rejected + " rejected";
                if (rejected) {
                    bar.classList.add("devPendingApprovalsFlash--reject");
                }
                section.insertBefore(bar, section.firstChild);
                window.setTimeout(function () {
                    if (bar.parentNode) {
                        bar.parentNode.removeChild(bar);
                    }
                    try {
                        const url = new URL(window.location.href);
                        url.searchParams.delete("approved");
                        url.searchParams.delete("rejected");
                        const next = url.pathname + (url.search || "") + (url.hash || "");
                        window.history.replaceState({}, document.title, next);
                    } catch (_) {
                        /* ignore */
                    }
                }, 3000);
            } catch (_) {
                /* ignore */
            }
        }

        async function loadPendingApprovals() {
            const listEl = document.getElementById("devPendingApprovalsList") || document.getElementById("pendingApprovals");
            if (!listEl) {
                return;
            }
            const emptyEl = document.getElementById("devPendingApprovalsEmpty");
            if (pendingApprovalsInFlight) {
                return pendingApprovalsInFlight;
            }
            listEl.setAttribute("aria-busy", "true");
            if (emptyEl) {
                emptyEl.textContent = "Loading pending approvals…";
                emptyEl.classList.remove("isHidden");
            }
            const timeoutMs = 12000;
            const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
            const timeoutId = ac ? window.setTimeout(() => ac.abort(), timeoutMs) : null;
            let loadedOk = false;
            const request = (async () => {
                try {
                    const response = await fetch("/api/dev/pending-approvals", {
                        headers: { Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                        credentials: "same-origin",
                        signal: ac ? ac.signal : undefined,
                    });
                    if (!response.ok) {
                        throw new Error("Pending approvals failed " + response.status);
                    }
                    const data = await response.json().catch(() => ({}));
                    const raw = Array.isArray(data && data.items) ? data.items : [];
                    renderPendingApprovals(raw);
                    loadedOk = true;
                } catch (err) {
                    if (emptyEl) {
                        emptyEl.textContent = err && err.name === "AbortError"
                            ? "Request timed out. Tap Refresh to try again."
                            : "Could not load pending approvals. Tap Refresh to try again.";
                        emptyEl.classList.remove("isHidden");
                    }
                    listEl.querySelectorAll(".devPendingApprovalRow").forEach((el) => el.remove());
                } finally {
                    if (timeoutId) window.clearTimeout(timeoutId);
                    listEl.setAttribute("aria-busy", "false");
                    if (!loadedOk && emptyEl && emptyEl.textContent === "Loading pending approvals…") {
                        emptyEl.textContent = "No pending approvals.";
                        emptyEl.classList.remove("isHidden");
                    }

                    ensurePendingApprovalsBindings();
                }
            })();
            pendingApprovalsInFlight = request;
            try {
                return await request;
            } finally {
                if (pendingApprovalsInFlight === request) {
                    pendingApprovalsInFlight = null;
                }
            }
        }

        function ensurePendingApprovalsBindings() {
            const pendingApprovalsListEl = document.getElementById("devPendingApprovalsList") || document.getElementById("pendingApprovals");
            if (!pendingApprovalsListEl) {
                return;
            }
            if (pendingApprovalsListEl.dataset.devBound === "true") {
                return;
            }
            pendingApprovalsListEl.dataset.devBound = "true";
            pendingApprovalsListEl.addEventListener("click", async (event) => {
                const btn = event.target instanceof HTMLElement
                    ? event.target.closest(".devPendingApprovalBtn[data-approval-action]")
                    : null;
                if (!btn) {
                    return;
                }
                event.preventDefault();
                const row = btn.closest(".devPendingApprovalRow");
                if (!row) {
                    return;
                }
                const queueKind = String(row.getAttribute("data-queue-kind") || "").trim().toLowerCase();
                if (queueKind !== "publication") {
                    return;
                }
                const itemKey = String(row.getAttribute("data-item-key") || "").trim();
                const targetId = String(row.getAttribute("data-card-code") || "").trim().toUpperCase();
                const action = String(btn.getAttribute("data-approval-action") || "").trim().toLowerCase();
                if (!itemKey || !targetId || (action !== "approve" && action !== "reject")) {
                    return;
                }
                const endpoint = action === "approve" ? "/api/dev/review-queue/approve" : "/api/dev/review-queue/reject";
                const rowButtons = row.querySelectorAll(".devPendingApprovalBtn");
                rowButtons.forEach((node) => {
                    node.disabled = true;
                });
                try {
                    const response = await fetch(endpoint, {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            Accept: "application/json",
                            "X-Requested-With": "miru-client-nav",
                        },
                        credentials: "same-origin",
                        body: JSON.stringify({ item_key: itemKey, target_id: targetId }),
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok || !data || data.ok === false) {
                        throw new Error((data && data.error) || "Action failed");
                    }
                    row.style.transition = "opacity 0.3s";
                    row.style.opacity = "0";
                    window.setTimeout(() => {
                        row.remove();
                        const hint = document.getElementById("devPendingApprovalsCountHint");
                        if (hint) {
                            const current = parseInt(hint.textContent || "0", 10) || 0;
                            const next = Math.max(0, current - 1);
                            hint.textContent = next + " card(s) waiting for approval or review";
                            if (next === 0) {
                                const emptyEl = document.getElementById("devPendingApprovalsEmpty");
                                if (emptyEl) {
                                    emptyEl.textContent = "No pending approvals.";
                                    emptyEl.classList.remove("isHidden");
                                }
                                const approveAll = document.getElementById("devPendingApprovalsApproveAll");
                                if (approveAll) {
                                    approveAll.disabled = true;
                                }
                            }
                        }
                    }, 300);
                } catch (err) {
                    const actionWrap = btn.parentElement;
                    if (actionWrap) {
                        const prior = actionWrap.querySelector(".devPendingApprovalError");
                        if (prior) {
                            prior.remove();
                        }
                        const errorEl = document.createElement("p");
                        errorEl.className = "devPendingApprovalError";
                        errorEl.style.color = "red";
                        errorEl.textContent = err && err.message ? err.message : "Network error. Please try again.";
                        actionWrap.appendChild(errorEl);
                    }
                    rowButtons.forEach((node) => {
                        node.disabled = false;
                    });
                }
            });
        }
        const pendingApprovalsListEl = document.getElementById("devPendingApprovalsList") || document.getElementById("pendingApprovals");
        ensurePendingApprovalsBindings();
        const pendingApprovalsRefreshBtn = document.getElementById("devPendingApprovalsRefresh");
        if (pendingApprovalsRefreshBtn && pendingApprovalsRefreshBtn.dataset.devBound !== "true") {
            pendingApprovalsRefreshBtn.dataset.devBound = "true";
            pendingApprovalsRefreshBtn.addEventListener("click", () => {
                void loadPendingApprovals();
            });
        }
        const pendingApprovalsApproveAllBtn = document.getElementById("devPendingApprovalsApproveAll");
        if (pendingApprovalsApproveAllBtn && pendingApprovalsApproveAllBtn.dataset.devBound !== "true") {
            pendingApprovalsApproveAllBtn.dataset.devBound = "true";
            pendingApprovalsApproveAllBtn.addEventListener("click", () => {
                pendingApprovalsApproveAllBtn.disabled = true;
                fetch("/api/dev/publish-review/approve-all", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        Accept: "application/json",
                        "X-Requested-With": "miru-client-nav",
                    },
                    credentials: "same-origin",
                    body: "{}",
                })
                    .then((r) => r.json().catch(() => ({})))
                    .then((data) => {
                        if (data && data.ok !== false) {
                            void loadPendingApprovals();
                            void loadDevStatus({ lightweight: true });
                        } else if (setDevControlFeedback) {
                            setDevControlFeedback(data && data.error ? data.error : "Approve all failed.", true);
                        }
                    })
                    .finally(() => {
                        pendingApprovalsApproveAllBtn.disabled = false;
                    });
            });
        }
        const reviewApprovalsBtn = document.getElementById("devReviewApprovalsBtn");
        if (reviewApprovalsBtn && reviewApprovalsBtn.dataset.devBound !== "true") {
            reviewApprovalsBtn.dataset.devBound = "true";
            reviewApprovalsBtn.addEventListener("click", function () {
                const section = document.getElementById("devPendingApprovalsSection");
                if (section) {
                    requestAnimationFrame(function () {
                        section.scrollIntoView({ behavior: "smooth", block: "start" });
                    });
                }
                void loadPendingApprovals();
            });
        }
        root.loadPendingApprovals = loadPendingApprovals;
        if (document.querySelector(".devPendingApprovals")) {
            initDevPublicationApprovalFlash();
        }
        if (pendingApprovalsListEl) {
            void loadPendingApprovals();
        }

        const devMonitorScrollBtn = document.getElementById("devMonitorScrollBtn");
        if (devMonitorScrollBtn && devMonitorScrollBtn.dataset.devBound !== "true") {
            devMonitorScrollBtn.dataset.devBound = "true";
            devMonitorScrollBtn.addEventListener("click", function () {
                const el = document.getElementById("devMonitor");
                if (el) {
                    el.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            });
        }
        const devLaunchpadScrollToMonitor = document.getElementById("devLaunchpadScrollToMonitor");
        if (devLaunchpadScrollToMonitor && devLaunchpadScrollToMonitor.dataset.devBound !== "true") {
            devLaunchpadScrollToMonitor.dataset.devBound = "true";
            devLaunchpadScrollToMonitor.addEventListener("click", function () {
                const el = document.getElementById("devMonitor");
                if (el) {
                    el.scrollIntoView({ behavior: "smooth", block: "start" });
                }
            });
        }

        if (devRefreshButtons.length && root.dataset.devRefreshBound !== "true") {
            root.dataset.devRefreshBound = "true";
            devRefreshButtons.forEach((btn) => {
                btn.addEventListener("click", () => {
                    void loadDevStatus({ manual: true, lightweight: true }).then(() => {
                        void loadPendingApprovals();
                        void loadTaskQueue();
                    });
                });
            });
        }

        const devRulingsLookupBtn = document.getElementById("devRulingsLookupBtn");
        const devRulingsResult = document.getElementById("devRulingsResult");
        const devRulingsEmpty = document.getElementById("devRulingsEmpty");
        const devRulingsBest = document.getElementById("devRulingsBest");
        const devRulingsBestQuestion = document.getElementById("devRulingsBestQuestion");
        const devRulingsBestAnswer = document.getElementById("devRulingsBestAnswer");
        const devRulingsBestSummaryWrap = document.getElementById("devRulingsBestSummaryWrap");
        const devRulingsBestSummary = document.getElementById("devRulingsBestSummary");
        const devRulingsBestCitation = document.getElementById("devRulingsBestCitation");
        const devRulingsMoreWrap = document.getElementById("devRulingsMoreWrap");
        const devRulingsMoreSummary = document.getElementById("devRulingsMoreSummary");
        const devRulingsMoreList = document.getElementById("devRulingsMoreList");
        const devRulingsLoading = document.getElementById("devRulingsLoading");
        if (devRulingsLookupBtn && devRulingsResult && devRulingsLookupBtn.dataset.devBound !== "true") {
            devRulingsLookupBtn.dataset.devBound = "true";
            devRulingsLookupBtn.addEventListener("click", async () => {
                const cardCode = (document.getElementById("devRulingsCardCode") && document.getElementById("devRulingsCardCode").value) || "";
                const topicKey = (document.getElementById("devRulingsTopicKey") && document.getElementById("devRulingsTopicKey").value) || "";
                const query = (document.getElementById("devRulingsQuery") && document.getElementById("devRulingsQuery").value) || "";
                const params = new URLSearchParams();
                if (cardCode.trim()) params.set("card_code", cardCode.trim());
                if (topicKey.trim()) params.set("topic_key", topicKey.trim());
                if (query.trim()) params.set("query", query.trim());
                if (!params.toString()) {
                    params.set("card_code", "OP01-001");
                }
                devRulingsResult.hidden = true;
                if (devRulingsLoading) {
                    devRulingsLoading.classList.remove("isHidden");
                }
                devRulingsLookupBtn.disabled = true;
                try {
                    const response = await fetch("/api/dev/official-rulings?" + params.toString(), {
                        headers: { Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                        credentials: "same-origin",
                    });
                    const data = await response.json().catch(() => ({}));
                    if (devRulingsLoading) devRulingsLoading.classList.add("isHidden");
                    devRulingsResult.hidden = false;
                    const hasBest = !!data.best_match;
                    const more = data.more || [];
                    const empty = !hasBest && more.length === 0;
                    if (devRulingsEmpty) {
                        devRulingsEmpty.classList.toggle("isHidden", !empty);
                    }
                    if (devRulingsBest) {
                        devRulingsBest.classList.toggle("isHidden", !hasBest);
                    }
                    if (!hasBest) {
                        if (devRulingsMoreWrap) devRulingsMoreWrap.classList.toggle("isHidden", more.length === 0);
                        if (more.length > 0 && devRulingsBest) {
                            const first = more[0];
                            if (devRulingsBestQuestion) devRulingsBestQuestion.textContent = first.question_text || "";
                            if (devRulingsBestAnswer) devRulingsBestAnswer.textContent = first.ruling_text || "";
                            const hasSummary = first.normalized_summary && first.normalized_summary.trim();
                            if (devRulingsBestSummaryWrap) devRulingsBestSummaryWrap.classList.toggle("isHidden", !hasSummary);
                            if (devRulingsBestSummary) devRulingsBestSummary.textContent = hasSummary ? first.normalized_summary.trim() : "";
                            const cit = first.citation || {};
                            const parts = [];
                            if (cit.source_title) parts.push(cit.source_title);
                            if (cit.source_type) parts.push("(" + cit.source_type + ")");
                            if (cit.source_reference) parts.push(" â€” " + cit.source_reference);
                            let linkHtml = "";
                            if (cit.source_url && cit.source_url.trim()) {
                                const href = (cit.source_anchor && cit.source_anchor.trim()) ? (cit.source_url.trim() + cit.source_anchor.trim()) : cit.source_url.trim();
                                linkHtml = ' <a href="' + href.replace(/"/g, "&quot;") + '" target="_blank" rel="noopener noreferrer">View source</a>';
                            }
                            if (devRulingsBestCitation) devRulingsBestCitation.innerHTML = (parts.join(" ") || "Official ruling").trim() + linkHtml;
                            devRulingsBest.classList.remove("isHidden");
                        }
                        if (devRulingsMoreWrap) {
                            devRulingsMoreWrap.classList.toggle("isHidden", more.length <= 1);
                            if (devRulingsMoreSummary) devRulingsMoreSummary.textContent = "More matches (" + Math.max(0, more.length - 1) + ")";
                            if (devRulingsMoreList) {
                                devRulingsMoreList.innerHTML = "";
                                more.slice(1).forEach((r) => {
                                    const li = document.createElement("li");
                                    li.textContent = (r.normalized_summary && r.normalized_summary.trim()) ? r.normalized_summary.trim() : (r.ruling_text || "").slice(0, 120) + (r.ruling_text && r.ruling_text.length > 120 ? "â€¦" : "");
                                    devRulingsMoreList.appendChild(li);
                                });
                            }
                        }
                    } else {
                        const best = data.best_match;
                        if (devRulingsBestQuestion) devRulingsBestQuestion.textContent = best.question_text || "";
                        if (devRulingsBestAnswer) devRulingsBestAnswer.textContent = best.ruling_text || "";
                        const hasSummary = best.normalized_summary && best.normalized_summary.trim();
                        if (devRulingsBestSummaryWrap) devRulingsBestSummaryWrap.classList.toggle("isHidden", !hasSummary);
                        if (devRulingsBestSummary) devRulingsBestSummary.textContent = hasSummary ? best.normalized_summary.trim() : "";
                        const cit = best.citation || {};
                        const parts = [];
                        if (cit.source_title) parts.push(cit.source_title);
                        if (cit.source_type) parts.push("(" + cit.source_type + ")");
                        if (cit.source_reference) parts.push(" â€” " + cit.source_reference);
                        let linkHtml = "";
                        if (cit.source_url && cit.source_url.trim()) {
                            const href = (cit.source_anchor && cit.source_anchor.trim()) ? (cit.source_url.trim() + cit.source_anchor.trim()) : cit.source_url.trim();
                            linkHtml = ' <a href="' + href.replace(/"/g, "&quot;") + '" target="_blank" rel="noopener noreferrer">View source</a>';
                        }
                        if (devRulingsBestCitation) {
                            devRulingsBestCitation.innerHTML = (parts.join(" ") || "Official ruling").trim() + linkHtml;
                        }
                        const more = data.more || [];
                        if (devRulingsMoreWrap) {
                            devRulingsMoreWrap.classList.toggle("isHidden", more.length === 0);
                        }
                        if (devRulingsMoreSummary) devRulingsMoreSummary.textContent = "More matches (" + more.length + ")";
                        if (devRulingsMoreList) {
                            devRulingsMoreList.innerHTML = "";
                            more.forEach((r) => {
                                const li = document.createElement("li");
                                li.textContent = (r.normalized_summary && r.normalized_summary.trim()) ? r.normalized_summary.trim() : (r.ruling_text || "").slice(0, 120) + (r.ruling_text && r.ruling_text.length > 120 ? "â€¦" : "");
                                devRulingsMoreList.appendChild(li);
                            });
                        }
                    }
                } catch (e) {
                    if (devRulingsLoading) devRulingsLoading.classList.add("isHidden");
                    devRulingsResult.hidden = false;
                    if (devRulingsEmpty) {
                        devRulingsEmpty.classList.remove("isHidden");
                        const emptyText = devRulingsEmpty.querySelector(".devRulingsTestEmptyText");
                        if (emptyText) emptyText.textContent = "Lookup failed. Try again or check the console.";
                    }
                    if (devRulingsBest) devRulingsBest.classList.add("isHidden");
                    if (devRulingsMoreWrap) devRulingsMoreWrap.classList.add("isHidden");
                } finally {
                    devRulingsLookupBtn.disabled = false;
                }
            });
        }

        function loadRuntimeStatus() {
            const rootEl = document.getElementById("devMonitor");
            const mainPort = String((rootEl && rootEl.getAttribute("data-main-site-port")) || "8080");
            const status18765 = document.getElementById("devRuntimeStatus18765");
            const status18080 = document.getElementById("devRuntimeStatus18080");
            const status8080 = document.getElementById("devRuntimeStatus8080");
            const checked18765 = document.getElementById("devChecked18765");
            const checked18080 = document.getElementById("devChecked18080");
            const checked8080 = document.getElementById("devChecked8080");
            const dot18765 = document.getElementById("devServiceSummaryDot18765");
            const dot18080 = document.getElementById("devServiceSummaryDot18080");
            const dot8080 = document.getElementById("devServiceSummaryDot8080");
            const guardStateEl = document.getElementById("devRuntimeGuardState");
            const feedbackEl = document.getElementById("devRuntimeControlFeedback");
            const restartHintEl = document.getElementById("devRuntimeRestartHint");
            const restartBtns = [
                document.getElementById("devRuntimeRestart18080Btn"),
                document.getElementById("devRuntimeRestart18765Btn"),
                document.getElementById("devRuntimeRestart8080Btn"),
                document.getElementById("devRuntimeRestartWorktreeBtn"),
            ].filter(Boolean);
            if (!status18765 && !status18080 && !status8080) return;
            fetch("/api/runtime/status", {
                headers: { Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                credentials: "same-origin",
            })
                .then((r) => r.json().catch(() => ({})))
                .then((data) => {
                    latestRuntimeStatus = data || {};
                    const s18765 = data["18765"] === "ok" ? "Healthy" : "Unhealthy";
                    const s18080 = data["18080"] === "ok" ? "Healthy" : "Unhealthy";
                    const mainKey = Object.prototype.hasOwnProperty.call(data, mainPort) ? mainPort : "8080";
                    const mainVal = data[mainKey];
                    const s8080 =
                        mainVal === "ok" ? "Healthy" : mainVal === undefined || mainVal === null ? "—" : "Unhealthy";
                    if (status18765) {
                        status18765.textContent = s18765;
                        status18765.className = "statusPill statusPill--" + (data["18765"] === "ok" ? "good" : "warn");
                    }
                    if (status18080) {
                        status18080.textContent = s18080;
                        status18080.className = "statusPill statusPill--" + (data["18080"] === "ok" ? "good" : "warn");
                    }
                    if (status8080) {
                        status8080.textContent = s8080;
                        status8080.className =
                            "statusPill statusPill--" +
                            (mainVal === "ok" ? "good" : mainVal === undefined || mainVal === null ? "neutral" : "warn");
                    }
                    function setSummaryDot(el, tone) {
                        if (!el) {
                            return;
                        }
                        el.className = "devServiceDot devServiceDot--" + tone;
                    }
                    setSummaryDot(dot18765, data["18765"] === "ok" ? "good" : "bad");
                    setSummaryDot(dot18080, data["18080"] === "ok" ? "good" : "bad");
                    if (mainVal === "ok") {
                        setSummaryDot(dot8080, "good");
                    } else if (mainVal === undefined || mainVal === null) {
                        setSummaryDot(dot8080, "neutral");
                    } else {
                        setSummaryDot(dot8080, "bad");
                    }
                    const checkedLine = data.checked_at ? "Last checked " + formatTimeLA(data.checked_at) : "Last checked —";
                    [checked18765, checked18080, checked8080].forEach((el) => {
                        if (el) el.textContent = checkedLine;
                    });
                    const legacyChecked = document.getElementById("devRuntimeControlChecked");
                    if (legacyChecked && data.checked_at) {
                        legacyChecked.textContent = "Checked " + formatTimeLA(data.checked_at);
                    }
                    const allowed = data.restart_allowed === true;
                    if (guardStateEl) {
                        guardStateEl.textContent = allowed
                            ? "Guard: restarts authorized from this device (localhost / private / Tailscale per server rules)."
                            : "Guard: runtime restarts restricted — use this machine or Tailscale, or set MIRU_RUNTIME_RESTART_TOKEN.";
                    }
                    restartBtns.forEach((btn) => {
                        btn.disabled = !allowed;
                        const act = btn.getAttribute("data-action") || "";
                        let t = btn.title || "";
                        if (allowed) {
                            if (act === "restart-18080") t = "Restart Project Miru dashboard (18080)";
                            else if (act === "restart-18765") t = "Restart Miru AI Dev (replaces this process)";
                            else if (act === "restart-worktree") t = "Restart worktree stack (18080 + 18765)";
                            else if (act === "restart-8080") t = "Start or refresh main site (docker compose when available)";
                        } else {
                            t = "Restart allowed only from this machine or Tailscale";
                        }
                        btn.title = t;
                    });
                    if (restartHintEl) {
                        restartHintEl.textContent = allowed ? "You can restart services from this device." : "Restart only from this machine or Tailscale.";
                        restartHintEl.className = "devRuntimeRestartHint devProgressDetail" + (allowed ? " devRuntimeRestartHint--allowed" : " devRuntimeRestartHint--restricted");
                    }
                    if (feedbackEl && feedbackEl.textContent && !feedbackEl.dataset.sticky) {
                        feedbackEl.hidden = true;
                        feedbackEl.textContent = "";
                    }
                    if (lastDevStatusPayload && !isDevCockpitMinimalSurface()) {
                        renderControlLayer(lastDevStatusPayload);
                    }
                })
                .catch(() => {
                    latestRuntimeStatus = null;
                    if (status18765) status18765.textContent = "—";
                    if (status18080) status18080.textContent = "—";
                    if (status8080) status8080.textContent = "—";
                    [dot18765, dot18080, dot8080].forEach((el) => {
                        if (el) el.className = "devServiceDot devServiceDot--neutral";
                    });
                    [checked18765, checked18080, checked8080].forEach((el) => {
                        if (el) el.textContent = "Last checked —";
                    });
                    const legacyChecked = document.getElementById("devRuntimeControlChecked");
                    if (legacyChecked) legacyChecked.textContent = "";
                    if (guardStateEl) guardStateEl.textContent = "Guard: status unavailable.";
                    restartBtns.forEach((btn) => { btn.disabled = false; });
                    if (restartHintEl) restartHintEl.textContent = "";
                    if (lastDevStatusPayload && !isDevCockpitMinimalSurface()) {
                        renderControlLayer(lastDevStatusPayload);
                    }
                });
        }

        function setRuntimeFeedback(text, isError, sticky) {
            const feedbackEl = document.getElementById("devRuntimeControlFeedback");
            if (!feedbackEl) return;
            feedbackEl.textContent = text || "";
            feedbackEl.hidden = !text;
            feedbackEl.className = "devRuntimeControlFeedback devProgressDetail" + (isError ? " devRuntimeControlFeedback--error" : "");
            feedbackEl.dataset.sticky = sticky ? "1" : "";
            if (text && !sticky) {
                window.clearTimeout(feedbackEl._clearTimer);
                feedbackEl._clearTimer = window.setTimeout(() => {
                    feedbackEl.textContent = "";
                    feedbackEl.hidden = true;
                    delete feedbackEl.dataset.sticky;
                }, 8000);
            }
        }

        const devMonitorEl = document.getElementById("devMonitor");
        const runtimeRestartToken = (devMonitorEl && devMonitorEl.getAttribute("data-runtime-restart-token")) || "";
        function runtimeRestartHeaders() {
            const h = { Accept: "application/json", "X-Requested-With": "miru-client-nav", "Content-Type": "application/json" };
            if (runtimeRestartToken) h["X-Miru-Runtime-Token"] = runtimeRestartToken;
            return h;
        }
        const devRuntimeRefreshBtn = document.getElementById("devRuntimeRefreshBtn");
        const devRuntimeRestart18080Btn = document.getElementById("devRuntimeRestart18080Btn");
        const devRuntimeRestart18765Btn = document.getElementById("devRuntimeRestart18765Btn");
        const devRuntimeRestartWorktreeBtn = document.getElementById("devRuntimeRestartWorktreeBtn");
        if (devRuntimeRefreshBtn) {
            devRuntimeRefreshBtn.addEventListener("click", () => {
                devRuntimeRefreshBtn.disabled = true;
                loadRuntimeStatus();
                void loadDevStatus({ lightweight: true });
                window.setTimeout(() => { devRuntimeRefreshBtn.disabled = false; }, 800);
            });
        }
        const devHandoffCopyBtn = document.getElementById("devHandoffCopyBtn");
        if (devHandoffCopyBtn && devHandoffCopyBtn.dataset.devBound !== "true") {
            devHandoffCopyBtn.dataset.devBound = "true";
            devHandoffCopyBtn.addEventListener("click", async () => {
                const ta = document.getElementById("devHandoffPromptText");
                const text = ta && typeof ta.value === "string" ? ta.value.trim() : "";
                if (!text) {
                    flashButtonState(devHandoffCopyBtn, "Empty");
                    return;
                }
                try {
                    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
                        await navigator.clipboard.writeText(text);
                        flashButtonState(devHandoffCopyBtn, "Copied");
                    } else {
                        throw new Error("no clipboard");
                    }
                } catch (_) {
                    setRuntimeFeedback("Could not copy automatically. Select the prompt in the text area and copy manually.", true, false);
                    flashButtonState(devHandoffCopyBtn, "Select text");
                    if (ta && typeof ta.focus === "function") {
                        ta.focus();
                        if (typeof ta.select === "function") {
                            ta.select();
                        }
                    }
                }
            });
        }
        const devHandoffResolveBtn = document.getElementById("devHandoffResolveBtn");
        if (devHandoffResolveBtn && devHandoffResolveBtn.dataset.devBound !== "true") {
            devHandoffResolveBtn.dataset.devBound = "true";
            devHandoffResolveBtn.addEventListener("click", async () => {
                devHandoffResolveBtn.disabled = true;
                try {
                    const r = await fetch("/api/dev/operator-handoff/resolve", {
                        method: "POST",
                        headers: runtimeRestartHeaders(),
                        credentials: "same-origin",
                        body: "{}",
                    });
                    const data = await r.json().catch(() => ({}));
                    if (r.ok && data.ok) {
                        if (data.operator_handoff) {
                            renderOperatorHandoff({ operator_handoff: data.operator_handoff });
                        }
                        void loadDevStatus({ lightweight: true });
                        flashButtonState(devHandoffResolveBtn, "Saved");
                    } else {
                        setRuntimeFeedback(
                            (data.error || "Handoff resolve failed") + (r.status === 403 ? " (this machine, LAN, Tailscale, or token)" : ""),
                            true,
                            false
                        );
                    }
                } catch (e) {
                    setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
                } finally {
                    devHandoffResolveBtn.disabled = false;
                }
            });
        }
        const devHandoffClearAckBtn = document.getElementById("devHandoffClearAckBtn");
        if (devHandoffClearAckBtn && devHandoffClearAckBtn.dataset.devBound !== "true") {
            devHandoffClearAckBtn.dataset.devBound = "true";
            devHandoffClearAckBtn.addEventListener("click", async () => {
                devHandoffClearAckBtn.disabled = true;
                try {
                    const r = await fetch("/api/dev/operator-handoff/clear-resolution", {
                        method: "POST",
                        headers: runtimeRestartHeaders(),
                        credentials: "same-origin",
                        body: "{}",
                    });
                    const data = await r.json().catch(() => ({}));
                    if (r.ok && data.ok) {
                        if (data.operator_handoff) {
                            renderOperatorHandoff({ operator_handoff: data.operator_handoff });
                        }
                        void loadDevStatus({ lightweight: true });
                        flashButtonState(devHandoffClearAckBtn, "Cleared");
                    } else {
                        setRuntimeFeedback(
                            (data.error || "Clear failed") + (r.status === 403 ? " (this machine, LAN, Tailscale, or token)" : ""),
                            true,
                            false
                        );
                    }
                } catch (e) {
                    setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
                } finally {
                    devHandoffClearAckBtn.disabled = false;
                }
            });
        }
        const devCockpitRestartMiruAiBtn = document.getElementById("devCockpitRestartMiruAiBtn");
        const devCockpitRestartPmBtn = document.getElementById("devCockpitRestartPmBtn");
        if (devCockpitRestartMiruAiBtn && devRuntimeRestart18765Btn) {
            devCockpitRestartMiruAiBtn.addEventListener("click", () => devRuntimeRestart18765Btn.click());
        }
        if (devCockpitRestartPmBtn && devRuntimeRestart18080Btn) {
            devCockpitRestartPmBtn.addEventListener("click", () => devRuntimeRestart18080Btn.click());
        }
        async function triggerPortRestart(btn, port) {
            if (!btn) return;
            const priorText = btn.textContent;
            btn.disabled = true;
            btn.textContent = "Restarting...";
            try {
                const r = await fetch("/api/dev/restart/" + String(port), {
                    method: "POST",
                    headers: runtimeRestartHeaders(),
                    credentials: "same-origin",
                });
                const data = await r.json().catch(() => ({}));
                if (!(r.ok && data && data.status === "restarting" && String(data.port) === String(port))) {
                    setRuntimeFeedback(data.error || data.detail || "Restart failed", true, false);
                }
            } catch (e) {
                setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
            }
            window.setTimeout(() => {
                btn.textContent = priorText;
                btn.disabled = false;
                loadRuntimeStatus();
            }, 3000);
        }
        if (devRuntimeRestart18080Btn) {
            devRuntimeRestart18080Btn.addEventListener("click", async () => {
                await triggerPortRestart(devRuntimeRestart18080Btn, 18080);
            });
        }
        if (devRuntimeRestart18765Btn) {
            devRuntimeRestart18765Btn.addEventListener("click", async () => {
                await triggerPortRestart(devRuntimeRestart18765Btn, 18765);
            });
        }
        if (devRuntimeRestartWorktreeBtn) {
            devRuntimeRestartWorktreeBtn.addEventListener("click", async () => {
                devRuntimeRestartWorktreeBtn.disabled = true;
                setRuntimeFeedback("Restarting worktree stackâ€¦ Reconnect in a few seconds.", false, true);
                try {
                    const r = await fetch("/api/runtime/restart/worktree", {
                        method: "POST",
                        headers: runtimeRestartHeaders(),
                        credentials: "same-origin",
                    });
                    const data = await r.json().catch(() => ({}));
                    if (r.status === 202 && data.ok) {
                        setRuntimeFeedback(data.detail || data.message, false, true);
                    } else {
                        setRuntimeFeedback((data.error || data.detail || "Restart failed") + (r.status === 403 ? " (this machine or Tailscale)" : ""), true, false);
                        devRuntimeRestartWorktreeBtn.disabled = false;
                    }
                } catch (e) {
                    setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
                    devRuntimeRestartWorktreeBtn.disabled = false;
                }
            });
        }
        const devRuntimeRestart8080Btn = document.getElementById("devRuntimeRestart8080Btn");
        if (devRuntimeRestart8080Btn) {
            devRuntimeRestart8080Btn.addEventListener("click", async () => {
                devRuntimeRestart8080Btn.disabled = true;
                setRuntimeFeedback("Starting or refreshing main siteâ€¦", false, true);
                try {
                    const r = await fetch("/api/runtime/restart/main-site", {
                        method: "POST",
                        headers: runtimeRestartHeaders(),
                        credentials: "same-origin",
                    });
                    const data = await r.json().catch(() => ({}));
                    if (r.ok && data.ok) {
                        setRuntimeFeedback((data.message || "Done") + (data.detail ? ": " + data.detail : ""), false, false);
                        loadRuntimeStatus();
                    } else {
                        setRuntimeFeedback((data.error || data.detail || "Main site script failed") + (r.status === 403 ? " (this machine or Tailscale)" : ""), true, false);
                    }
                } catch (e) {
                    setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
                } finally {
                    devRuntimeRestart8080Btn.disabled = false;
                }
            });
        }

        void loadRuntimeStatus();

        void loadDevStatus({ lightweight: true }).then(() => {
            scheduleNonCriticalDevLoads();
        });
        devMonitorIntervalId = window.setInterval(() => {
            if (!shouldRefreshDevMonitor()) {
                return;
            }
            void loadDevStatus({ lightweight: true }).then(() => {
                if (isDevCockpitPage) {
                    return;
                }
                void loadPendingApprovals();
                void loadTaskQueue();
            });
        }, monitorRefreshMs);
    }
    function initializePageBehaviors() {
        syncPageConfig();
        initializeDevMonitor();
    }

    initializePageBehaviors();
})();

(function miruFetchMissingImagesInit() {
    var endpoint = "/api/dev/fetch-missing-images";
    var inFlight = false;

    function currentButton() {
        return document.getElementById("fetch-images-btn");
    }

    function currentStatus() {
        return document.getElementById("fetch-images-status");
    }

    function setButtonState(label, disabled) {
        var btn = currentButton();
        if (!btn) return;
        btn.textContent = label;
        btn.disabled = Boolean(disabled);
    }

    function setStatusText(text) {
        var status = currentStatus();
        if (!status) return;
        status.textContent = text;
    }

    async function runFetch() {
        if (inFlight) return;
        inFlight = true;
        setButtonState("Fetching...", true);
        setStatusText("Fetching...");
        try {
            var response = await fetch(endpoint, {
                method: "POST",
                headers: {
                    Accept: "application/json",
                    "X-Requested-With": "miru-client-nav",
                },
                credentials: "same-origin",
            });
            var data = await response.json().catch(function () {
                return {};
            });
            if (response.ok && data && data.status === "started") {
                setStatusText("Started - check Pushover for results");
            } else {
                setStatusText("Error - check server logs");
            }
        } catch (_err) {
            setStatusText("Error - check server logs");
        } finally {
            inFlight = false;
            setButtonState("Fetch Missing Images", false);
        }
    }

    function onClick(event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var button = target.closest("#fetch-images-btn");
        if (!button) return;
        event.preventDefault();
        void runFetch();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
            document.addEventListener("click", onClick);
        });
    } else {
        document.addEventListener("click", onClick);
    }
})();

(function miruApprovalsInit() {
    const API = "/api/dev/pending-approvals";
    const APPROVE_API = "/api/dev/review-queue/approve";
    const REJECT_API = "/api/dev/review-queue/reject";

    function escAttr(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/"/g, "&quot;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function escHtml(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function post(url, body) {
        return fetch(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Accept: "application/json",
                "X-Requested-With": "miru-client-nav",
            },
            credentials: "same-origin",
            body: JSON.stringify(body),
        });
    }

    function updateApprovalCount() {
        const count = document.getElementById("miruApprovalsCount");
        const list = document.getElementById("miruApprovalsList");
        if (!count || !list) {
            return;
        }
        const n = list.querySelectorAll(".miruApprovalCard").length;
        if (!n) {
            count.textContent = "0 card(s) waiting for approval or review";
            list.innerHTML = "<p>No pending approvals.</p>";
            return;
        }
        count.textContent = n + " card(s) waiting for approval or review";
    }

    function closeModal() {
        const modal = document.getElementById("miruApprovalModal");
        if (!modal) {
            return;
        }
        modal.setAttribute("hidden", "hidden");
        const scrollY = modal._miruStoredScrollY || 0;
        document.body.style.position = "";
        document.body.style.top = "";
        document.body.style.width = "";
        window.scrollTo(0, scrollY);
        delete modal._miruStoredScrollY;
        delete modal.dataset.itemKey;
        delete modal.dataset.targetId;
    }

    function openModalForCard(card) {
        const modal = document.getElementById("miruApprovalModal");
        const nameEl = document.getElementById("miruModalName");
        const codeEl = document.getElementById("miruModalCode");
        const insightEl = document.getElementById("miruModalInsight");
        const metaEl = document.getElementById("miruModalMeta");
        const imageEl = document.getElementById("miruModalImage");
        if (!modal || !card || !nameEl || !codeEl || !insightEl || !metaEl || !imageEl) {
            return;
        }
        const itemKey = String(card.dataset.itemKey || "");
        const targetId = String(card.dataset.targetId || "");
        const title = String(card.dataset.cardTitle || "");
        const code = String(card.dataset.cardCode || "");
        const summary = String(card.dataset.cardSummary || "");
        const conf = String(card.dataset.cardConf || "");
        const reason = String(card.dataset.cardReason || "");
        const imageUrl = String(card.dataset.imageUrl || "").trim();

        modal.dataset.itemKey = itemKey;
        modal.dataset.targetId = targetId;
        nameEl.textContent = title;
        codeEl.textContent = code;
        insightEl.textContent = summary;
        metaEl.textContent = "Confidence: " + conf + " | Review reason: " + reason;
        if (imageUrl) {
            imageEl.hidden = false;
            imageEl.src = imageUrl;
            imageEl.alt = code || title || "Card image";
        } else {
            imageEl.hidden = true;
            imageEl.removeAttribute("src");
            imageEl.alt = "";
        }
        modal.removeAttribute("hidden");
        if (modal.parentElement !== document.body) {
            document.body.appendChild(modal);
        }
        const scrollY = window.scrollY;
        modal._miruStoredScrollY = scrollY;
        document.body.style.position = "fixed";
        document.body.style.top = `-${scrollY}px`;
        document.body.style.width = "100%";
        modal.scrollTop = 0;
        const box = modal.querySelector(".miruModalBox");
        if (box) box.scrollTop = 0;
    }

    function submitDecision(itemKey, targetId, approve) {
        const url = approve ? APPROVE_API : REJECT_API;
        return post(url, {
            item_key: itemKey || "",
            target_id: targetId || "",
        }).then(function (r) {
            if (!r.ok) {
                return r
                    .json()
                    .catch(function () {
                        return {};
                    })
                    .then(function (d) {
                        throw new Error((d && d.error) || "HTTP " + r.status);
                    });
            }
            const list = document.getElementById("miruApprovalsList");
            if (list) {
                const selector =
                    '.miruApprovalCard[data-item-key="' +
                    escAttr(itemKey || "") +
                    '"][data-target-id="' +
                    escAttr(targetId || "") +
                    '"]';
                const card = list.querySelector(selector);
                if (card) {
                    card.remove();
                }
            }
            updateApprovalCount();
        });
    }

    function render(items) {
        const list = document.getElementById("miruApprovalsList");
        const count = document.getElementById("miruApprovalsCount");
        if (!list || !count) {
            return;
        }
        const actionable = items.filter(function (i) {
            return (
                i &&
                i.queue_kind === "publication" &&
                i.status === "pending" &&
                (i.approval_state === "" || i.approval_state === "pending_review")
            );
        });
        count.textContent =
            actionable.length + " card(s) waiting for approval or review";
        list.innerHTML = "";
        if (!actionable.length) {
            list.innerHTML = "<p>No pending approvals.</p>";
            return;
        }
        actionable.forEach(function (item) {
            const title = item.card_name || item.card_code || item.target_id || "—";
            const code = item.card_code || item.target_id || "";
            const summary = item.summary_text || item.review_reason || "";
            const conf = item.confidence_score != null ? item.confidence_score : "";
            const reason = item.review_reason || "";
            const imageUrl =
                item.image_url != null ? String(item.image_url).trim() : "";

            // Determine insight type from summary or reason
            let insightType = "usage"; // default
            const summaryLower = summary.toLowerCase();
            const reasonLower = reason.toLowerCase();
            if (summaryLower.includes("ruling") || summaryLower.includes("official") || reasonLower.includes("ruling")) {
                insightType = "ruling";
            } else if (summaryLower.includes("price") || summaryLower.includes("cost") || summaryLower.includes("market")) {
                insightType = "price";
            } else if (summaryLower.includes("meta") || summaryLower.includes("format") || summaryLower.includes("tier")) {
                insightType = "meta";
            } else if (summaryLower.includes("strategy") || summaryLower.includes("combo") || summaryLower.includes("synergy")) {
                insightType = "strategy";
            }

            const imageMarkup = imageUrl
                ? '<div class="miruApprovalImageWrap"><img class="miruApprovalImage" src="' +
                escAttr(imageUrl) +
                '" alt="' +
                escAttr(code || title || "Card image") +
                '"></div>'
                : '<div class="miruApprovalImageWrap"></div>';
            const key = escAttr(item.item_key || "");
            const tid = escAttr(item.target_id || "");
            const card = document.createElement("div");
            card.className = "miruApprovalCard";
            card.dataset.itemKey = String(item.item_key || "");
            card.dataset.targetId = String(item.target_id || "");
            card.dataset.cardTitle = String(title || "");
            card.dataset.cardCode = String(code || "");
            card.dataset.cardSummary = String(summary || "");
            card.dataset.cardConf = String(conf == null ? "" : conf);
            card.dataset.cardReason = String(reason || "");
            card.dataset.imageUrl = String(imageUrl || "");
            card.dataset.insightType = insightType;
            card.dataset.confidence = String(conf || "0");

            // Get first line of insight for condensed view
            const firstLine = summary.split('\n')[0].trim();
            const truncatedFirstLine = firstLine.length > 80 ? firstLine.substring(0, 80) + "..." : firstLine;

            // Confidence band
            let confidenceBand = "medium";
            const confNum = parseFloat(conf);
            if (!isNaN(confNum)) {
                if (confNum >= 0.85) confidenceBand = "strong";
                else if (confNum < 0.6) confidenceBand = "low";
            }

            card.innerHTML =
                imageMarkup +
                '<div class="miruApprovalContent">' +
                '<div class="miruApprovalTitleRow">' +
                '<strong class="miruApprovalName">' + escHtml(title) + '</strong>' +
                '<span class="miruApprovalCode">' + escHtml(code) + '</span>' +
                '</div>' +
                '<div class="miruApprovalMetaRow">' +
                '<span class="miruApprovalTypeBadge">' + insightType + '</span>' +
                '<span class="miruApprovalConfidence">' + (conf ? conf + ' conf' : 'no conf') + '</span>' +
                '</div>' +
                '<div class="miruApprovalInsight">' + escHtml(truncatedFirstLine) + '</div>' +
                '<div class="miruApprovalExpanded">' +
                '<div class="miruApprovalExpandedDetail">' +
                '<span class="miruApprovalExpandedLabel">Full insight:</span>' +
                '<span class="miruApprovalExpandedValue">' + escHtml(summary) + '</span>' +
                '</div>' +
                '<div class="miruApprovalExpandedDetail">' +
                '<span class="miruApprovalExpandedLabel">Confidence:</span>' +
                '<span class="miruApprovalExpandedValue">' + (conf || '—') + '</span>' +
                '</div>' +
                '<div class="miruApprovalExpandedDetail">' +
                '<span class="miruApprovalExpandedLabel">Reason:</span>' +
                '<span class="miruApprovalExpandedValue">' + escHtml(reason || '—') + '</span>' +
                '</div>' +
                '<div class="miruApprovalExpandedDetail">' +
                '<span class="miruApprovalExpandedLabel">Type:</span>' +
                '<span class="miruApprovalExpandedValue">' + insightType + '</span>' +
                '</div>' +
                '</div>' +
                '<div class="miruApprovalActions">' +
                '<button type="button" class="miruApproveBtn" data-key="' + key + '" data-id="' + tid + '">Approve</button>' +
                '<button type="button" class="miruRejectBtn" data-key="' + key + '" data-id="' + tid + '">Reject</button>' +
                '</div>' +
                '</div>';
            list.appendChild(card);
        });
    }

    // Session tracking for progress
    let sessionReviewed = 0;
    let sessionTotal = 0;

    // Filter state
    let activeInsightFilter = 'all';
    let activeConfidenceFilter = 'any';

    function updateProgress() {
        const progressText = document.getElementById('miruApprovalProgress')?.querySelector('.miruProgressText');
        const progressFill = document.getElementById('miruApprovalProgress')?.querySelector('.miruProgressFill');
        if (progressText) {
            progressText.textContent = `${sessionReviewed} reviewed this session`;
        }
        if (progressFill && sessionTotal > 0) {
            progressFill.style.width = `${(sessionReviewed / sessionTotal) * 100}%`;
        }
    }

    function applyFilters() {
        const cards = document.querySelectorAll('.miruApprovalCard');
        cards.forEach(card => {
            const insightType = card.dataset.insightType || 'usage';
            const confidence = parseFloat(card.dataset.confidence || '0');

            let insightMatch = activeInsightFilter === 'all' || insightType === activeInsightFilter;
            let confidenceMatch = activeConfidenceFilter === 'any';

            if (!confidenceMatch) {
                if (activeConfidenceFilter === 'strong' && confidence >= 0.85) confidenceMatch = true;
                else if (activeConfidenceFilter === 'medium' && confidence >= 0.6 && confidence < 0.85) confidenceMatch = true;
                else if (activeConfidenceFilter === 'low' && confidence < 0.6) confidenceMatch = true;
            }

            card.style.display = (insightMatch && confidenceMatch) ? '' : 'none';
        });

        // Update count to show only visible cards
        const visibleCards = Array.from(cards).filter(card => card.style.display !== 'none');
        const count = document.getElementById('miruApprovalsCount');
        if (count) {
            count.textContent = `${visibleCards.length} card(s) waiting for approval or review`;
        }
    }

    function onListClick(e) {
        const approveBtn = e.target && e.target.closest ? e.target.closest(".miruApproveBtn") : null;
        const rejectBtn = e.target && e.target.closest ? e.target.closest(".miruRejectBtn") : null;

        if (approveBtn || rejectBtn) {
            const isApprove = Boolean(approveBtn);
            const btn = approveBtn || rejectBtn;
            const card = btn.closest('.miruApprovalCard');

            // Disable buttons on this card
            const cardButtons = card.querySelectorAll('.miruApproveBtn, .miruRejectBtn');
            cardButtons.forEach(b => b.disabled = true);

            submitDecision(
                btn.getAttribute("data-key") || "",
                btn.getAttribute("data-id") || "",
                isApprove,
            ).then(function () {
                // Remove card with animation
                card.style.transition = 'opacity 0.3s, transform 0.3s';
                card.style.opacity = '0';
                card.style.transform = 'translateX(-20px)';
                setTimeout(() => {
                    card.remove();
                    sessionReviewed++;
                    updateProgress();
                    updateApprovalCount();
                    applyFilters(); // Reapply filters to update count
                }, 300);
            }).catch(function (err) {
                window.alert(
                    (isApprove ? "Approve" : "Reject") +
                    " failed: " +
                    (err && err.message ? err.message : String(err)),
                );
                // Re-enable buttons on error
                cardButtons.forEach(b => b.disabled = false);
            });
            return;
        }

        // Handle card expansion (not button clicks)
        const card = e.target && e.target.closest ? e.target.closest(".miruApprovalCard") : null;
        if (card && !e.target.closest('.miruApprovalActions')) {
            // Close any other expanded card
            const expandedCards = document.querySelectorAll('.miruApprovalCard--expanded');
            expandedCards.forEach(expandedCard => {
                if (expandedCard !== card) {
                    expandedCard.classList.remove('miruApprovalCard--expanded');
                }
            });

            // Toggle current card
            card.classList.toggle('miruApprovalCard--expanded');
        }
    }

    function onFilterClick(e) {
        const pill = e.target && e.target.closest ? e.target.closest('.miruFilterPill') : null;
        if (!pill) return;

        const filterType = pill.dataset.filterType;
        const filterValue = pill.dataset.value;

        // Update active state
        const row = pill.closest('.miruFilterRow');
        row.querySelectorAll('.miruFilterPill').forEach(p => p.classList.remove('miruFilterPill--active'));
        pill.classList.add('miruFilterPill--active');

        // Update filter state
        if (filterType === 'insight') {
            activeInsightFilter = filterValue;
        } else if (filterType === 'confidence') {
            activeConfidenceFilter = filterValue;
        }

        applyFilters();
    }

    function load() {
        const countEl = document.getElementById("miruApprovalsCount");
        if (countEl) {
            countEl.textContent = "Loading...";
        }
        fetch(API, {
            headers: {
                Accept: "application/json",
                "X-Requested-With": "miru-client-nav",
            },
            credentials: "same-origin",
        })
            .then(function (r) {
                if (!r.ok) {
                    throw new Error("HTTP " + r.status);
                }
                return r.json();
            })
            .then(function (data) {
                const items = Array.isArray(data.items) ? data.items : [];
                sessionTotal = items.length;
                sessionReviewed = 0;
                render(items);
                updateProgress();
                applyFilters();
            })
            .catch(function (err) {
                const count = document.getElementById("miruApprovalsCount");
                if (count) {
                    count.textContent =
                        "Failed to load: " +
                        (err && err.message ? err.message : String(err));
                }
            });
    }

    function boot() {
        const list = document.getElementById("miruApprovalsList");
        const refreshBtn = document.getElementById("miruApprovalsRefresh");
        const filtersContainer = document.getElementById("miruApprovalFilters");

        if (!list || !document.getElementById("miruApprovals")) {
            return;
        }

        // Card click handler
        list.addEventListener("click", onListClick);

        // Filter click handler
        if (filtersContainer) {
            filtersContainer.addEventListener("click", onFilterClick);
        }

        // Refresh button
        if (refreshBtn) {
            refreshBtn.addEventListener("click", load);
        }

        load();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }
})();

(function () {
    var PULSE_COVERAGE_INTERVAL_MS = 4000;
    var PULSE_COVERAGE_PATH = "/api/dev/catalog-publish-coverage";

    function applyCoverageToDevPulse(coveragePercent) {
        var el = document.getElementById("devCoverageText");
        if (!el || coveragePercent == null) {
            return;
        }
        var num = Number(coveragePercent);
        if (!isFinite(num)) {
            return;
        }
        var pctStr = String(num);
        el.textContent = pctStr + "%";
    }

    function fetchPulseCoverage() {
        fetch(PULSE_COVERAGE_PATH, {
            credentials: "same-origin",
            headers: {
                Accept: "application/json",
                "X-Requested-With": "miru-client-nav",
            },
        })
            .then(function (r) {
                return r.json();
            })
            .then(function (data) {
                if (!data || data.ok !== true) {
                    return;
                }
                applyCoverageToDevPulse(data.coverage_percent);
            })
            .catch(function () { });
    }

    function startDevPulseCoverageSync() {
        if (!document.getElementById("devCoverageText")) {
            return;
        }
        fetchPulseCoverage();
        setInterval(fetchPulseCoverage, PULSE_COVERAGE_INTERVAL_MS);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", startDevPulseCoverageSync);
    } else {
        startDevPulseCoverageSync();
    }
})();
