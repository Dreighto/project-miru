const MIRU_AI_TEST_HOOKS = (() => {
    function selectTextTarget(target) {
        if (!target || typeof target.select !== "function") {
            return false;
        }
        if (typeof target.focus === "function") {
            target.focus();
        }
        target.select();
        if (typeof target.setSelectionRange === "function") {
            target.setSelectionRange(0, String(target.value || "").length);
        }
        return true;
    }

    function attemptLegacyCopy({ value, documentRef, target }) {
        if (!documentRef || typeof documentRef.execCommand !== "function") {
            return false;
        }

        let workingTarget = target;
        let cleanup = null;
        if (!workingTarget || typeof workingTarget.select !== "function") {
            if (!documentRef.body || typeof documentRef.createElement !== "function") {
                return false;
            }
            workingTarget = documentRef.createElement("textarea");
            workingTarget.value = value;
            workingTarget.setAttribute("readonly", "readonly");
            workingTarget.style.position = "fixed";
            workingTarget.style.top = "0";
            workingTarget.style.left = "0";
            workingTarget.style.opacity = "0";
            documentRef.body.appendChild(workingTarget);
            cleanup = () => {
                if (workingTarget && typeof workingTarget.remove === "function") {
                    workingTarget.remove();
                }
            };
        } else {
            workingTarget.value = value;
        }

        const selected = selectTextTarget(workingTarget);
        let copied = false;
        try {
            copied = selected && documentRef.execCommand("copy");
        } catch (error) {
            copied = false;
        }
        if (cleanup) {
            cleanup();
        }
        return Boolean(copied);
    }

    async function copyTextWithFallback({
        text,
        clipboard,
        documentRef,
        target,
        onManualFallback,
        setFeedback,
        successText,
        fallbackText,
        emptyText,
        errorText,
    }) {
        const value = String(text || "");
        const notify = typeof setFeedback === "function" ? setFeedback : () => {};

        if (!value.trim()) {
            notify(emptyText || "There is no result to copy yet.", "warn");
            return "empty";
        }

        if (clipboard && typeof clipboard.writeText === "function") {
            try {
                await clipboard.writeText(value);
                notify(successText || "Copied.", "success");
                return "clipboard";
            } catch (error) {
                // Fall through to legacy copy and manual fallback.
            }
        }

        if (attemptLegacyCopy({ value, documentRef, target })) {
            notify(successText || "Copied.", "success");
            return "legacy";
        }

        if (typeof onManualFallback === "function") {
            onManualFallback();
        }
        notify(
            fallbackText || "Clipboard copy is blocked here. The result is ready for manual copy.",
            errorText ? "error" : "warn",
        );
        return "manual";
    }

    async function pasteTextWithFallback({
        clipboard,
        target,
        setFeedback,
        successText,
        fallbackText,
        emptyText,
    }) {
        const notify = typeof setFeedback === "function" ? setFeedback : () => {};
        if (!target) {
            notify(emptyText || "There is nowhere to paste yet.", "warn");
            return "missing";
        }

        if (clipboard && typeof clipboard.readText === "function") {
            try {
                const value = String(await clipboard.readText() || "");
                if (!value.trim()) {
                    notify(emptyText || "Clipboard is empty.", "warn");
                    return "empty";
                }
                target.value = value;
                if (typeof target.focus === "function") {
                    target.focus();
                }
                notify(successText || "Pasted.", "success");
                return "clipboard";
            } catch (error) {
                // Fall through to manual paste guidance.
            }
        }

        if (typeof target.focus === "function") {
            target.focus();
        }
        notify(
            fallbackText || "Browser paste is blocked here. Tap the field and use your device paste action.",
            "warn",
        );
        return "manual";
    }

    function buildRunPayload({ mode, requestText, filePath }) {
        return {
            mode: String(mode || "card knowledge").trim().toLowerCase(),
            request_text: String(requestText || "").trim(),
            file_path: String(filePath || "").trim(),
        };
    }

    function resetViewState({
        requestField,
        resultReadableField,
        resultField,
        errorField,
        resultMetaField,
        errorMetaField,
        setCopyFeedback,
        hidePanels,
        hideManualCopy,
    }) {
        if (requestField) {
            requestField.value = "";
        }
        if (resultReadableField) {
            resultReadableField.textContent = "";
        }
        if (resultField) {
            resultField.value = "";
        }
        if (errorField) {
            errorField.textContent = "";
        }
        if (resultMetaField) {
            resultMetaField.textContent = "";
        }
        if (errorMetaField) {
            errorMetaField.textContent = "";
        }
        if (typeof setCopyFeedback === "function") {
            setCopyFeedback("", "");
        }
        if (typeof hidePanels === "function") {
            hidePanels();
        }
        if (typeof hideManualCopy === "function") {
            hideManualCopy();
        }
    }

    function buildModeHintHtml(modeConfig) {
        const config = modeConfig || {};
        const parts = [
            `<strong>${escapeHtmlForHint(config.label || "Card Lookup")}</strong>`,
        ];
        if (config.caption) {
            parts.push(`<div class="modeHintRow"><span class="modeHintLabel">Best for:</span>${escapeHtmlForHint(config.caption)}</div>`);
        }
        if (config.use_case) {
            parts.push(`<div class="modeHintRow"><span class="modeHintLabel">Use it when:</span>${escapeHtmlForHint(config.use_case)}</div>`);
        }
        if (config.answer_shape) {
            parts.push(`<div class="modeHintRow"><span class="modeHintLabel">Answer style:</span>${escapeHtmlForHint(config.answer_shape)}</div>`);
        } else if (config.hint) {
            parts.push(`<div class="modeHintRow">${escapeHtmlForHint(config.hint)}</div>`);
        }
        return parts.join("");
    }

    function escapeHtmlForHint(value) {
        return String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    return {
        copyTextWithFallback,
        pasteTextWithFallback,
        buildRunPayload,
        resetViewState,
        selectTextTarget,
        buildModeHintHtml,
        attemptLegacyCopy,
    };
})();

if (typeof window !== "undefined") {
    window.MIRU_AI_TEST_HOOKS = MIRU_AI_TEST_HOOKS;
}

(function () {
    const config = window.MIRU_AI_CONFIG || {};
    const RUN_API_PATH = "/api/run";
    const modeConfigs = Array.isArray(config.modeConfigs) ? config.modeConfigs : [];
    const modeMap = Object.fromEntries(modeConfigs.map((item) => [item.key, item]));
    const {
        copyTextWithFallback,
        pasteTextWithFallback,
        buildRunPayload,
        resetViewState,
        selectTextTarget,
        buildModeHintHtml,
    } = MIRU_AI_TEST_HOOKS;
    let navigationInitialized = false;
    let devMonitorIntervalId = 0;
    let latestDevVoyage = null;
    let latestRuntimeStatus = null;
    let voyageMapResizeBound = false;
    const DEV_SECTION_REFRESH_MS = 45000;
    const devDeferredSections = {
        monitor: { url: "/api/dev/monitor-panel", loaded: false, inFlight: null, lastLoadedAt: 0 },
        imageCoverage: { url: "/api/dev/image-coverage", loaded: false, inFlight: null, lastLoadedAt: 0 },
        validationAudit: { url: "/api/dev/validation-audit", loaded: false, inFlight: null, lastLoadedAt: 0 },
        resourceMetrics: { url: "/api/dev/resource-metrics", loaded: false, inFlight: null, lastLoadedAt: 0 },
    };

    function resetDevDeferredSections() {
        Object.values(devDeferredSections).forEach((entry) => {
            entry.loaded = false;
            entry.inFlight = null;
            entry.lastLoadedAt = 0;
        });
    }

    function getCurrentDevTab() {
        const active = document.querySelector(".devConsoleTab[data-dev-tab].isActive");
        return active ? String(active.dataset.devTab || "") : "";
    }

    function shouldRefreshDevSection(sectionKey, { force = false } = {}) {
        const entry = devDeferredSections[sectionKey];
        if (!entry) {
            return false;
        }
        if (force || !entry.loaded) {
            return true;
        }
        return Date.now() - Number(entry.lastLoadedAt || 0) >= DEV_SECTION_REFRESH_MS;
    }

    function prefersCompactScroll() {
        return window.matchMedia && window.matchMedia("(max-width: 720px)").matches;
    }

    function revealPanel(panel) {
        if (!panel) {
            return;
        }
        panel.classList.remove("isHidden");
        if (prefersCompactScroll()) {
            panel.scrollIntoView({ behavior: "auto", block: "start" });
            return;
        }
        panel.scrollIntoView({ behavior: "smooth", block: "start" });
    }

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

    function shouldHandleClientNavigation(event, link) {
        if (!link || !link.href) {
            return false;
        }
        if (event.defaultPrevented) {
            return false;
        }
        if (event.button !== 0) {
            return false;
        }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return false;
        }
        if (link.target && link.target !== "_self") {
            return false;
        }
        if (link.hasAttribute("download")) {
            return false;
        }

        const destination = new URL(link.href, window.location.origin);
        if (destination.origin !== window.location.origin) {
            return false;
        }
        const currentUrl = new URL(window.location.href);
        if (destination.pathname === currentUrl.pathname && destination.search === currentUrl.search && destination.hash !== "") {
            return false;
        }

        return true;
    }

    function updateNavFromDocument(nextDocument) {
        const currentNav = document.querySelector(".navLinks");
        const nextNav = nextDocument.querySelector(".navLinks");
        if (currentNav && nextNav) {
            currentNav.innerHTML = nextNav.innerHTML;
        }
    }

    function replaceBodyPageClass(nextDocument) {
        if (!nextDocument || !nextDocument.body) {
            return;
        }
        document.body.className = nextDocument.body.className;
    }

    function swapMainContent(nextDocument) {
        const currentMain = getMainContent();
        const nextMain = nextDocument.getElementById("miruMainContent");
        if (!currentMain || !nextMain) {
            throw new Error("Miru navigation could not find the main content container.");
        }
        currentMain.replaceWith(nextMain);
    }

    async function navigateTo(url, { replace = false } = {}) {
        const destination = new URL(url, window.location.origin);
        const currentUrl = new URL(window.location.href);
        if (
            !replace &&
            destination.pathname === currentUrl.pathname &&
            destination.search === currentUrl.search &&
            destination.hash === currentUrl.hash
        ) {
            return;
        }

        const response = await fetch(destination.pathname + destination.search + destination.hash, {
            headers: {
                "X-Requested-With": "miru-client-nav",
            },
            credentials: "same-origin",
        });
        if (!response.ok) {
            throw new Error(`Miru navigation failed with status ${response.status}.`);
        }

        const html = await response.text();
        const parser = new DOMParser();
        const nextDocument = parser.parseFromString(html, "text/html");
        const nextMain = nextDocument.getElementById("miruMainContent");
        if (!nextMain) {
            throw new Error("Miru navigation response did not include #miruMainContent.");
        }

        updateNavFromDocument(nextDocument);
        replaceBodyPageClass(nextDocument);
        swapMainContent(nextDocument);
        document.title = nextDocument.title || document.title;
        syncPageConfig();
        window.scrollTo({ top: 0, behavior: "auto" });

        if (replace) {
            window.history.replaceState({ path: destination.pathname }, "", destination.pathname + destination.search + destination.hash);
        } else {
            window.history.pushState({ path: destination.pathname }, "", destination.pathname + destination.search + destination.hash);
        }

        initializePageBehaviors();
    }

    async function handleClientNavigation(url, { replace = false } = {}) {
        try {
            await navigateTo(url, { replace });
        } catch (error) {
            window.location.assign(url);
        }
    }

    function initializePersistentNavigation() {
        if (navigationInitialized) {
            return;
        }
        if (
            typeof document === "undefined" ||
            typeof document.addEventListener !== "function" ||
            typeof window === "undefined" ||
            typeof window.addEventListener !== "function"
        ) {
            return;
        }
        navigationInitialized = true;

        document.addEventListener("click", (event) => {
            const link = event.target.closest(".navLink, .brandLink, a[data-client-nav]");
            if (!shouldHandleClientNavigation(event, link)) {
                return;
            }
            event.preventDefault();
            void handleClientNavigation(link.href);
        });

        window.addEventListener("popstate", () => {
            void handleClientNavigation(window.location.href, { replace: true });
        });
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

    function switchDevConsoleTab(nextTab, { updateHash = false } = {}) {
        const root = document.getElementById("devMonitor");
        if (!root) {
            return;
        }
        const advancedConsole = document.getElementById("devAdvancedConsole");
        const fallbackTab = root.dataset.devDefaultTab || "overview";
        const requestedTab = String(nextTab || fallbackTab);
        const activeTab = document.querySelector(`[data-dev-panel="${requestedTab}"]`) ? requestedTab : fallbackTab;
        currentValidationAuditCardCode = currentValidationAuditCardCode || "";
        if (advancedConsole && activeTab !== "overview") {
            advancedConsole.open = true;
        }
        document.querySelectorAll(".devConsoleTab[data-dev-tab]").forEach((button) => {
            const selected = button.dataset.devTab === activeTab;
            button.classList.toggle("isActive", selected);
            button.setAttribute("aria-selected", selected ? "true" : "false");
            button.tabIndex = selected ? 0 : -1;
        });
        document.querySelectorAll("[data-dev-panel]").forEach((panel) => {
            const shouldShow = panel.dataset.devPanel === activeTab;
            panel.classList.toggle("isHidden", !shouldShow);
            panel.hidden = !shouldShow;
        });
        if (updateHash && window.history && typeof window.history.replaceState === "function") {
            const url = new URL(window.location.href);
            url.hash = activeTab === fallbackTab ? "" : activeTab;
            window.history.replaceState(window.history.state, "", url.toString());
        }
        void loadDeferredPanelsForTab(activeTab);
    }

    function initializeDevConsoleTabs() {
        const root = document.getElementById("devMonitor");
        if (!root) {
            return;
        }
        if (root.dataset.devTabsBound !== "true") {
            root.dataset.devTabsBound = "true";
            root.addEventListener("click", (event) => {
                const tabButton = event.target instanceof HTMLElement ? event.target.closest("[data-dev-tab]") : null;
                if (tabButton) {
                    switchDevConsoleTab(tabButton.dataset.devTab, { updateHash: true });
                    return;
                }
                const learningEvent = event.target instanceof HTMLElement ? event.target.closest(".devLearningEvent[data-card-code]") : null;
                if (!learningEvent) {
                    return;
                }
                switchDevConsoleTab("validation", { updateHash: true });
                const validationPanel = document.getElementById("devValidationPanel");
                const urlBase = (validationPanel && validationPanel.dataset.validationAuditUrlBase) || "";
                void loadDeferredPanelsForTab("validation").finally(() => {
                    const refreshedPanel = document.getElementById("devValidationPanel");
                    const refreshedUrlBase = (refreshedPanel && refreshedPanel.dataset.validationAuditUrlBase) || urlBase;
                    void inspectValidationAudit(learningEvent.dataset.cardCode || "", refreshedUrlBase);
                });
            });
        }
        const hashTab = String(window.location.hash || "").replace(/^#/, "");
        switchDevConsoleTab(hashTab || root.dataset.devDefaultTab || "overview");
    }

    function updateDevMetricCards(selector, items, keyName) {
        const lookup = new Map((items || []).map((item) => [item[keyName], item]));
        document.querySelectorAll(selector).forEach((card) => {
            const key = card.dataset[keyName];
            const item = lookup.get(key);
            if (!item) {
                return;
            }
            const value = card.querySelector('[data-field="value"]');
            const detail = card.querySelector('[data-field="detail"]');
            if (value) {
                value.textContent = item.value || "";
            }
            if (detail) {
                detail.textContent = item.detail || "";
            }
            if (selector === ".devResourceCard") {
                const bar = card.querySelector('[data-field="bar"]');
                if (bar) {
                    bar.style.width = `${Math.max(0, Math.min(Number(item.percent || 0), 100))}%`;
                }
                card.classList.toggle("isUnavailable", !item.available);
            }
        });
    }

    function renderDevIssueCard(issueKey, issue) {
        const card = document.querySelector(`.devIssueCard[data-issue-key="${issueKey}"]`);
        if (!card || !issue) {
            return;
        }
        const status = card.querySelector('[data-field="status"]');
        const detail = card.querySelector('[data-field="detail"]');
        const list = card.querySelector('[data-field="items"]');
        card.classList.remove("devIssueCard--good", "devIssueCard--warn", "devIssueCard--neutral");
        card.classList.add(`devIssueCard--${issue.tone || "neutral"}`);
        if (status) {
            status.textContent = issue.status || "Unavailable";
            status.className = `statusPill statusPill--${issue.tone || "neutral"}`;
        }
        if (detail) {
            detail.textContent = issue.detail || "";
        }
        if (list) {
            list.innerHTML = "";
            const items = Array.isArray(issue.items) && issue.items.length ? issue.items : ["Everything looks ready from this page."];
            items.forEach((message) => {
                const li = document.createElement("li");
                li.textContent = String(message || "");
                list.appendChild(li);
            });
        }
    }

    function setImageSource(id, src, alt) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }
        if (src) {
            element.src = src;
        }
        if (typeof alt === "string") {
            element.alt = alt;
        }
    }

    function setText(id, text) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = text || "";
        }
    }

    function renderVoyageLog(containerId, items) {
        const container = document.getElementById(containerId);
        if (!container) {
            return;
        }
        container.innerHTML = "";
        (items || []).forEach((item) => {
            const entry = document.createElement("li");
            entry.className = `voyageLogEntry voyageLogEntry--${item.tone || "travel"}`;
            const icon = document.createElement("img");
            icon.src = item.icon_url || "";
            icon.alt = "";
            const text = document.createElement("span");
            text.textContent = item.message || "";
            entry.appendChild(icon);
            entry.appendChild(text);
            container.appendChild(entry);
        });
    }

    function describeVoyageLearningStage(stage, label) {
        const normalized = String(stage || "").trim().toLowerCase();
        const stageLabel = label || "this stage";
        if (normalized.includes("planned")) {
            return `${stageLabel} means the next card-knowledge milestone is defined, but Miru is still very early and has not started moving through it yet.`;
        }
        if (normalized.includes("discover")) {
            return `${stageLabel} means Miru is still gathering the card facts it needs before they are organized into something more dependable.`;
        }
        if (normalized.includes("track") || normalized.includes("catalog")) {
            return `${stageLabel} means Miru is organizing known card facts so basic answers become more reliable, but the work is still early.`;
        }
        if (normalized.includes("verify")) {
            return `${stageLabel} means Miru is locking card facts in with better evidence so verified lookup gets stronger, not that Miru is close to advanced play intelligence.`;
        }
        return `${stageLabel} is part of Miru's early push toward more dependable card intelligence.`;
    }

    function getConservativeVoyageFrame(voyage) {
        if (voyage && (
            voyage.stage_title ||
            voyage.stage_detail ||
            voyage.route_progress ||
            voyage.can_do_now ||
            voyage.next_title
        )) {
            const currentIsland = voyage && voyage.current_island ? voyage.current_island : {};
            const nextIsland = voyage && voyage.next_island ? voyage.next_island : null;
            return {
                currentPhase: String(voyage.stage || currentIsland.name || "Approaching Reverse Mountain"),
                currentFooting: String(voyage.sea_label || currentIsland.stage || "Early structured learning"),
                routeSummary: String(voyage.route_progress || "Miru is still building dependable card knowledge."),
                stageTitle: String(voyage.stage_title || voyage.stage || "Approaching Reverse Mountain"),
                stageDetail: String(voyage.stage_detail || voyage.boss_summary || "Miru is still in early structured learning."),
                canDoNow: String(voyage.can_do_now || "Build dependable card knowledge"),
                canDoNowDetail: String(voyage.can_do_now_detail || "Miru can keep collecting, checking, and organizing card facts."),
                stillLearning: String(voyage.still_learning || "Broader trusted coverage"),
                nextFocus: String(voyage.next_title || (nextIsland && nextIsland.name) || "Steadier trusted coverage"),
                nextDetail: String(voyage.next_detail || "The next step is widening trusted coverage, not advanced strategy."),
                stillLearningDetail: String(voyage.still_learning_detail || "Miru still needs much more verified coverage before higher-level reasoning is trustworthy."),
            };
        }
        const progress = Number(voyage && voyage.progress_percent || 0);
        const learningStage = String(voyage && voyage.learning_stage || "").trim().toLowerCase();
        if (progress >= 45 || learningStage.includes("verify")) {
            return {
                currentPhase: "Early Grand Line prep",
                currentFooting: "Verified card coverage",
                routeSummary: "Miru is extending verified card coverage before anything more ambitious.",
                stageTitle: "Building trusted card coverage",
                stageDetail: "Useful verified card intelligence",
                canDoNow: "Grounded verified card lookup",
                canDoNowDetail: "Miru can answer card-fact questions more reliably from verified data.",
                stillLearning: "Broader structured coverage",
                nextFocus: "Broader structured card knowledge",
                nextDetail: "Still not near deck, matchup, or tournament reasoning.",
                stillLearningDetail: "Advanced strategy and matchup reasoning are still well ahead.",
            };
        }
        if (progress >= 20 || learningStage.includes("track") || learningStage.includes("catalog")) {
            return {
                currentPhase: "East Blue",
                currentFooting: "Reliable card foundations",
                routeSummary: "Miru is organizing card knowledge so lookup answers stay dependable.",
                stageTitle: "Reliable card lookup",
                stageDetail: "Early verified card knowledge",
                canDoNow: "Grounded verified card lookup",
                canDoNowDetail: "Useful for card facts, citations, and structured lookup answers.",
                stillLearning: "Stronger verification",
                nextFocus: "Stronger verification",
                nextDetail: "Still far from advanced strategic intelligence.",
                stillLearningDetail: "Miru still needs much more work before higher-level play intelligence.",
            };
        }
        return {
            currentPhase: "East Blue foundations",
            currentFooting: "Verified card foundations",
            routeSummary: "Miru is building dependable verified card answers first.",
            stageTitle: "Card basics",
            stageDetail: "Very early verified card knowledge",
            canDoNow: "Basic verified card lookup",
            canDoNowDetail: "Miru can ground simple card answers in verified information.",
            stillLearning: "Reliable card coverage",
            nextFocus: "Reliable card lookup",
            nextDetail: "Miru still needs much more work before higher-level reasoning.",
            stillLearningDetail: "Coverage depth and stronger verification still come before advanced intelligence.",
        };
    }

    const ROADMAP_ARC_TO_ROUTE_KEY = {
        "east blue": "east_blue",
        "alabasta": "alabasta",
        "water 7": "water_7",
        "dressrosa": "dressrosa",
        "wano": "wano",
        "egghead": "egghead",
    };
    const VOYAGE_STAGE_ART = {
        east_blue: {
            key: "east_blue",
            name: "East Blue",
            shortName: "East Blue",
            spriteUrl: "/static/icons/miru_voyage/islands/island_east_blue.png",
            mapX: 10,
            mapY: 75,
        },
        reverse_mountain: {
            key: "reverse_mountain",
            name: "Reverse Mountain",
            shortName: "Reverse Mountain",
            spriteUrl: "/static/icons/miru_voyage/islands/island_reverse_mountain.png",
            mapX: 23,
            mapY: 57,
        },
        alabasta: {
            key: "alabasta",
            name: "Alabasta",
            shortName: "Alabasta",
            spriteUrl: "/static/icons/miru_voyage/islands/island_alabasta.png",
            mapX: 35,
            mapY: 66,
        },
        skypiea: {
            key: "skypiea",
            name: "Skypiea",
            shortName: "Skypiea",
            spriteUrl: "/static/icons/miru_voyage/islands/island_skypiea.png",
            mapX: 45,
            mapY: 42,
        },
        water_7: {
            key: "water_7",
            name: "Water 7",
            shortName: "Water 7",
            spriteUrl: "/static/icons/miru_voyage/islands/island_water_7.png",
            mapX: 56,
            mapY: 56,
        },
        thriller_bark: {
            key: "thriller_bark",
            name: "Thriller Bark",
            shortName: "Thriller Bark",
            spriteUrl: "/static/icons/miru_voyage/islands/island_thriller_bark.png",
            mapX: 66,
            mapY: 42,
        },
        fishman_island: {
            key: "fishman_island",
            name: "Fishman Island",
            shortName: "Fishman Island",
            spriteUrl: "/static/icons/miru_voyage/islands/island_fishman_island.png",
            mapX: 74,
            mapY: 64,
        },
        dressrosa: {
            key: "dressrosa",
            name: "Dressrosa",
            shortName: "Dressrosa",
            spriteUrl: "/static/icons/miru_voyage/islands/island_dressrosa.png",
            mapX: 83,
            mapY: 48,
        },
        whole_cake: {
            key: "whole_cake",
            name: "Whole Cake",
            shortName: "Whole Cake",
            spriteUrl: "/static/icons/miru_voyage/islands/island_whole_cake.png",
            mapX: 90,
            mapY: 62,
        },
        wano: {
            key: "wano",
            name: "Wano",
            shortName: "Wano",
            spriteUrl: "/static/icons/miru_voyage/islands/island_wano.png",
            mapX: 86,
            mapY: 29,
        },
        egghead: {
            key: "egghead",
            name: "Egghead",
            shortName: "Egghead",
            spriteUrl: "/static/icons/miru_voyage/islands/island_egghead.png",
            mapX: 69,
            mapY: 15,
        },
        laugh_tale: {
            key: "laugh_tale",
            name: "Laugh Tale",
            shortName: "Laugh Tale",
            spriteUrl: "/static/icons/miru_voyage/islands/island_laugh_tale.png",
            mapX: 50,
            mapY: 11,
        },
    };
    const VOYAGE_STATUS_MARKER = {
        completed: "/static/icons/miru_voyage/routes/route_completed_marker.png",
        current: "/static/icons/miru_voyage/routes/route_current_ship_marker.png",
        next: "/static/icons/miru_voyage/routes/route_next_destination_marker.png",
        planned: "/static/icons/miru_voyage/routes/route_checkpoint_marker.png",
        finish: "/static/icons/miru_voyage/routes/route_finish_marker.png",
    };
    const VOYAGE_SHARED_FALLBACKS = {
        ship: "/static/icons/miru_voyage/ships/polar_tang_idle.png",
        captain_log: "/static/icons/miru_voyage/ui/ui_log_pose.png",
        compass: "/static/icons/miru_voyage/ui/ui_compass_open.png",
        vivre: "/static/icons/miru_voyage/ui/ui_vivre_card.png",
    };

    function summarizeStageHeadline(items, fallback) {
        return Array.isArray(items) && items.length ? String(items[0]) : fallback;
    }

    function summarizeStageDetail(items, fallback) {
        if (Array.isArray(items) && items.length > 1) {
            return items.slice(1, 3).map((item) => String(item)).join(" â€¢ ");
        }
        return fallback;
    }

    function roadmapArcToRouteKey(value) {
        return ROADMAP_ARC_TO_ROUTE_KEY[String(value || "").trim().toLowerCase()] || "";
    }

    function normalizeVoyageKey(value) {
        return String(value || "")
            .trim()
            .toLowerCase()
            .replace(/&/g, "and")
            .replace(/[^a-z0-9]+/g, "_")
            .replace(/^_+|_+$/g, "");
    }

    function looksLikeBrokenVoyageArt(src) {
        const normalized = String(src || "").trim().toLowerCase();
        if (!normalized) {
            return true;
        }
        return [
            "/static/icons/miru-fruit.png",
            "/static/icons/miru_voyage/ui/ui_compass_open.png",
            "/static/icons/miru_voyage/ui/ui_vivre_card.png",
            "/static/icons/miru_voyage/ui/ui_log_pose.png",
        ].some((token) => normalized.includes(token));
    }

    function getVoyageStageAsset(routeKey) {
        return VOYAGE_STAGE_ART[routeKey] || null;
    }

    function deriveVoyageRouteKey(...values) {
        for (const value of values) {
            const direct = normalizeVoyageKey(value);
            if (VOYAGE_STAGE_ART[direct]) {
                return direct;
            }
            const byArc = roadmapArcToRouteKey(value);
            if (byArc) {
                return byArc;
            }
        }
        return "";
    }

    function resolveVoyageArt(src, routeKey) {
        if (!looksLikeBrokenVoyageArt(src)) {
            return String(src || "").trim();
        }
        const asset = getVoyageStageAsset(routeKey);
        return asset ? asset.spriteUrl : "";
    }

    function normalizeVoyageAssets(assets) {
        const normalizedAssets = { ...(assets || {}) };
        Object.entries(VOYAGE_SHARED_FALLBACKS).forEach(([key, fallback]) => {
            if (looksLikeBrokenVoyageArt(normalizedAssets[key])) {
                normalizedAssets[key] = fallback;
            }
        });
        return normalizedAssets;
    }

    function normalizeVoyageNode(node, fallbackStatus) {
        const routeKey = deriveVoyageRouteKey(
            node && node.key,
            node && node.route_key,
            node && node.name,
            node && node.short_name,
            node && node.stage,
        );
        const asset = getVoyageStageAsset(routeKey);
        const status = String((node && node.status) || fallbackStatus || "planned");
        return {
            ...(node || {}),
            key: routeKey || String((node && node.key) || "").trim(),
            name: String((node && node.name) || (asset && asset.name) || "Planned"),
            short_name: String((node && node.short_name) || (asset && asset.shortName) || (node && node.name) || "Planned"),
            map_x: Number(node && node.map_x != null ? node.map_x : (asset && asset.mapX) != null ? asset.mapX : 0),
            map_y: Number(node && node.map_y != null ? node.map_y : (asset && asset.mapY) != null ? asset.mapY : 0),
            status,
            sprite_url: resolveVoyageArt(node && node.sprite_url, routeKey),
            marker_url: looksLikeBrokenVoyageArt(node && node.marker_url)
                ? (VOYAGE_STATUS_MARKER[status] || VOYAGE_STATUS_MARKER.planned)
                : String(node && node.marker_url || ""),
        };
    }

    function buildFallbackVoyageNode(routeKey, status) {
        const asset = getVoyageStageAsset(routeKey);
        if (!asset) {
            return null;
        }
        return normalizeVoyageNode({
            key: routeKey,
            name: asset.name,
            short_name: asset.shortName,
            map_x: asset.mapX,
            map_y: asset.mapY,
            status: status || "planned",
            sprite_url: asset.spriteUrl,
            marker_url: VOYAGE_STATUS_MARKER[status || "planned"] || VOYAGE_STATUS_MARKER.planned,
        }, status);
    }

    function findVoyageRouteIndex(routeNodes, routeKey, stage) {
        if (!Array.isArray(routeNodes) || !routeNodes.length) {
            return -1;
        }
        if (routeKey) {
            const directIndex = routeNodes.findIndex((node) => deriveVoyageRouteKey(node && node.key, node && node.name, node && node.short_name, node && node.stage) === routeKey);
            if (directIndex !== -1) {
                return directIndex;
            }
        }
        const stageRouteKey = deriveVoyageRouteKey(stage && stage.voyage_arc, stage && stage.label);
        if (stageRouteKey) {
            return routeNodes.findIndex((node) => deriveVoyageRouteKey(node && node.key, node && node.name, node && node.short_name, node && node.stage) === stageRouteKey);
        }
        return -1;
    }

    function cloneVoyageNode(node) {
        return {
            ...node,
            bosses: Array.isArray(node && node.bosses)
                ? node.bosses.map((boss) => ({ ...boss }))
                : [],
        };
    }

    function normalizeVoyageWithRoadmap(voyage, intelligence) {
        const normalizedVoyage = voyage && typeof voyage === "object"
            ? {
                ...voyage,
                assets: normalizeVoyageAssets(voyage.assets || {}),
                current_island: normalizeVoyageNode(voyage.current_island || {}, "current"),
                next_island: voyage.next_island ? normalizeVoyageNode(voyage.next_island, "next") : null,
                next_boss: voyage.next_boss ? { ...voyage.next_boss } : null,
                ship_position: { ...(voyage.ship_position || {}) },
                route_nodes: Array.isArray(voyage.route_nodes) ? voyage.route_nodes.map((node) => normalizeVoyageNode(cloneVoyageNode(node), node && node.status)) : [],
            }
            : {
                assets: normalizeVoyageAssets({}),
                current_island: normalizeVoyageNode({}, "current"),
                next_island: null,
                next_boss: null,
                ship_position: {},
                route_nodes: [],
            };
        const currentStage = intelligence && typeof intelligence.current_stage === "object" ? intelligence.current_stage : null;
        const nextStage = intelligence && typeof intelligence.next_stage === "object" ? intelligence.next_stage : null;
        if (!normalizedVoyage.route_nodes.length || !currentStage) {
            return { voyage: normalizedVoyage, currentStage, nextStage };
        }

        const currentRouteKey = deriveVoyageRouteKey(currentStage && currentStage.voyage_arc, normalizedVoyage.current_island && normalizedVoyage.current_island.key);
        const nextRouteKey = deriveVoyageRouteKey(nextStage && nextStage.voyage_arc, normalizedVoyage.next_island && normalizedVoyage.next_island.key);
        const currentIndex = findVoyageRouteIndex(normalizedVoyage.route_nodes, currentRouteKey, currentStage);
        if (currentIndex === -1) {
            const currentFallbackNode = buildFallbackVoyageNode(currentRouteKey, "current");
            const nextFallbackNode = buildFallbackVoyageNode(nextRouteKey, "next");
            if (currentFallbackNode) {
                normalizedVoyage.current_island = {
                    ...currentFallbackNode,
                    ...normalizedVoyage.current_island,
                    sprite_url: resolveVoyageArt(normalizedVoyage.current_island && normalizedVoyage.current_island.sprite_url, currentRouteKey),
                };
                normalizedVoyage.ship_position = {
                    ...normalizedVoyage.ship_position,
                    x: Number(normalizedVoyage.current_island.map_x ?? currentFallbackNode.map_x ?? normalizedVoyage.ship_position.x ?? 50),
                    y: Number(normalizedVoyage.current_island.map_y ?? currentFallbackNode.map_y ?? normalizedVoyage.ship_position.y ?? 50),
                };
            }
            if (nextFallbackNode) {
                normalizedVoyage.next_island = {
                    ...(normalizedVoyage.next_island || {}),
                    ...nextFallbackNode,
                    sprite_url: resolveVoyageArt(normalizedVoyage.next_island && normalizedVoyage.next_island.sprite_url, nextRouteKey),
                };
            }
            return { voyage: normalizedVoyage, currentStage, nextStage };
        }

        const derivedNextIndex = findVoyageRouteIndex(normalizedVoyage.route_nodes, nextRouteKey, nextStage);
        const resolvedNextIndex = derivedNextIndex > currentIndex ? derivedNextIndex : currentIndex + 1;
        normalizedVoyage.route_nodes = normalizedVoyage.route_nodes.map((node, index, list) => {
            let status = "planned";
            if (index < currentIndex) {
                status = "completed";
            } else if (index === currentIndex) {
                status = "current";
            } else if (index === resolvedNextIndex) {
                status = "next";
            } else if (index === list.length - 1) {
                status = "finish";
            }
            return {
                ...normalizeVoyageNode(node, status),
                status,
            };
        });

        const currentNode = normalizedVoyage.route_nodes[currentIndex];
        const nextNode = normalizedVoyage.route_nodes[resolvedNextIndex] || null;
        normalizedVoyage.current_island = {
            ...normalizedVoyage.current_island,
            ...currentNode,
            stage: currentStage.voyage_arc || currentNode.stage || normalizedVoyage.current_island.stage,
        };
        normalizedVoyage.ship_position = {
            ...normalizedVoyage.ship_position,
            x: Number(currentNode.map_x ?? normalizedVoyage.ship_position.x ?? 50),
            y: Number(currentNode.map_y ?? normalizedVoyage.ship_position.y ?? 50),
        };
        if (nextNode) {
            normalizedVoyage.next_island = {
                ...(normalizedVoyage.next_island || {}),
                ...nextNode,
                status: "next",
            };
        }
        return { voyage: normalizedVoyage, currentStage, nextStage };
    }

    function getVoyageDisplayFrame(voyage, intelligence) {
        const fallback = getConservativeVoyageFrame(voyage);
        const currentStage = intelligence && typeof intelligence.current_stage === "object" ? intelligence.current_stage : null;
        const nextStage = intelligence && typeof intelligence.next_stage === "object" ? intelligence.next_stage : null;
        const currentIsland = voyage && voyage.current_island ? voyage.current_island : {};
        const nextIsland = voyage && voyage.next_island ? voyage.next_island : null;
        return {
            currentPhase: currentStage && currentStage.voyage_arc
                ? currentStage.voyage_arc
                : (currentIsland.name || fallback.currentPhase),
            currentFooting: currentStage && currentStage.voyage_arc
                ? currentStage.voyage_arc
                : fallback.currentFooting,
            routeSummary: currentStage && currentStage.summary
                ? currentStage.summary
                : fallback.routeSummary,
            stageTitle: currentStage && currentStage.label
                ? currentStage.label
                : fallback.stageTitle,
            stageDetail: currentStage && currentStage.summary
                ? currentStage.summary
                : fallback.stageDetail,
            canDoNow: summarizeStageHeadline(currentStage && currentStage.can_do_now, fallback.canDoNow),
            canDoNowDetail: summarizeStageDetail(currentStage && currentStage.can_do_now, fallback.canDoNowDetail),
            stillLearning: summarizeStageHeadline(currentStage && currentStage.still_learning, fallback.stillLearning),
            stillLearningDetail: summarizeStageDetail(currentStage && currentStage.still_learning, fallback.stillLearningDetail),
            nextTitle: nextStage && nextStage.label
                ? nextStage.label
                : ((nextIsland && nextIsland.name) || fallback.nextFocus),
            nextDetail: currentStage && currentStage.next_stage_detail
                ? currentStage.next_stage_detail
                : ((nextStage && nextStage.summary) || fallback.nextDetail),
            stageMeaning: voyage && voyage.stage_meaning
                ? voyage.stage_meaning
                : (currentStage && currentStage.what_this_means
                ? currentStage.what_this_means
                : describeVoyageLearningStage(voyage && voyage.learning_stage, currentStage && currentStage.label ? currentStage.label : fallback.stageTitle)),
            currentRoadmapImage: resolveVoyageArt(currentIsland.sprite_url, deriveVoyageRouteKey(currentStage && currentStage.voyage_arc, currentIsland && currentIsland.key)),
            currentRoadmapLabel: currentStage && currentStage.voyage_arc
                ? currentStage.voyage_arc
                : (currentIsland.name || fallback.currentPhase),
            currentRoadmapDetail: currentStage && currentStage.label
                ? currentStage.label
                : fallback.stageTitle,
            nextRoadmapImage: resolveVoyageArt(nextIsland && nextIsland.sprite_url, deriveVoyageRouteKey(nextStage && nextStage.voyage_arc, nextIsland && nextIsland.key)),
            nextRoadmapLabel: nextStage && nextStage.voyage_arc
                ? nextStage.voyage_arc
                : ((nextIsland && nextIsland.name) || fallback.nextFocus),
            nextRoadmapDetail: nextStage && nextStage.label
                ? nextStage.label
                : fallback.nextDetail,
        };
    }

    function buildRecentLearnedMarkup(validationAudit, voyage) {
        const recentValidated = validationAudit && Array.isArray(validationAudit.recently_validated)
            ? validationAudit.recently_validated
            : [];
        if (recentValidated.length) {
            return {
                summary: "Latest verified cards are the clearest sign of new learning right now.",
                html: recentValidated.slice(0, 4).map((item) => `
                    <button class="devLearningEvent" type="button" data-card-code="${escapeHtml(item.card_code || "")}">
                        <strong>${escapeHtml(item.card_code || "")} Â· ${escapeHtml(item.card_name || "Unknown card")}</strong>
                        <span>${escapeHtml(item.verified_at || "Recently verified")}</span>
                    </button>
                `).join(""),
            };
        }
        return {
            summary: "No recent verified learning is available yet.",
            html: '<p class="devValidationEmpty">No recent verified learning is available yet.</p>',
        };
    }

    function buildNeedsAttentionItems(payload) {
        const items = [];
        const issues = payload && payload.issues ? payload.issues : {};
        ["miru_ai", "project_miru"].forEach((key) => {
            const issue = issues[key];
            if (issue && issue.tone === "warn" && issue.detail) {
                items.push(String(issue.detail));
            }
        });
        const pushover = payload && payload.pushover;
        if (pushover && pushover.enabled && !pushover.configured) {
            items.push("Pushover is enabled but still missing required credentials.");
        }
        const limitsByProvider = payload && payload.limits_by_provider ? payload.limits_by_provider : {};
        ["cursor", "codex"].forEach((provider) => {
            const entry = limitsByProvider[provider];
            if (entry && Number(entry.remaining_percent ?? 100) < 20) {
                items.push(`${provider.charAt(0).toUpperCase()}${provider.slice(1)} usage is below 20% remaining.`);
            }
        });
        if (!items.length) {
            items.push("No urgent issues. Miru looks ready from the Dev Console.");
        }
        return items;
    }

    function renderOverviewSummary(payload) {
        const activity = payload && payload.activity ? payload.activity : {};
        const trainingSource = payload.training_progress || payload.training || {};
        const voyage = payload.voyage || (payload.training && payload.training.voyage) || {};
        const learningStageLabel = String(voyage.sea_label || "Early structured learning");
        const coverageClarification = "This measures verified card knowledge coverage, not Miru's full strategic intelligence.";
        const validationAudit = payload.validation_audit || {};
        const recentLearned = buildRecentLearnedMarkup(validationAudit, voyage);
        const learnedSummary = document.getElementById("devRecentLearnedSummary");
        const learnedList = document.getElementById("devRecentLearnedList");
        const stateTitle = document.getElementById("devOverviewStateTitle");
        const stateDescription = document.getElementById("devOverviewStateDescription");
        const stateDetail = document.getElementById("devOverviewStateDetail");
        const statePill = document.getElementById("devOverviewStatePill");
        const progressPercentValue = trainingSource.training_progress_percent ?? trainingSource.progress_percent ?? 0;
        const progressPercent = document.getElementById("devOverviewProgressPercent");
        const progressSummary = document.getElementById("devOverviewProgressSummary");
        const progressDetail = document.getElementById("devOverviewProgressDetail");
        const progressStageLabel = document.getElementById("devOverviewStageLabel");
        const progressFill = document.getElementById("devOverviewProgressFill");
        const healthCard = document.getElementById("devHealthCard");
        const healthHeadline = document.getElementById("devHealthHeadline");
        const healthSignal = document.getElementById("devHealthSignal");
        const healthSummary = document.getElementById("devHealthSummary");
        const needsAttentionList = document.getElementById("devNeedsAttentionList");
        const attentionItems = buildNeedsAttentionItems(payload);
        const attentionTone = attentionItems.length === 1 && attentionItems[0].startsWith("No urgent")
            ? "good"
            : "warn";
        if (learnedSummary) {
            learnedSummary.textContent = recentLearned.summary;
        }
        if (learnedList) {
            learnedList.innerHTML = recentLearned.html;
        }
        if (stateTitle) {
            stateTitle.textContent = activity.title || "Sleeping";
        }
        if (stateDescription) {
            stateDescription.textContent = activity.description || "Miru is idle and ready.";
        }
        if (stateDetail) {
            stateDetail.textContent = activity.detail || "Miru is waiting for the next question.";
        }
        if (statePill) {
            statePill.textContent = activity.title || "Sleeping";
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
        if (healthCard) {
            healthCard.dataset.tone = attentionTone;
        }
        if (healthHeadline) {
            healthHeadline.textContent = attentionTone === "warn" ? "Needs attention" : "Healthy";
        }
        if (healthSignal) {
            healthSignal.textContent = attentionTone === "warn" ? "Check now" : "Healthy";
            healthSignal.className = `statusPill statusPill--${attentionTone}`;
        }
        if (healthSummary) {
            healthSummary.textContent = attentionTone === "warn"
                ? "One or more services or limits need a closer look. Open System or Validation for detail."
                : "Miru AI, Project Miru, and notifications all look ready from this console.";
        }
        if (needsAttentionList) {
            needsAttentionList.innerHTML = attentionItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
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

    function buildMonitorGainMarkup(item) {
        return `
            <article class="devMonitorListItem">
                <span class="devMetricLabel">${escapeHtml(item.label || "Metric")}</span>
                <strong>${escapeHtml(item.value || "0")}</strong>
                <p>${escapeHtml(item.detail || "")}</p>
            </article>
        `;
    }

    function buildMonitorWarningMarkup(item) {
        const tone = item && item.tone ? String(item.tone) : "neutral";
        return `
            <article class="devMonitorListItem devMonitorListItem--${escapeHtml(tone)}">
                <span class="devMetricLabel">${escapeHtml(item.title || "Monitor note")}</span>
                <strong>${escapeHtml(item.detail || "")}</strong>
            </article>
        `;
    }

    function buildMonitorActivityMarkup(item) {
        const title = escapeHtml(item && item.title ? item.title : "Monitor event");
        const detail = escapeHtml(item && item.detail ? item.detail : "");
        const timestamp = escapeHtml(item && item.timestamp ? item.timestamp : "");
        const cardCode = item && item.card_code ? String(item.card_code) : "";
        const tone = escapeHtml(item && item.tone ? item.tone : "neutral");
        if (cardCode) {
            return `
                <button class="devLearningEvent devMonitorEvent" type="button" data-card-code="${escapeHtml(cardCode)}">
                    <strong>${title}</strong>
                    <span>${detail}</span>
                    <small>${timestamp}</small>
                </button>
            `;
        }
        return `
            <article class="devMonitorEvent devMonitorEvent--static" data-tone="${tone}">
                <strong>${title}</strong>
                <span>${detail}</span>
                <small>${timestamp}</small>
            </article>
        `;
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

    function buildControlIssueMarkup(item) {
        const tone = escapeHtml(item && item.tone ? item.tone : "neutral");
        return `
            <article class="devMonitorListItem devMonitorListItem--${tone}">
                <span class="devMetricLabel">${escapeHtml(item && item.title ? item.title : "Issue")}</span>
                <strong>${escapeHtml(item && item.detail ? item.detail : "")}</strong>
                <p>${escapeHtml(item && item.source ? item.source : "")}</p>
            </article>
        `;
    }

    function buildControlActivityMarkup(item) {
        const tone = escapeHtml(item && item.tone ? item.tone : "neutral");
        const ts = item && item.timestamp ? formatTimeLA(item.timestamp) : "";
        const footer = ts || (item && item.source ? item.source : "");
        return `
            <div class="devActivityFeedItem devActivityFeedItem--${tone}">
                <span class="devActivityFeedTitleText">${escapeHtml(item && item.title ? item.title : "Event")}</span>
                <span class="devActivityFeedDetail">${escapeHtml(item && item.detail ? item.detail : "")}</span>
                <span class="devActivityFeedTime">${escapeHtml(footer)}</span>
            </div>
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

    function buildJudgmentSignalMarkup(item) {
        const recommendation = item && item.recommendation ? item.recommendation : {};
        const tone = item && item.tone ? item.tone : "neutral";
        return `
            <article class="devJudgmentSignal devJudgmentSignal--${escapeHtml(tone)}">
                <div class="devJudgmentSignalHead">
                    <span class="devMetricLabel">${escapeHtml(item && item.title ? item.title : "Signal")}</span>
                    <span class="statusPill statusPill--${escapeHtml(tone)}">${escapeHtml(item && item.category_label ? item.category_label : "Info")}</span>
                </div>
                <div class="devJudgmentChipRow">
                    <span class="devJudgmentChip">${escapeHtml(item && item.issue_type_label ? item.issue_type_label : "Environment")}</span>
                    <span class="devJudgmentChip">${escapeHtml(recommendation.label || "Observe only")}</span>
                    <span class="devJudgmentChip">${escapeHtml(recommendation.guardrail_label || "Read-only")}</span>
                </div>
                <p>${escapeHtml(item && item.summary ? item.summary : "")}</p>
            </article>
        `;
    }

    function renderJudgmentLayer(payload) {
        const control = payload && payload.control_layer ? payload.control_layer : {};
        const judgment = control.judgment || {};
        const primary = judgment.primary || {};
        const recommendation = primary.recommendation || {};
        const items = Array.isArray(judgment.items) ? judgment.items : [];
        const copyBtn = document.getElementById("devJudgmentCopyPromptBtn");
        const actionClass = String(recommendation.action_class || "").trim().toLowerCase();
        const routeTask = String(recommendation.route_task || "").trim();
        const route = routeTask && actionClass !== "user_review_required"
            ? inferWorkerRoute(routeTask, payload)
            : null;

        const statusPill = document.getElementById("devJudgmentStatusPill");
        if (statusPill) {
            statusPill.textContent = judgment.status_label || "Info";
            statusPill.className = `statusPill statusPill--${judgment.tone || "neutral"}`;
        }
        setText("devJudgmentWhat", judgment.what_i_think || "Miru will summarize the current state after the first live refresh.");
        setText("devJudgmentWhy", judgment.what_matters_most || "No dominant concern surfaced yet.");
        setText("devJudgmentNext", judgment.what_i_recommend_next || "Observe the current state.");
        setText("devJudgmentGuardrail", recommendation.guardrail_label || judgment.guardrail_label || "Read-only");
        setText("devJudgmentAction", recommendation.label || "Observe only");
        setText("devJudgmentConfidence", `Confidence ${recommendation.confidence || "medium"}`);
        setText("devJudgmentRisk", `Risk ${recommendation.risk || "low"}`);
        setText("devJudgmentRetry", judgment.retry_guidance || recommendation.retry_guidance || "Observe the current state before retrying.");
        setText("devJudgmentSource", (control.status_sources && control.status_sources.judgment) || judgment.source || "Judgment source detail unavailable.");

        const guardrailNode = document.getElementById("devJudgmentGuardrail");
        if (guardrailNode) {
            const guardrailTone = recommendation.guardrail_label === "Review required"
                ? "warn"
                : (recommendation.guardrail_label === "Safe action" ? "neutral" : "good");
            guardrailNode.className = `devJudgmentChip devJudgmentChip--${guardrailTone}`;
        }

        const signalsWrap = document.getElementById("devJudgmentSignals");
        if (signalsWrap) {
            signalsWrap.innerHTML = items.length
                ? items.map((item) => buildJudgmentSignalMarkup(item)).join("")
                : '<p class="devValidationEmpty">Judgment signals will appear after the first live refresh.</p>';
        }

        if (recommendation.guardrail_label === "Review required") {
            setText("devJudgmentWorker", "User review only");
            setText("devJudgmentWorkerReason", "Human review comes before any worker handoff for this item.");
            setText("devJudgmentTarget", "User review");
            setText("devJudgmentVerify", recommendation.retry_guidance || "Do not retry until the review decision is made.");
            if (copyBtn) {
                copyBtn.disabled = true;
                copyBtn.dataset.prompt = "";
            }
            return;
        }

        if (route) {
            setText("devJudgmentWorker", route.worker || "Codex");
            setText("devJudgmentWorkerReason", route.why || "The worker router will explain why this recommendation fits.");
            setText("devJudgmentTarget", route.environment || "Worktree repo on 18765");
            setText("devJudgmentVerify", route.verification || "Verify on the live Dev page and status endpoints.");
            if (copyBtn) {
                copyBtn.disabled = false;
                copyBtn.dataset.prompt = route.prompt || "";
            }
            return;
        }

        setText("devJudgmentWorker", "Observe locally");
        setText("devJudgmentWorkerReason", recommendation.rationale || "This recommendation does not need a worker handoff.");
        setText("devJudgmentTarget", "Worktree repo on 18765");
        setText("devJudgmentVerify", recommendation.retry_guidance || "Observe the current state before retrying.");
        if (copyBtn) {
            copyBtn.disabled = true;
            copyBtn.dataset.prompt = "";
        }
    }

    function renderControlLayer(payload) {
        const control = payload && payload.control_layer ? payload.control_layer : {};
        const items = resolveControlHealthItems(payload);
        const issues = control.recent_issues || {};
        const activityItems = Array.isArray(control.recent_activity) ? control.recent_activity : [];
        const sources = control.status_sources || {};
        const headline = summarizeControlHeadline(items);

        renderJudgmentLayer(payload);

        const summaryNode = document.getElementById("devControlLayerSummary");
        const healthHeadline = document.getElementById("devControlHealthHeadline");
        const healthList = document.getElementById("devControlHealthList");
        const issuesHeadline = document.getElementById("devControlIssuesHeadline");
        const issuesSummary = document.getElementById("devControlIssuesSummary");
        const issuesList = document.getElementById("devControlIssuesList");
        const activityHeadline = document.getElementById("devControlActivityHeadline");
        const activityList = document.getElementById("devControlActivityList");

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
        if (issuesHeadline) {
            issuesHeadline.textContent = issues.status || "INCONCLUSIVE";
            issuesHeadline.className = `statusPill statusPill--${issues.tone || "neutral"}`;
        }
        if (issuesSummary) {
            issuesSummary.textContent = issues.summary || "No recent issue summary yet.";
        }
        if (issuesList) {
            const rows = Array.isArray(issues.items) && issues.items.length
                ? issues.items
                : [{
                    title: issues.headline || "All clear",
                    detail: issues.summary || "No active issue surfaced.",
                    source: issues.source || "",
                    tone: issues.tone || "neutral",
                }];
            issuesList.innerHTML = rows.map((item) => buildControlIssueMarkup(item)).join("");
        }
        if (activityHeadline) {
            activityHeadline.textContent = activityItems.length ? "Live" : "INCONCLUSIVE";
            activityHeadline.className = `statusPill statusPill--${activityItems.length ? "good" : "neutral"}`;
        }
        if (activityList) {
            activityList.innerHTML = activityItems.length
                ? activityItems.map((item) => buildControlActivityMarkup(item)).join("")
                : '<p class="devActivityFeedEmpty">No recent control-layer activity yet.</p>';
        }
        setText("devControlHealthSource", sources.health || "Health source detail unavailable.");
        setText("devControlIssuesSource", sources.recent_issues || "Issue source detail unavailable.");
        setText("devControlActivitySource", sources.recent_activity || "Activity source detail unavailable.");
        setText("devWorkerRouterSource", `Worker routing uses Miru-specific rules plus the current system summary. ${sources.truth || ""}`.trim());
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

    function buildLiveControlSummary(payload) {
        const control = payload && payload.control_layer ? payload.control_layer : {};
        return String(control.copy_summary || control.summary || "").trim();
    }

    function inferWorkerRoute(taskText, payload) {
        const text = String(taskText || "").trim();
        const normalized = text.toLowerCase();
        const mentionsMain = /(main repo|main runtime|\b8765\b|\b8080\b|intelligence pipeline|visual intelligence|image pipeline|dossier|source adapters|main site)/.test(normalized);
        const mentionsProjectMiru = /(project miru|\b18080\b|dashboard|leader page|leaders|catalog|set page|user-facing)/.test(normalized);
        const mentionsDev = /(\b18765\b|dev page|dev surface|control layer|runtime status|operator|govern|autopilot|approval|heartbeat|learner|worker|api|endpoint|restart|script|powershell|windows launch|process|socket|health|status)/.test(normalized);
        const mentionsUi = /(ui|ux|layout|css|html|template|render|mobile|responsive|copy button|button|panel|visual|styling|spacing)/.test(normalized);
        const mentionsAudit = /(audit|review|reconcile|compare|drift|authority|docs|doc|plan|risk|map|report|summary)/.test(normalized);

        let worker = "Codex";
        let why = "Best fit for runtime debugging, repo-aware edits, and verification-heavy Miru work.";
        let environment = "Worktree repo on 18765";
        let verification = "Verify on http://127.0.0.1:18765/dev plus /api/dev-status and any touched runtime endpoint.";

        if (mentionsUi && !mentionsDev && !mentionsMain) {
            worker = "Cursor";
            why = "Best fit for tight front-end iteration and targeted UI polish when the task is mostly page-level behavior or styling.";
            environment = mentionsProjectMiru ? "Worktree repo on 18080" : "Worktree repo on 18765";
            verification = mentionsProjectMiru
                ? "Verify the visual change directly on http://127.0.0.1:18080/ and confirm no behavior regressions."
                : "Verify the visual change directly on http://127.0.0.1:18765/dev and confirm the rest of the Dev page still behaves normally.";
        } else if (mentionsAudit && !mentionsDev && !mentionsUi) {
            worker = "Claude Code";
            why = "Best fit for careful synthesis, audit writing, and high-context reconciliation where preserving nuance matters more than fast patching.";
            environment = mentionsMain ? "Main repo on 8080/8765, with worktree cross-checks if needed" : "Worktree repo documentation and authority surfaces";
            verification = "Verify the report against the cited files, routes, startup paths, and live endpoints named in the task.";
        }

        if (mentionsMain) {
            environment = "Main repo on 8080/8765";
            verification = "Verify against the main runtime path or its cited entry points, and avoid assuming worktree authority.";
            if (!mentionsAudit && !mentionsUi) {
                worker = "Codex";
                why = "Best fit for deeper intelligence-pipeline changes and exact runtime verification in the main repo.";
            }
        } else if (mentionsDev) {
            environment = "Worktree repo on 18765";
            verification = "Verify on http://127.0.0.1:18765/dev and /api/dev-status, and confirm 18080 is unaffected.";
        } else if (mentionsProjectMiru) {
            environment = "Worktree repo on 18080";
            if (!mentionsUi) {
                verification = "Verify on http://127.0.0.1:18080/ and confirm Project Miru behavior stays intact.";
            }
        } else if (!text) {
            environment = "Worktree repo on 18765";
            verification = "Verify on http://127.0.0.1:18765/dev and /api/dev-status, and confirm 18080 is unaffected.";
        }

        const reportFormat = [
            "CONFIRMED WORKING",
            "INCONCLUSIVE",
            "FAILED",
        ].join(" / ");
        const liveSummary = buildLiveControlSummary(payload);
        const prompt = [
            `Recommended worker: ${worker}`,
            "Recommended model: strongest reliable coding model available",
            "Reasoning level: High",
            "",
            `Environment target: ${environment}`,
            "Assume the repo state has already been inspected. Do not rescan the entire project unless necessary.",
            "",
            "Task:",
            text || "Investigate the current Miru issue carefully and finish the bug completely.",
            "",
            "Current observed state:",
            liveSummary || "Use the live Miru Dev status as the source of truth.",
            "",
            "Requirements:",
            "- Reuse existing mechanisms before creating anything new.",
            "- Keep changes scoped and surgical.",
            "- Validate against the live target before claiming success.",
            "",
            `Required verification target: ${verification}`,
            `Required report format: ${reportFormat}`,
        ].join("\n");

        return {
            worker,
            why,
            environment,
            verification,
            reportFormat,
            prompt,
        };
    }

    function renderWorkerRouteRecommendation(result) {
        if (!result) {
            return;
        }
        setText("devWorkerRouterWorker", result.worker || "Enter a task first");
        setText("devWorkerRouterWhy", result.why || "The router will explain why the recommendation fits.");
        setText("devWorkerRouterTarget", result.environment || "Worktree / 18765 by default");
        setText("devWorkerRouterVerify", result.verification || "Verification target will appear with the recommendation.");
        setText("devWorkerRouterMeta", `Required report format: ${result.reportFormat || "CONFIRMED WORKING / INCONCLUSIVE / FAILED"}`);
        const promptNode = document.getElementById("devWorkerRouterPrompt");
        if (promptNode) {
            promptNode.value = result.prompt || "";
        }
        const pill = document.getElementById("devWorkerRouterWorkerPill");
        if (pill) {
            const tone = result.worker === "Codex" ? "good" : "neutral";
            pill.textContent = result.worker || "Waiting";
            pill.className = `statusPill statusPill--${tone}`;
        }
    }

    function renderMonitorDeck(payload) {
        const monitor = payload && payload.monitor ? payload.monitor : {};
        const state = monitor.state || {};
        const progress = monitor.progress || {};
        const source = monitor.source || {};
        const gains = Array.isArray(monitor.gains) ? monitor.gains : [];
        const recentActivity = Array.isArray(monitor.recent_activity) ? monitor.recent_activity : [];
        const warnings = Array.isArray(monitor.warnings) ? monitor.warnings : [];

        const progressPill = document.getElementById("devMonitorProgressPill");
        const progressSummary = document.getElementById("devMonitorProgressSummary");
        const progressDetail = document.getElementById("devMonitorProgressDetail");
        const sourceLabel = document.getElementById("devMonitorSourceLabel");
        const sourceDetail = document.getElementById("devMonitorSourceDetail");
        const stateTitle = document.getElementById("devMonitorStateTitle");
        const statePill = document.getElementById("devMonitorStatePill");
        const stateDescription = document.getElementById("devMonitorStateDescription");
        const taskLabel = document.getElementById("devMonitorTaskLabel");
        const taskType = document.getElementById("devMonitorTaskType");
        const queueStatus = document.getElementById("devMonitorQueueStatus");
        const heartbeat = document.getElementById("devMonitorHeartbeat");
        const gainsList = document.getElementById("devMonitorGainsList");
        const activityList = document.getElementById("devMonitorActivityList");
        const warningsList = document.getElementById("devMonitorWarningsList");

        if (progressPill) {
            progressPill.textContent = progress.label || "Idle";
            progressPill.className = `statusPill statusPill--${progress.tone || "neutral"}`;
        }
        if (progressSummary) {
            progressSummary.textContent = progress.summary || "Miru progress summary is unavailable.";
        }
        if (progressDetail) {
            progressDetail.textContent = progress.detail || "No recent operator summary is available yet.";
        }
        if (sourceLabel) {
            sourceLabel.textContent = source.label || "Monitoring worktree runtime";
        }
        if (sourceDetail) {
            sourceDetail.textContent = source.detail || "";
        }
        if (stateTitle) {
            stateTitle.textContent = state.label || "Idle";
        }
        if (statePill) {
            statePill.textContent = state.label || "Idle";
            statePill.className = `statusPill statusPill--${state.tone || "neutral"}`;
        }
        if (stateDescription) {
            stateDescription.textContent = state.description || "Miru is online and waiting for work.";
        }
        if (taskLabel) {
            taskLabel.textContent = state.task_label || "No active task";
        }
        if (taskType) {
            taskType.textContent = state.task_type_label || "Waiting";
        }
        if (queueStatus) {
            queueStatus.textContent = state.queue_status || "0 waiting, 0 running, 0 failed";
        }
        if (heartbeat) {
            heartbeat.textContent = state.heartbeat || "No heartbeat reported yet.";
        }
        if (gainsList) {
            gainsList.innerHTML = gains.length
                ? gains.map((item) => buildMonitorGainMarkup(item)).join("")
                : '<p class="devValidationEmpty">No learning gain data is available yet.</p>';
        }
        if (activityList) {
            activityList.innerHTML = recentActivity.length
                ? recentActivity.map((item) => buildMonitorActivityMarkup(item)).join("")
                : '<p class="devValidationEmpty">No recent monitor events are available yet.</p>';
        }
        if (warningsList) {
            warningsList.innerHTML = warnings.length
                ? warnings.map((item) => buildMonitorWarningMarkup(item)).join("")
                : '<p class="devValidationEmpty">No warnings are available.</p>';
        }
    }

    function renderDevMonitorPayload(payload) {
        renderMonitorDeck({ monitor: payload && payload.monitor ? payload.monitor : {} });
    }

    function renderDevImageCoveragePayload(payload) {
        renderImageCoverageList(payload && payload.image_coverage_by_set ? payload.image_coverage_by_set : []);
    }

    function renderDevValidationAuditPayload(payload) {
        renderValidationAuditInsights(
            payload && payload.validation_audit ? payload.validation_audit : {},
            payload && payload.validation_audit_url_base ? payload.validation_audit_url_base : ""
        );
    }

    function renderDevResourceMetricsPayload(payload) {
        updateDevMetricCards(".devResourceCard", payload && payload.resource_metrics ? payload.resource_metrics : [], "resourceKey");
    }

    async function fetchDeferredDevSection(sectionKey, { force = false } = {}) {
        const entry = devDeferredSections[sectionKey];
        if (!entry || !entry.url) {
            return null;
        }
        if (!shouldRefreshDevSection(sectionKey, { force })) {
            return null;
        }
        if (entry.inFlight) {
            return entry.inFlight;
        }
        entry.inFlight = fetch(entry.url, {
            headers: {
                Accept: "application/json",
                "X-Requested-With": "miru-client-nav",
            },
            credentials: "same-origin",
        })
            .then((response) => {
                if (!response.ok) {
                    throw new Error(`${sectionKey} failed with ${response.status}`);
                }
                return response.json();
            })
            .then((payload) => {
                entry.loaded = true;
                entry.lastLoadedAt = Date.now();
                if (sectionKey === "monitor") {
                    renderDevMonitorPayload(payload);
                } else if (sectionKey === "imageCoverage") {
                    renderDevImageCoveragePayload(payload);
                } else if (sectionKey === "validationAudit") {
                    renderDevValidationAuditPayload(payload);
                } else if (sectionKey === "resourceMetrics") {
                    renderDevResourceMetricsPayload(payload);
                }
                return payload;
            })
            .catch(() => null)
            .finally(() => {
                entry.inFlight = null;
            });
        return entry.inFlight;
    }

    async function loadDeferredPanelsForTab(tabName, { force = false } = {}) {
        const tab = String(tabName || getCurrentDevTab() || "");
        const tasks = [];
        if (tab === "queue") {
            tasks.push(fetchDeferredDevSection("monitor", { force }));
            tasks.push(fetchDeferredDevSection("imageCoverage", { force }));
        } else if (tab === "validation") {
            tasks.push(fetchDeferredDevSection("validationAudit", { force }));
        } else if (tab === "system") {
            tasks.push(fetchDeferredDevSection("resourceMetrics", { force }));
        } else if (tab === "overview") {
            const r = document.getElementById("devMonitor");
            if (r && typeof r.loadPendingApprovals === "function") {
                tasks.push(Promise.resolve(r.loadPendingApprovals()));
            }
        }
        if (!tasks.length) {
            return [];
        }
        return Promise.allSettled(tasks);
    }

    function buildVoyageMapMarkup(voyage) {
        const shipPosition = voyage.ship_position || {};
        const routeNodes = Array.isArray(voyage.route_nodes) ? voyage.route_nodes : [];
        const nodesMarkup = routeNodes.map((node) => {
            const stateLabel = String(node.short_name || node.name || node.status || "planned");
            return `
                <article class="devVoyageMapNode devVoyageMapNode--${escapeHtml(node.status || "planned")}" style="left: ${Number(node.map_x || 0)}%; top: ${Number(node.map_y || 0)}%;">
                    <span class="devVoyageMapNodeDot" aria-hidden="true"></span>
                    <span class="devVoyageMapNodeLabel">${escapeHtml(stateLabel)}</span>
                </article>
            `;
        }).join("");

        return `
            <svg class="voyageMapRouteSvg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                <polyline class="voyageMapRouteGlow" points="${escapeHtml(voyage.route_polyline || "")}"></polyline>
                <polyline class="voyageMapRouteLine" points="${escapeHtml(voyage.route_polyline || "")}"></polyline>
            </svg>
            ${nodesMarkup}
            <div class="devVoyagePolarTang" style="left: ${Number(shipPosition.x || 0)}%; top: ${Number(shipPosition.y || 0)}%;">
                <span class="devVoyagePolarTangPulse" aria-hidden="true"></span>
                <span class="devVoyagePolarTangDot" aria-hidden="true"></span>
                <span class="devVoyagePolarTangLabel">Current route</span>
            </div>
        `;
    }

    function applyVoyageMapStage(stage, voyage, mode) {
        if (!stage || !voyage) {
            return;
        }
        const assets = voyage.assets || {};
        const shipPosition = voyage.ship_position || {};
        const x = Math.max(0, Math.min(Number(shipPosition.x || 50), 100));
        const y = Math.max(0, Math.min(Number(shipPosition.y || 50), 100));
        const mapAsset = assets.map || assets.world_map || assets.chart || "";
        if (mapAsset) {
            stage.style.setProperty("--voyage-map-image", `url("${String(mapAsset).replace(/"/g, '\\"')}")`);
        } else {
            stage.style.removeProperty("--voyage-map-image");
        }
        stage.dataset.hasMap = mapAsset ? "true" : "false";
        stage.style.setProperty("--voyage-map-scale", mode === "focused" ? "1.42" : "1");
        stage.style.setProperty("--voyage-pan-x", `${Math.max(-18, Math.min(18, (50 - x) * 0.48))}%`);
        stage.style.setProperty("--voyage-pan-y", `${Math.max(-16, Math.min(16, (50 - y) * 0.42))}%`);
    }

    function syncVoyageMapViewportMode(voyage) {
        const viewport = document.getElementById("devVoyageMapViewport");
        const expandedViewport = document.getElementById("devVoyageMapExpandedViewport");
        const hint = document.getElementById("devVoyageMapHint");
        const stage = document.getElementById("devVoyageMapStage");
        const expandedStage = document.getElementById("devVoyageMapStageExpanded");
        const isCompact = window.matchMedia && window.matchMedia("(max-width: 720px)").matches;
        if (viewport) {
            viewport.dataset.mode = isCompact ? "focused" : "full";
        }
        if (expandedViewport) {
            expandedViewport.dataset.mode = "full";
        }
        if (hint) {
            hint.textContent = isCompact
                ? "Mobile starts focused on the current route. Use Open full map for the wider shell."
                : "Desktop shows the broader route shell by default. Open full map for the widest view.";
        }
        applyVoyageMapStage(stage, voyage, isCompact ? "focused" : "full");
        applyVoyageMapStage(expandedStage, voyage, "full");
    }

    function openVoyageMapDialog() {
        const dialog = document.getElementById("devVoyageMapDialog");
        if (!dialog) {
            return;
        }
        dialog.hidden = false;
        dialog.classList.remove("isHidden");
        dialog.setAttribute("aria-hidden", "false");
        document.body.classList.add("hasVoyageMapDialog");
    }

    function closeVoyageMapDialog() {
        const dialog = document.getElementById("devVoyageMapDialog");
        if (!dialog) {
            return;
        }
        dialog.hidden = true;
        dialog.classList.add("isHidden");
        dialog.setAttribute("aria-hidden", "true");
        document.body.classList.remove("hasVoyageMapDialog");
    }

    function initializeVoyageMapShell() {
        const panel = document.getElementById("devVoyagePanel");
        if (!panel) {
            return;
        }
        if (panel.dataset.voyageMapBound !== "true") {
            panel.dataset.voyageMapBound = "true";
            panel.addEventListener("click", (event) => {
                const target = event.target instanceof HTMLElement ? event.target.closest("#devVoyageExpandButton, #devVoyageCloseButton, [data-voyage-map-close]") : null;
                if (!target) {
                    return;
                }
                if (target.id === "devVoyageExpandButton") {
                    openVoyageMapDialog();
                    return;
                }
                closeVoyageMapDialog();
            });
            if (!voyageMapResizeBound) {
                voyageMapResizeBound = true;
                window.addEventListener("resize", () => {
                    syncVoyageMapViewportMode(latestDevVoyage);
                });
                document.addEventListener("keydown", (event) => {
                    if (event.key === "Escape") {
                        closeVoyageMapDialog();
                    }
                });
            }
        }
        syncVoyageMapViewportMode(latestDevVoyage);
    }

    function setImageSource(id, src, alt) {
        const image = document.getElementById(id);
        if (!image) {
            return;
        }
        const resolved = String(src || "").trim();
        if (resolved) {
            image.src = resolved;
        } else {
            image.removeAttribute("src");
        }
        image.alt = alt || "";
    }

    function renderDevVoyage(voyage, intelligence) {
        if (!voyage) {
            return;
        }
        const normalized = normalizeVoyageWithRoadmap(voyage, intelligence);
        const roadmapVoyage = normalized.voyage;
        latestDevVoyage = roadmapVoyage;
        const frame = getVoyageDisplayFrame(roadmapVoyage, {
            current_stage: normalized.currentStage,
            next_stage: normalized.nextStage,
        });
        const map = document.getElementById("devVoyageMap");
        const expandedMap = document.getElementById("devVoyageMapExpanded");
        const markup = buildVoyageMapMarkup(roadmapVoyage);
        if (map) {
            map.innerHTML = markup;
        }
        if (expandedMap) {
            expandedMap.innerHTML = markup;
        }
        syncVoyageMapViewportMode(roadmapVoyage);
        setText("devVoyageArc", frame.currentPhase);
        setText("devVoyageCurrentIslandSummary", frame.currentFooting);
        setText("devVoyageRouteProgressSummary", frame.routeSummary);
        setText("devVoyageStageTitle", frame.stageTitle);
        setText("devVoyageLearningStage", frame.stageDetail);
        setText("devVoyageCanDoNow", frame.canDoNow);
        setText("devVoyageCanDoNowDetail", frame.canDoNowDetail);
        setText("devVoyageStillLearning", frame.stillLearning);
        setText("devVoyageStillLearningDetail", frame.stillLearningDetail);
        setText("devVoyageNextStageTitle", frame.nextTitle);
        setText("devVoyageNextStageDetail", frame.nextDetail);
        setText("devVoyageStageMeaning", frame.stageMeaning);
        setText("devVoyageProgressPercentSummary", `${Number(roadmapVoyage.progress_percent || 0).toFixed(1)}%`);
        setText("devVoyageMapBadgeTitle", frame.currentRoadmapLabel);
        setText("devVoyageMapBadgeCopy", frame.currentRoadmapDetail);
        setText("devVoyageExpandedMapBadgeTitle", frame.currentRoadmapLabel);
        setText("devVoyageExpandedMapBadgeCopy", `${frame.currentRoadmapDetail}. ${frame.nextRoadmapLabel} is next.`);
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
        renderOverviewSummary(payload);
        const currentMode = resolveLearnerMode(payload) || "REVIEW_REQUIRED";
        const learningEngine = payload.learning_engine || {};
        const workerLastRun = payload.worker_last_run || {};
        const intelligenceWorker = ((payload.intelligence_status || {}).worker || {}).label;
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
        updateDevMetricCards(".devMetricCard", payload.learning_metrics || [], "metricKey");
        renderDevIssueCard("miru_ai", payload.issues && payload.issues.miru_ai);
        renderDevIssueCard("project_miru", payload.issues && payload.issues.project_miru);
        renderPushoverStatus(payload.pushover || {});
        renderIntelligenceStatus(payload.intelligence_status || {});
        renderOperatorHandoff(payload);
        renderControlLayer(payload);
        renderDevVoyage(
            payload.voyage || (payload.training && payload.training.voyage),
            payload.intelligence_progress || (payload.training && payload.training.intelligence_progress) || null,
        );
        renderMonitorDeck(payload);
        renderLimitsDock(payload.limits_by_provider || {});
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

    function buildOperatorActivityMarkup(item) {
        return `
            <article class="devMonitorEvent devMonitorEvent--static" data-tone="${escapeHtml(item.tone || "neutral")}">
                <strong>${escapeHtml(item.title || "Runtime event")}</strong>
                <span>${escapeHtml(item.detail || "")}</span>
                <small>${escapeHtml(item.timestamp || "")}</small>
            </article>
        `;
    }

    function renderOperatorConsole(consolePayload) {
        const payload = consolePayload || {};
        const guidance = payload.guidance || {};
        const snapshot = payload.snapshot || {};
        const needsNext = payload.needs_next || {};
        const workerState = snapshot.worker_state || {};
        const truthSource = snapshot.truth_source || {};
        const lastHeartbeat = snapshot.last_heartbeat || {};
        const lastTask = snapshot.last_completed_task || {};
        const recentActivity = Array.isArray(payload.recent_activity) ? payload.recent_activity : [];
        const progressCategories = Array.isArray(payload.progress_categories) ? payload.progress_categories : [];

        const workerPill = document.getElementById("devOperatorWorkerPill");
        const sentence = document.getElementById("devOperatorSentence");
        const guidanceHealth = document.getElementById("devOperatorGuidanceHealth");
        const guidanceFreshness = document.getElementById("devOperatorGuidanceFreshness");
        const guidanceMomentum = document.getElementById("devOperatorGuidanceMomentum");
        const guidanceStrongest = document.getElementById("devOperatorGuidanceStrongest");
        const needsTitle = document.getElementById("devOperatorNeedsTitle");
        const needsDetail = document.getElementById("devOperatorNeedsDetail");
        const workerStateNode = document.getElementById("devOperatorWorkerState");
        const runtimeState = document.getElementById("devOperatorRuntimeState");
        const queueSummary = document.getElementById("devOperatorQueueSummary");
        const queueDetail = document.getElementById("devOperatorQueueDetail");
        const truthSourceLabel = document.getElementById("devOperatorTruthSourceLabel");
        const truthSourceDetail = document.getElementById("devOperatorTruthSourceDetail");
        const heartbeatLabel = document.getElementById("devOperatorHeartbeatLabel");
        const heartbeatDetail = document.getElementById("devOperatorHeartbeatDetail");
        const lastTaskLabel = document.getElementById("devOperatorLastTaskLabel");
        const lastTaskDetail = document.getElementById("devOperatorLastTaskDetail");
        const activityList = document.getElementById("devOperatorActivityList");
        const progressList = document.getElementById("devOperatorProgressCategories");

        if (workerPill) {
            workerPill.textContent = workerState.label || "Loading";
            workerPill.className = `statusPill statusPill--${workerState.tone || "neutral"}`;
        }
        if (sentence) {
            sentence.textContent = snapshot.sentence || "Operator snapshot unavailable.";
        }
        if (guidanceHealth) {
            guidanceHealth.textContent = guidance.health || "Loading";
        }
        if (guidanceFreshness) {
            guidanceFreshness.textContent = guidance.freshness || "Waiting for freshness detail.";
        }
        if (guidanceMomentum) {
            guidanceMomentum.textContent = guidance.momentum || "Loading";
        }
        if (guidanceStrongest) {
            guidanceStrongest.textContent = guidance.strongest || "Waiting for intelligence summary.";
        }
        if (needsTitle) {
            needsTitle.textContent = needsNext.title || "Loading";
        }
        if (needsDetail) {
            needsDetail.textContent = needsNext.detail || "Waiting for the next recommended action.";
        }
        if (workerStateNode) {
            workerStateNode.textContent = workerState.label || "Loading";
        }
        if (runtimeState) {
            runtimeState.textContent = snapshot.runtime_state || "Waiting for runtime state.";
        }
        if (queueSummary) {
            queueSummary.textContent = snapshot.queue_summary || "Loading";
        }
        if (queueDetail) {
            queueDetail.textContent = snapshot.queue_detail || "Waiting for queue detail.";
        }
        if (truthSourceLabel) {
            truthSourceLabel.textContent = truthSource.label || "Loading";
        }
        if (truthSourceDetail) {
            truthSourceDetail.textContent = truthSource.detail || "Waiting for runtime source detail.";
        }
        if (heartbeatLabel) {
            heartbeatLabel.textContent = lastHeartbeat.label || "Loading";
        }
        if (heartbeatDetail) {
            heartbeatDetail.textContent = lastHeartbeat.detail || "Waiting for heartbeat detail.";
        }
        if (lastTaskLabel) {
            lastTaskLabel.textContent = lastTask.label || "Loading";
        }
        if (lastTaskDetail) {
            lastTaskDetail.textContent = lastTask.detail || "Waiting for completed-task detail.";
        }
        if (activityList) {
            activityList.innerHTML = recentActivity.length
                ? recentActivity.map((item) => buildOperatorActivityMarkup(item)).join("")
                : '<p class="devValidationEmpty">No recent runtime activity is available.</p>';
        }
        if (progressList) {
            progressList.innerHTML = progressCategories.length
                ? progressCategories.map((item) => `
                    <article class="devMonitorListItem devIntelligenceStageItem">
                        <div class="devIntelligenceStageHead">
                            <span class="devMetricLabel">${escapeHtml(item.label || "")}</span>
                            <span class="statusPill statusPill--${(item.status || "").toLowerCase().includes("strong") ? "good" : "neutral"}">${escapeHtml(item.status || "")}</span>
                        </div>
                        <p>${escapeHtml(item.detail || "")}</p>
                    </article>
                `).join("")
                : '<p class="devValidationEmpty">Loading intelligence progress.</p>';
        }
    }

    function renderLimitsDock(limitsByProvider) {
        const cursorCard = document.getElementById("devLimitsCardCursor");
        const codexCard = document.getElementById("devLimitsCardCodex");
        const cards = [
            { node: cursorCard, provider: "cursor" },
            { node: codexCard, provider: "codex" },
        ];
        cards.forEach(({ node, provider }) => {
            if (!node) return;
            const body = node.querySelector(".devLimitsCardBody");
            if (!body) return;
            const entry = limitsByProvider[provider];
            if (!entry) {
                body.innerHTML = '<p class="devLimitsEmpty" data-field="empty">No data</p>';
                node.classList.remove("devLimitsCard--warn");
                return;
            }
            const pct = entry.remaining_percent ?? 100;
            const remaining = `<p class="devLimitsRemaining" data-field="remaining"><strong>${Number(pct).toFixed(0)}%</strong> remaining</p>`;
            const reset = `<p class="devLimitsReset" data-field="reset">Reset: ${escapeHtml(entry.reset_at || "â€”")}</p>`;
            const notes = entry.notes ? `<p class="devLimitsNotes" data-field="notes">${escapeHtml(entry.notes)}</p>` : "";
            const metaText = `Updated ${escapeHtml(entry.updated_at || "â€”")}${entry.source ? " Â· " + escapeHtml(entry.source) : ""}`;
            body.innerHTML = remaining + reset + notes + `<p class="devLimitsMeta" data-field="meta">${metaText}</p>`;
            node.classList.toggle("devLimitsCard--warn", pct < 20);
        });
    }

    let currentValidationAuditCardCode = "";

    function buildValidationItemMarkup(item, metaText) {
        return `
            <button class="devValidationItem" type="button" data-card-code="${escapeHtml(item.card_code || "")}">
                <strong>${escapeHtml(item.card_code || "")} Â· ${escapeHtml(item.card_name || "Unknown card")}</strong>
                <span>${escapeHtml(metaText || "")}</span>
            </button>
        `;
    }

    function renderValidationAuditList(containerId, items, emptyText, formatter) {
        const container = document.getElementById(containerId);
        if (!container) {
            return;
        }
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {
            container.innerHTML = `<p class="devValidationEmpty">${escapeHtml(emptyText)}</p>`;
            return;
        }
        container.innerHTML = list.map((item) => buildValidationItemMarkup(item, formatter(item))).join("");
    }

    function confidenceTone(confidence) {
        const value = Number(confidence || 0);
        if (value >= 0.9) {
            return "good";
        }
        if (value >= 0.75) {
            return "neutral";
        }
        return "warn";
    }

    function renderValidationAuditInsights(audit, urlBase) {
        const panel = document.getElementById("devValidationPanel");
        if (panel && urlBase) {
            panel.dataset.validationAuditUrlBase = urlBase;
        }
        const recentConflicts = audit.recent_conflicts || [];
        const lowestConfidence = audit.lowest_confidence || [];
        const recentlyValidated = audit.recently_validated || [];
        const rejectedEvidence = audit.rejected_evidence || [];
        renderValidationAuditList(
            "devValidationRecentConflicts",
            recentConflicts,
            "No recent conflicts.",
            (item) => `${String(item.conflict_rule || "no-conflict").replace(/_/g, " ")}`
        );
        renderValidationAuditList(
            "devValidationLowestConfidence",
            lowestConfidence,
            "No validation confidence data yet.",
            (item) => `${Number(item.confidence || 0).toFixed(2)} confidence`
        );
        renderValidationAuditList(
            "devValidationRecentlyValidated",
            recentlyValidated,
            "No recent validation records.",
            (item) => item.verified_at || "Unknown time"
        );
        renderValidationAuditList(
            "devValidationRejectedEvidence",
            rejectedEvidence,
            "No rejected evidence records yet.",
            (item) => `${Number(item.rejected_source_count || 0)} rejected source${Number(item.rejected_source_count || 0) === 1 ? "" : "s"}`
        );

        const input = document.getElementById("devValidationCardCode");
        if (input && !input.value && recentlyValidated[0] && recentlyValidated[0].card_code) {
            input.value = recentlyValidated[0].card_code;
        }
        if (!currentValidationAuditCardCode && recentlyValidated[0] && recentlyValidated[0].card_code && urlBase) {
            void inspectValidationAudit(recentlyValidated[0].card_code, urlBase);
        }
    }

    function renderValidationSourceCard(source, extraClass, fallbackText) {
        if (!source || typeof source !== "object" || Object.keys(source).length === 0) {
            return `<p class="devValidationEmpty">${escapeHtml(fallbackText)}</p>`;
        }
        const sourceName = source.display_name || source.source_id || "Unknown source";
        const trustLabel = source.trust_label || "unknown";
        const summary = [];
        if (source.source_reference) {
            summary.push(`Ref ${source.source_reference}`);
        }
        if (source.review_state) {
            summary.push(source.review_state);
        }
        if (source.rejection_reason) {
            summary.push(source.rejection_reason);
        }
        return `
            <article class="devValidationSourceCard ${escapeHtml(extraClass || "")}">
                <span class="devValidationSourceBadge">${escapeHtml(trustLabel)}</span>
                <strong>${escapeHtml(sourceName)}</strong>
                <p>${escapeHtml(source.source_id || "")}</p>
                ${summary.length ? `<p>${escapeHtml(summary.join(" Â· "))}</p>` : ""}
            </article>
        `;
    }

    function renderValidationDetail(audit) {
        const title = document.getElementById("devValidationDetailTitle");
        const confidence = document.getElementById("devValidationDetailConfidence");
        const summary = document.getElementById("devValidationDetailSummary");
        const canonicalGrid = document.getElementById("devValidationCanonicalGrid");
        const winningSource = document.getElementById("devValidationWinningSource");
        const rejectedSources = document.getElementById("devValidationRejectedSources");
        if (!title || !confidence || !summary || !canonicalGrid || !winningSource || !rejectedSources) {
            return;
        }
        if (!audit) {
            title.textContent = "Pick a card to inspect";
            confidence.textContent = "Read-only";
            confidence.className = "statusPill statusPill--neutral";
            summary.textContent = "Miru will show the winning source, rejected evidence, conflict rule, and the current canonical values for the selected card.";
            canonicalGrid.innerHTML = "";
            winningSource.innerHTML = '<p class="devValidationEmpty">No card selected.</p>';
            rejectedSources.innerHTML = '<p class="devValidationEmpty">No card selected.</p>';
            return;
        }
        const canonical = audit.canonical_values || {};
        const conflictSummary = audit.conflict_summary || {};
        title.textContent = `${audit.card_code || ""} Â· ${canonical.card_name || "Unknown card"}`;
        confidence.textContent = `${Number(audit.confidence || 0).toFixed(2)} confidence`;
        confidence.className = `statusPill statusPill--${confidenceTone(audit.confidence)}`;
        summary.textContent = audit.confidence_reason || conflictSummary.summary || "No confidence reasoning recorded.";
        const canonicalFields = [
            ["Set", canonical.set_name],
            ["Rarity", canonical.rarity],
            ["Color", canonical.color],
            ["Type", canonical.card_type],
            ["Cost", canonical.cost],
            ["Power", canonical.power],
            ["Counter", canonical.counter],
            ["Attribute", canonical.attribute],
            ["Effect Text", canonical.effect_text],
            ["Trigger Text", canonical.trigger_text],
        ];
        canonicalGrid.innerHTML = canonicalFields.map(([label, value]) => `
            <div class="devValidationCanonicalCell">
                <strong>${escapeHtml(label)}</strong>
                <span>${escapeHtml(value || "â€”")}</span>
            </div>
        `).join("");
        winningSource.innerHTML = renderValidationSourceCard(
            audit.winning_source || {},
            Number(audit.confidence || 0) < 0.75 ? "devValidationSourceCard--lowConfidence" : "",
            "No winning source was recorded."
        );
        const rejected = Array.isArray(audit.rejected_sources) ? audit.rejected_sources : [];
        rejectedSources.innerHTML = rejected.length
            ? rejected.map((source) => renderValidationSourceCard(source, "devValidationSourceCard--rejected", "")).join("")
            : '<p class="devValidationEmpty">No rejected sources for this card.</p>';
    }

    async function inspectValidationAudit(cardCode, urlBase) {
        const normalizedCode = String(cardCode || "").trim().toUpperCase();
        if (!normalizedCode || !urlBase) {
            return;
        }
        const input = document.getElementById("devValidationCardCode");
        if (input) {
            input.value = normalizedCode;
        }
        const title = document.getElementById("devValidationDetailTitle");
        const summary = document.getElementById("devValidationDetailSummary");
        if (title) {
            title.textContent = `Loading ${normalizedCode}â€¦`;
        }
        if (summary) {
            summary.textContent = "Fetching validation evidence trail.";
        }
        try {
            const response = await fetch(`${urlBase}/${encodeURIComponent(normalizedCode)}`, {
                headers: {
                    Accept: "application/json",
                    "X-Requested-With": "miru-client-nav",
                },
                credentials: "same-origin",
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || `Request failed with ${response.status}`);
            }
            currentValidationAuditCardCode = normalizedCode;
            renderValidationDetail(payload.audit || null);
        } catch (error) {
            renderValidationDetail(null);
            const detailTitle = document.getElementById("devValidationDetailTitle");
            const detailSummary = document.getElementById("devValidationDetailSummary");
            if (detailTitle) {
                detailTitle.textContent = `${normalizedCode} audit unavailable`;
            }
            if (detailSummary) {
                detailSummary.textContent = error instanceof Error ? error.message : "Validation audit could not be loaded.";
            }
        }
    }

    function renderImageCoverageList(items) {
        const container = document.getElementById("devCoverageList");
        const setCount = document.getElementById("devCoverageSetCount");
        const averagePercent = document.getElementById("devCoverageAveragePercent");
        const verifiedTotal = document.getElementById("devCoverageVerifiedTotal");
        const missingTotal = document.getElementById("devCoverageMissingTotal");
        const accordionMeta = document.getElementById("devCoverageAccordionMeta");
        const accordionTitle = document.getElementById("devCoverageAccordionTitle");
        const accordionHint = document.getElementById("devCoverageAccordionHint");
        if (!container) {
            return;
        }
        container.innerHTML = "";
        if (!Array.isArray(items) || items.length === 0) {
            if (setCount) {
                setCount.textContent = "0";
            }
            if (averagePercent) {
                averagePercent.textContent = "0.0%";
            }
            if (verifiedTotal) {
                verifiedTotal.textContent = "0";
            }
            if (missingTotal) {
                missingTotal.textContent = "0";
            }
            if (accordionMeta) {
                accordionMeta.textContent = "No data";
            }
            if (accordionTitle) {
                accordionTitle.textContent = "No set-by-set image ingestion yet";
            }
            if (accordionHint) {
                accordionHint.textContent = "Expand later when image coverage starts reporting by set.";
            }
            const empty = document.createElement("p");
            empty.className = "devCoverageEmpty";
            empty.textContent = "No image coverage data yet.";
            container.appendChild(empty);
            return;
        }
        const totals = items.reduce((accumulator, item) => {
            accumulator.percent += Number(item.coverage_percent || 0);
            accumulator.verified += Number(item.images_verified || 0);
            accumulator.missing += Number(item.images_missing || 0);
            return accumulator;
        }, { percent: 0, verified: 0, missing: 0 });
        if (setCount) {
            setCount.textContent = String(items.length);
        }
        if (averagePercent) {
            averagePercent.textContent = `${(totals.percent / items.length).toFixed(1)}%`;
        }
        if (verifiedTotal) {
            verifiedTotal.textContent = Number(totals.verified).toLocaleString();
        }
        if (missingTotal) {
            missingTotal.textContent = Number(totals.missing).toLocaleString();
        }
        if (accordionMeta) {
            accordionMeta.textContent = `${items.length} sets`;
        }
        if (accordionTitle) {
            accordionTitle.textContent = "Show set-by-set image ingestion";
        }
        if (accordionHint) {
            accordionHint.textContent = "Open this only when you need per-set tracked, verified, missing, and total counts.";
        }
        items.forEach((item) => {
            const row = document.createElement("article");
            row.className = "devCoverageRow";
            row.dataset.setCode = String(item.set_code || "");
            row.dataset.milestoneStage = String(item.milestone_stage ?? "");

            const header = document.createElement("div");
            header.className = "devCoverageHeader";
            const title = document.createElement("div");
            title.className = "devCoverageTitle";
            const setLabel = document.createElement("strong");
            setLabel.className = "devCoverageSet";
            setLabel.textContent = String(item.set_code || "");
            const stageLabel = document.createElement("span");
            const stageText = String(item.milestone_label || "not_started");
            stageLabel.className = `devCoverageStage devCoverageStage--${stageText}`;
            stageLabel.textContent = stageText;
            title.appendChild(setLabel);
            title.appendChild(stageLabel);
            const percentLabel = document.createElement("span");
            percentLabel.className = "devCoveragePercent";
            percentLabel.textContent = `${Number(item.coverage_percent || 0).toFixed(1)}%`;
            header.appendChild(title);
            header.appendChild(percentLabel);

            const bar = document.createElement("div");
            bar.className = "devCoverageBar";
            bar.setAttribute("aria-hidden", "true");
            const fill = document.createElement("span");
            const percent = Math.max(0, Math.min(Number(item.coverage_percent || 0), 100));
            fill.style.width = `${percent}%`;
            bar.appendChild(fill);

            const meta = document.createElement("div");
            meta.className = "devCoverageMeta";
            const tracked = document.createElement("span");
            tracked.textContent = `${Number(item.images_tracked || 0)} tracked`;
            const verified = document.createElement("span");
            verified.textContent = `${Number(item.images_verified || 0)} verified`;
            const missing = document.createElement("span");
            missing.textContent = `${Number(item.images_missing || 0)} missing`;
            const total = document.createElement("span");
            total.textContent = `${Number(item.total_cards || 0)} total`;
            meta.appendChild(tracked);
            meta.appendChild(verified);
            meta.appendChild(missing);
            meta.appendChild(total);

            row.appendChild(header);
            row.appendChild(bar);
            row.appendChild(meta);
            container.appendChild(row);
        });
    }

    function initializeDevMonitor() {
        stopDevMonitorPolling();
        resetDevDeferredSections();
        const root = document.getElementById("devMonitor");
        if (!root) {
            return;
        }
        initializeDevConsoleTabs();
        initializeVoyageMapShell();
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
        const operatorConsoleUrl = "/api/dev/operator-console";
        const operatorActionUrl = "/api/dev/operator-console/action";
        const operatorQueryUrl = "/api/dev/operator-console/query";
        const validationPanel = document.getElementById("devValidationPanel");
        const inspectButton = document.getElementById("devValidationInspectButton");
        const inspectInput = document.getElementById("devValidationCardCode");
        const operatorActionButtons = Array.from(document.querySelectorAll("[data-operator-action]"));
        const operatorActionFeedback = document.getElementById("devOperatorActionFeedback");
        const operatorQueryForm = document.getElementById("devOperatorQueryForm");
        const operatorQueryInput = document.getElementById("devOperatorQueryInput");
        const operatorQueryButton = document.getElementById("devOperatorQueryButton");
        const operatorQueryAnswer = document.getElementById("devOperatorQueryAnswer");
        const operatorQueryMeta = document.getElementById("devOperatorQueryMeta");
        const operatorSuggestionRow = document.getElementById("devOperatorQuerySuggestions");
        const copySystemSummaryBtn = document.getElementById("devCopySystemSummaryBtn");
        const workerRouterForm = document.getElementById("devWorkerRouterForm");
        const workerRouterInput = document.getElementById("devWorkerRouterInput");
        const workerRouterPrompt = document.getElementById("devWorkerRouterPrompt");
        const copyWorkerPromptBtn = document.getElementById("devCopyWorkerPromptBtn");
        const judgmentCopyPromptBtn = document.getElementById("devJudgmentCopyPromptBtn");
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

        const MAX_LEARNING_LOOP_CYCLES = 3;
        const LOOP_POLL_INTERVAL_MS = 15000;
        const LOOP_PAUSE_BETWEEN_CYCLES_MS = 20000;
        const LOOP_MAX_WAIT_PER_CYCLE_MS = 2 * 60 * 1000;

        async function runLearningLoop(btn) {
            setDevControlFeedback("Run Learning Loop: starting (up to " + MAX_LEARNING_LOOP_CYCLES + " cycles)â€¦", false);
            for (let cycle = 1; cycle <= MAX_LEARNING_LOOP_CYCLES; cycle++) {
                setDevControlFeedback("Run Learning Loop: cycle " + cycle + "/" + MAX_LEARNING_LOOP_CYCLES + "â€¦", false);
                try {
                    const startRes = await fetch("/api/dev/start-learner", {
                        method: "POST",
                        headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                        credentials: "same-origin",
                        body: "{}",
                    });
                    const startData = await startRes.json().catch(function () { return {}; });
                    if (startData.already_running) {
                        setDevControlFeedback("Run Learning Loop: learner already running, waiting for idle (cycle " + cycle + "/" + MAX_LEARNING_LOOP_CYCLES + ")â€¦", false);
                    }
                } catch (e) {
                    setDevControlFeedback("Run Learning Loop: start failed â€“ " + (e && e.message ? e.message : "request failed"), true);
                    return;
                }
                const cycleStart = Date.now();
                while (Date.now() - cycleStart < LOOP_MAX_WAIT_PER_CYCLE_MS) {
                    try {
                        const statusRes = await fetch(apiUrl + (apiUrl.indexOf("?") >= 0 ? "&" : "?") + "lightweight=1");
                        const statusData = await statusRes.json().catch(function () { return {}; });
                        const state = (statusData.learning_engine && statusData.learning_engine.learner_state) || "";
                        if (state === "Idle" || state === "Running (waiting)") {
                            break;
                        }
                    } catch (e) {
                        /* ignore poll errors */
                    }
                    await new Promise(function (r) { setTimeout(r, LOOP_POLL_INTERVAL_MS); });
                }
                if (cycle < MAX_LEARNING_LOOP_CYCLES) {
                    setDevControlFeedback("Run Learning Loop: cycle " + cycle + " done. Pausing " + (LOOP_PAUSE_BETWEEN_CYCLES_MS / 1000) + "s before nextâ€¦", false);
                    await new Promise(function (r) { setTimeout(r, LOOP_PAUSE_BETWEEN_CYCLES_MS); });
                }
            }
            setDevControlFeedback("Run Learning Loop finished. " + MAX_LEARNING_LOOP_CYCLES + " cycles run. Use Stop Learner to run sync and pause.", false);
            void loadDevStatus({ manual: true, lightweight: true });
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
                if (action === "run-learning-loop") {
                    runLearningLoop(btn).finally(function () {
                        btn.disabled = false;
                    });
                    return;
                }
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
                void loadOperatorConsole();
                void loadPendingApprovals();
                void loadDeferredPanelsForTab(getCurrentDevTab());
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

        async function loadOperatorConsole({ force = false } = {}) {
            try {
                const response = await fetch(force ? `${operatorConsoleUrl}?force=1` : operatorConsoleUrl, {
                    headers: {
                        Accept: "application/json",
                        "X-Requested-With": "miru-client-nav",
                    },
                    credentials: "same-origin",
                });
                if (!response.ok) {
                    throw new Error(`Operator console failed with ${response.status}`);
                }
                const payload = await response.json();
                renderOperatorConsole(payload);
            } catch (error) {
                if (operatorActionFeedback) {
                    operatorActionFeedback.textContent = "Operator console is currently unavailable.";
                }
            }
        }

        const pendingApprovalsList = document.getElementById("devPendingApprovalsList");
        const pendingApprovalsEmpty = document.getElementById("devPendingApprovalsEmpty");
        const pendingApprovalsRefreshBtn = document.getElementById("devPendingApprovalsRefresh");
        const projectMiruLink = (root.dataset.projectMiruLink || "").trim();

        function renderPendingApprovals(items) {
            if (!pendingApprovalsList) {
                return;
            }
            if (!Array.isArray(items) || items.length === 0) {
                if (pendingApprovalsEmpty) {
                    pendingApprovalsEmpty.textContent = "No pending approvals.";
                    pendingApprovalsEmpty.classList.remove("isHidden");
                }
                pendingApprovalsList.querySelectorAll(".devPendingApprovalRow").forEach((el) => el.remove());
                return;
            }
            if (pendingApprovalsEmpty) {
                pendingApprovalsEmpty.classList.add("isHidden");
            }
            const fragment = document.createDocumentFragment();
            items.forEach((item) => {
                const row = document.createElement("div");
                row.className = "devPendingApprovalRow";
                row.dataset.id = String(item.id);
                row.dataset.cardCode = String(item.card_code || "");
                row.dataset.sourceId = String(item.source_id || "");
                const head = document.createElement("div");
                head.className = "devPendingApprovalHead";
                const card = document.createElement("span");
                card.className = "devPendingApprovalCard";
                card.textContent = item.card_code || "â€”";
                const source = document.createElement("span");
                source.className = "devPendingApprovalSource";
                source.textContent = item.source_id || "â€”";
                head.appendChild(card);
                head.appendChild(source);
                const meta = document.createElement("div");
                meta.className = "devPendingApprovalMeta";
                const reason = document.createElement("span");
                reason.className = "devPendingApprovalReason";
                reason.textContent = item.reason || "â€”";
                const confidence = document.createElement("span");
                confidence.className = "devPendingApprovalConfidence";
                confidence.textContent = typeof item.confidence === "number" ? (item.confidence * 100).toFixed(0) + "%" : "â€”";
                meta.appendChild(reason);
                meta.appendChild(confidence);
                const extra = document.createElement("div");
                extra.className = "devPendingApprovalExtra";
                if (item.created_at) {
                    const created = document.createElement("span");
                    created.className = "devPendingApprovalCreated";
                    created.textContent = String(item.created_at).trim();
                    extra.appendChild(created);
                }
                const idLabel = document.createElement("span");
                idLabel.className = "devPendingApprovalId";
                idLabel.textContent = "#" + String(item.id);
                extra.appendChild(idLabel);
                const actions = document.createElement("div");
                actions.className = "devPendingApprovalActions";
                const approveBtn = document.createElement("button");
                approveBtn.type = "button";
                approveBtn.className = "utilityButton devPendingApprovalBtn devPendingApprovalBtn--approve";
                approveBtn.textContent = "Approve";
                approveBtn.dataset.action = "approve";
                const rejectBtn = document.createElement("button");
                rejectBtn.type = "button";
                rejectBtn.className = "utilityButton devPendingApprovalBtn devPendingApprovalBtn--reject";
                rejectBtn.textContent = "Reject";
                rejectBtn.dataset.action = "reject";
                const inspectBtn = document.createElement("button");
                inspectBtn.type = "button";
                inspectBtn.className = "utilityButton devPendingApprovalBtn devPendingApprovalBtn--inspect";
                inspectBtn.textContent = "Inspect";
                inspectBtn.dataset.action = "inspect";
                actions.appendChild(approveBtn);
                actions.appendChild(rejectBtn);
                actions.appendChild(inspectBtn);
                row.appendChild(head);
                row.appendChild(meta);
                row.appendChild(extra);
                row.appendChild(actions);
                fragment.appendChild(row);
            });
            pendingApprovalsList.querySelectorAll(".devPendingApprovalRow").forEach((el) => el.remove());
            pendingApprovalsList.insertBefore(fragment, pendingApprovalsEmpty);
        }

        async function loadPendingApprovals() {
            if (!pendingApprovalsList) {
                return;
            }
            if (pendingApprovalsEmpty) {
                pendingApprovalsEmpty.textContent = "Loadingâ€¦";
                pendingApprovalsEmpty.classList.remove("isHidden");
            }
            const timeoutMs = 12000;
            const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
            const timeoutId = ac ? window.setTimeout(() => ac.abort(), timeoutMs) : null;
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
                const items = Array.isArray(data && data.items) ? data.items : [];
                renderPendingApprovals(items);
            } catch (err) {
                if (pendingApprovalsEmpty) {
                    pendingApprovalsEmpty.textContent = err && err.name === "AbortError"
                        ? "Request timed out. Tap Refresh to try again."
                        : "Could not load pending approvals. Tap Refresh to try again.";
                    pendingApprovalsEmpty.classList.remove("isHidden");
                }
                if (pendingApprovalsList) {
                    pendingApprovalsList.querySelectorAll(".devPendingApprovalRow").forEach((el) => el.remove());
                }
            } finally {
                if (timeoutId) window.clearTimeout(timeoutId);
                if (pendingApprovalsEmpty && pendingApprovalsEmpty.textContent === "Loadingâ€¦") {
                    pendingApprovalsEmpty.textContent = "No pending approvals.";
                    pendingApprovalsEmpty.classList.remove("isHidden");
                }
            }
        }

        function handlePendingApprovalAction(event) {
            const btn = event.target instanceof HTMLElement ? event.target.closest(".devPendingApprovalBtn") : null;
            if (!btn || !btn.dataset.action) {
                return;
            }
            const row = btn.closest(".devPendingApprovalRow");
            if (!row) {
                return;
            }
            const id = row.dataset.id;
            const cardCode = row.dataset.cardCode || "";
            const sourceId = row.dataset.sourceId || "";
            const action = btn.dataset.action;
            if (action === "inspect") {
                const base = projectMiruLink || "/";
                const url = base.replace(/\/$/, "") + "/card/" + encodeURIComponent(cardCode);
                window.open(url, "_blank", "noopener,noreferrer");
                return;
            }
            if (action === "reject") {
                btn.disabled = true;
                fetch("/api/dev/reject-validation", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                    credentials: "same-origin",
                    body: JSON.stringify({ id: id ? parseInt(id, 10) : null }),
                })
                    .then((r) => r.json().catch(() => ({})))
                    .then(() => {
                        void loadPendingApprovals();
                    })
                    .finally(() => {
                        btn.disabled = false;
                    });
                return;
            }
            if (action === "approve") {
                btn.disabled = true;
                fetch("/api/dev/approve-validation", {
                    method: "POST",
                    headers: { "Content-Type": "application/json", Accept: "application/json", "X-Requested-With": "miru-client-nav" },
                    credentials: "same-origin",
                    body: JSON.stringify({ id: id ? parseInt(id, 10) : null, card_code: cardCode, source_id: sourceId }),
                })
                    .then((r) => r.json().catch(() => ({})))
                    .then((data) => {
                        if (data && data.ok !== false) {
                            void loadPendingApprovals();
                        } else if (setDevControlFeedback) {
                            setDevControlFeedback(data && data.error ? data.error : "Approve failed.", true);
                        }
                    })
                    .finally(() => {
                        btn.disabled = false;
                    });
            }
        }

        if (pendingApprovalsList && pendingApprovalsList.dataset.devBound !== "true") {
            pendingApprovalsList.dataset.devBound = "true";
            pendingApprovalsList.addEventListener("click", handlePendingApprovalAction);
        }
        if (pendingApprovalsRefreshBtn && pendingApprovalsRefreshBtn.dataset.devBound !== "true") {
            pendingApprovalsRefreshBtn.dataset.devBound = "true";
            pendingApprovalsRefreshBtn.addEventListener("click", () => {
                void loadPendingApprovals();
            });
        }
        const reviewApprovalsBtn = document.getElementById("devReviewApprovalsBtn");
        if (reviewApprovalsBtn && reviewApprovalsBtn.dataset.devBound !== "true") {
            reviewApprovalsBtn.dataset.devBound = "true";
            reviewApprovalsBtn.addEventListener("click", function () {
                switchDevConsoleTab("overview", { updateHash: true });
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
        if (pendingApprovalsList) {
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
                        if (isDevCockpitPage) {
                            return;
                        }
                        void loadOperatorConsole({ force: true });
                        return loadDeferredPanelsForTab(getCurrentDevTab(), { force: true });
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

        if (inspectButton && inspectButton.dataset.devBound !== "true") {
            inspectButton.dataset.devBound = "true";
            inspectButton.addEventListener("click", () => {
                const urlBase = (validationPanel && validationPanel.dataset.validationAuditUrlBase) || "";
                void inspectValidationAudit(inspectInput && inspectInput.value, urlBase);
            });
        }
        if (inspectInput && inspectInput.dataset.devBound !== "true") {
            inspectInput.dataset.devBound = "true";
            inspectInput.addEventListener("keydown", (event) => {
                if (event.key !== "Enter") {
                    return;
                }
                event.preventDefault();
                const urlBase = (validationPanel && validationPanel.dataset.validationAuditUrlBase) || "";
                void inspectValidationAudit(inspectInput.value, urlBase);
            });
        }
        if (validationPanel && validationPanel.dataset.devBound !== "true") {
            validationPanel.dataset.devBound = "true";
            validationPanel.addEventListener("click", (event) => {
                const target = event.target instanceof HTMLElement ? event.target.closest(".devValidationItem") : null;
                if (!target) {
                    return;
                }
                const urlBase = validationPanel.dataset.validationAuditUrlBase || "";
                void inspectValidationAudit(target.dataset.cardCode || "", urlBase);
            });
        }
        operatorActionButtons.forEach((button) => {
            if (button.dataset.devBound === "true") {
                return;
            }
            button.dataset.devBound = "true";
            button.addEventListener("click", async () => {
                const action = button.dataset.operatorAction || "";
                if (!action) {
                    return;
                }
                button.disabled = true;
                if (operatorActionFeedback) {
                    operatorActionFeedback.textContent = "Refreshing operator telemetryâ€¦";
                }
                try {
                    const response = await fetch(operatorActionUrl, {
                        method: "POST",
                        headers: {
                            Accept: "application/json",
                            "Content-Type": "application/json",
                            "X-Requested-With": "miru-client-nav",
                        },
                        credentials: "same-origin",
                        body: JSON.stringify({ action }),
                    });
                    const payload = await response.json();
                    if (!response.ok || !payload.ok) {
                        throw new Error(payload.error || `Operator action failed with ${response.status}`);
                    }
                    renderOperatorConsole(payload.operator_console || {});
                    if (operatorActionFeedback) {
                        operatorActionFeedback.textContent = payload.message || "Operator action completed.";
                    }
                    void loadDevStatus({ lightweight: true });
                } catch (error) {
                    if (operatorActionFeedback) {
                        operatorActionFeedback.textContent = error instanceof Error ? error.message : "Operator action failed.";
                    }
                } finally {
                    button.disabled = false;
                }
            });
        });
        if (operatorSuggestionRow && operatorSuggestionRow.dataset.devBound !== "true") {
            operatorSuggestionRow.dataset.devBound = "true";
            operatorSuggestionRow.addEventListener("click", (event) => {
                const target = event.target instanceof HTMLElement ? event.target.closest(".tipPill") : null;
                if (!target || !operatorQueryInput) {
                    return;
                }
                operatorQueryInput.value = target.textContent || "";
                operatorQueryInput.focus();
            });
        }
        if (operatorQueryForm && operatorQueryForm.dataset.devBound !== "true") {
            operatorQueryForm.dataset.devBound = "true";
            operatorQueryForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                const query = String((operatorQueryInput && operatorQueryInput.value) || "").trim();
                if (!query) {
                    if (operatorQueryMeta) {
                        operatorQueryMeta.textContent = "Enter a short operator prompt first.";
                    }
                    return;
                }
                if (operatorQueryButton) {
                    operatorQueryButton.disabled = true;
                }
                if (operatorQueryMeta) {
                    operatorQueryMeta.textContent = "Checking the live operator snapshot.";
                }
                try {
                    const response = await fetch(operatorQueryUrl, {
                        method: "POST",
                        headers: {
                            Accept: "application/json",
                            "Content-Type": "application/json",
                            "X-Requested-With": "miru-client-nav",
                        },
                        credentials: "same-origin",
                        body: JSON.stringify({ query }),
                    });
                    const payload = await response.json();
                    if (!response.ok || !payload.ok) {
                        throw new Error(payload.error || `Operator query failed with ${response.status}`);
                    }
                    if (operatorQueryAnswer) {
                        operatorQueryAnswer.textContent = payload.answer || "No operator answer was returned.";
                    }
                    if (operatorQueryMeta) {
                        operatorQueryMeta.textContent = payload.meta
                            || (payload.matched
                                ? `Answered from live runtime status at ${payload.updated_at || "now"}.`
                                : "That prompt is not supported yet, so the answer stayed conservative.");
                    }
                } catch (error) {
                    if (operatorQueryAnswer) {
                        operatorQueryAnswer.textContent = "The operator query could not be completed.";
                    }
                    if (operatorQueryMeta) {
                        operatorQueryMeta.textContent = error instanceof Error ? error.message : "Operator query failed.";
                    }
                } finally {
                    if (operatorQueryButton) {
                        operatorQueryButton.disabled = false;
                    }
                }
            });
        }

        if (workerRouterForm && workerRouterForm.dataset.devBound !== "true") {
            workerRouterForm.dataset.devBound = "true";
            workerRouterForm.addEventListener("submit", (event) => {
                event.preventDefault();
                const result = inferWorkerRoute(workerRouterInput ? workerRouterInput.value : "", lastDevStatusPayload);
                renderWorkerRouteRecommendation(result);
            });
        }
        if (copyWorkerPromptBtn && copyWorkerPromptBtn.dataset.devBound !== "true") {
            copyWorkerPromptBtn.dataset.devBound = "true";
            copyWorkerPromptBtn.addEventListener("click", async () => {
                const text = workerRouterPrompt ? workerRouterPrompt.value : "";
                const mode = await copyTextWithFallback({
                    text,
                    clipboard: navigator.clipboard,
                    documentRef: document,
                    target: workerRouterPrompt,
                    successText: "Prompt copied.",
                    fallbackText: "Clipboard copy is blocked here. The generated prompt is selected for manual copy.",
                    emptyText: "Route a task first so there is a prompt to copy.",
                });
                if (mode === "clipboard" || mode === "legacy") {
                    flashButtonState(copyWorkerPromptBtn, "Copied");
                } else if (mode === "manual" && workerRouterPrompt) {
                    workerRouterPrompt.focus();
                    workerRouterPrompt.select();
                    flashButtonState(copyWorkerPromptBtn, "Select text");
                }
            });
        }
        if (copySystemSummaryBtn && copySystemSummaryBtn.dataset.devBound !== "true") {
            copySystemSummaryBtn.dataset.devBound = "true";
            copySystemSummaryBtn.addEventListener("click", async () => {
                const text = buildLiveControlSummary(lastDevStatusPayload);
                const mode = await copyTextWithFallback({
                    text,
                    clipboard: navigator.clipboard,
                    documentRef: document,
                    successText: "System summary copied.",
                    fallbackText: "Clipboard copy is blocked here. Use the visible summary text for manual copy.",
                    emptyText: "System summary is not available yet.",
                });
                if (mode === "clipboard" || mode === "legacy") {
                    flashButtonState(copySystemSummaryBtn, "Copied");
                } else if (mode === "manual") {
                    flashButtonState(copySystemSummaryBtn, "Copy manually");
                }
            });
        }
        if (judgmentCopyPromptBtn && judgmentCopyPromptBtn.dataset.devBound !== "true") {
            judgmentCopyPromptBtn.dataset.devBound = "true";
            judgmentCopyPromptBtn.addEventListener("click", async () => {
                const text = judgmentCopyPromptBtn.dataset.prompt || "";
                if (!text) {
                    flashButtonState(judgmentCopyPromptBtn, "No prompt");
                    return;
                }
                const mode = await copyTextWithFallback({
                    text,
                    clipboard: navigator.clipboard,
                    documentRef: document,
                    successText: "Recommended prompt copied.",
                    fallbackText: "Clipboard copy is blocked here. Use the judgment recommendation manually.",
                    emptyText: "No judgment prompt is available for this recommendation.",
                });
                if (mode === "clipboard" || mode === "legacy") {
                    flashButtonState(judgmentCopyPromptBtn, "Copied");
                } else if (mode === "manual") {
                    flashButtonState(judgmentCopyPromptBtn, "Copy manually");
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
                const text = ta && typeof ta.value === "string" ? ta.value : "";
                if (!text) {
                    flashButtonState(devHandoffCopyBtn, "Empty");
                    return;
                }
                const mode = await copyTextWithFallback({
                    text,
                    clipboard: navigator.clipboard,
                    documentRef: document,
                    successText: "Prompt copied.",
                    fallbackText: "Select the prompt text manually.",
                    emptyText: "No prompt text.",
                });
                if (mode === "clipboard" || mode === "legacy") {
                    flashButtonState(devHandoffCopyBtn, "Copied");
                } else if (mode === "manual") {
                    flashButtonState(devHandoffCopyBtn, "Select text");
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
        if (devRuntimeRestart18080Btn) {
            devRuntimeRestart18080Btn.addEventListener("click", async () => {
                devRuntimeRestart18080Btn.disabled = true;
                setRuntimeFeedback("Restarting Project Miruâ€¦", false, true);
                try {
                    const r = await fetch("/api/runtime/restart/18080", {
                        method: "POST",
                        headers: runtimeRestartHeaders(),
                        credentials: "same-origin",
                    });
                    const data = await r.json().catch(() => ({}));
                    if (r.ok && data.ok) {
                        setRuntimeFeedback(data.message + (data.detail ? ": " + data.detail : ""), false, false);
                        loadRuntimeStatus();
                    } else {
                        setRuntimeFeedback((data.error || data.detail || "Restart failed") + (r.status === 403 ? " (this machine or Tailscale)" : ""), true, false);
                    }
                } catch (e) {
                    setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
                } finally {
                    devRuntimeRestart18080Btn.disabled = false;
                }
            });
        }
        if (devRuntimeRestart18765Btn) {
            devRuntimeRestart18765Btn.addEventListener("click", async () => {
                devRuntimeRestart18765Btn.disabled = true;
                setRuntimeFeedback("Restarting Miru AIâ€¦ This tab will close.", false, true);
                try {
                    const r = await fetch("/api/runtime/restart/18765", {
                        method: "POST",
                        headers: runtimeRestartHeaders(),
                        credentials: "same-origin",
                    });
                    const data = await r.json().catch(() => ({}));
                    if (r.status === 202 && data.ok) {
                        setRuntimeFeedback(data.detail || data.message, false, true);
                    } else {
                        setRuntimeFeedback((data.error || data.detail || "Restart failed") + (r.status === 403 ? " (this machine or Tailscale)" : ""), true, false);
                        devRuntimeRestart18765Btn.disabled = false;
                    }
                } catch (e) {
                    setRuntimeFeedback(e && e.message ? e.message : "Request failed", true, false);
                    devRuntimeRestart18765Btn.disabled = false;
                }
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
                const activeTab = getCurrentDevTab();
                if (!activeTab || activeTab === "overview") {
                    void loadOperatorConsole();
                }
                return loadDeferredPanelsForTab(activeTab);
            });
        }, monitorRefreshMs);
    }

    function initializePageBehaviors() {
        syncPageConfig();

        initializeDevMonitor();

        const form = document.getElementById("miruForm") || document.getElementById("miruHomeForm");
        if (!form || form.dataset.miruBound === "true") {
            return;
        }
        form.dataset.miruBound = "true";

        const modeInputs = Array.from(form.querySelectorAll('input[name="mode"]'));
        const requestText = document.getElementById("requestText");
        const requestHelp = document.getElementById("requestHelp");
        const requestExample = document.getElementById("requestExample");
        const modeHint = document.getElementById("modeHint");
        const runButton = document.getElementById("runButton");
        const clearButton = document.getElementById("clearButton");
        const pasteButton = document.getElementById("pasteButton");
        const presetButtons = Array.from(document.querySelectorAll(".presetButton"));
        const loadingCard = document.getElementById("loadingCard");
        const resultCard = document.getElementById("resultCard");
        const resultMeta = document.getElementById("resultMeta");
        const resultHint = document.getElementById("resultHint");
        const resultReadable = document.getElementById("resultReadable");
        const manualCopyBlock = document.getElementById("manualCopyBlock");
        const resultOutput = document.getElementById("resultOutput");
        const copyButton = document.getElementById("copyButton");
        const selectButton = document.getElementById("selectButton");
        const copyFeedback = document.getElementById("copyFeedback");
        const errorCard = document.getElementById("errorCard");
        const errorMeta = document.getElementById("errorMeta");
        const errorOutput = document.getElementById("errorOutput");

        let isRunning = false;
        let copyResetTimer = null;
        let lastPasteTriggerAt = 0;
        const launchParams = new URLSearchParams(window.location.search || "");

        function applyLaunchPrefill() {
            if (!requestText || requestText.value.trim()) {
                return;
            }
            const prefills = [
                launchParams.get("request_text"),
                launchParams.get("prompt"),
                launchParams.get("q"),
            ];
            const prefillText = prefills.find((value) => String(value || "").trim()) || "";
            if (!prefillText) {
                return;
            }
            requestText.value = prefillText;
            const launchMode = String(launchParams.get("mode") || "").trim();
            if (launchMode) {
                setSelectedMode(launchMode);
            }
            updateModeUi();
        }

        function getSelectedMode() {
            const checked = modeInputs.find((input) => input.checked);
            if (checked) {
                return checked.value;
            }
            return config.defaultMode || "card lookup";
        }

        function setSelectedMode(mode) {
            const targetMode = modeMap[mode] ? mode : (config.defaultMode || "card lookup");
            modeInputs.forEach((input) => {
                input.checked = input.value === targetMode;
            });
        }

        function hidePanels() {
            if (loadingCard) {
                loadingCard.classList.add("isHidden");
            }
            if (resultCard) {
                resultCard.classList.add("isHidden");
            }
            if (errorCard) {
                errorCard.classList.add("isHidden");
            }
        }

        function hideManualCopyArea() {
            if (manualCopyBlock) {
                manualCopyBlock.classList.add("isHidden");
            }
        }

        function showManualCopyArea({ shouldSelect } = { shouldSelect: false }) {
            if (!manualCopyBlock) {
                return;
            }
            manualCopyBlock.classList.remove("isHidden");
            if (!shouldSelect) {
                return;
            }
            selectTextTarget(resultOutput);
        }

        function setCopyFeedback(message, tone) {
            if (!copyFeedback) {
                return;
            }

            copyFeedback.textContent = message;
            copyFeedback.dataset.tone = tone || "";
            window.clearTimeout(copyResetTimer);
            if (!message) {
                return;
            }

            copyResetTimer = window.setTimeout(() => {
                copyFeedback.textContent = "";
                copyFeedback.dataset.tone = "";
            }, 2600);
        }

        function updateModeUi() {
            const selectedMode = getSelectedMode();
            const modeConfig = modeMap[selectedMode] || modeConfigs[0] || {
                label: "Card Lookup",
                hint: "",
                request_help: "",
                request_placeholder: "",
                request_example: "",
                result_hint: "",
            };
            if (modeHint) {
                modeHint.innerHTML = buildModeHintHtml(modeConfig);
            }
            if (requestHelp) {
                requestHelp.textContent = requestHelp.dataset.homeHelp || modeConfig.request_help || "Ask about a card, set, variant, mechanic, or missing catalog field.";
            }
            if (requestText) {
                requestText.placeholder = requestText.dataset.homePlaceholder || modeConfig.request_placeholder || "Example: What is OP09-001?";
            }
            if (requestExample) {
                requestExample.textContent = requestExample.dataset.homeExample || modeConfig.request_example || "";
            }
        }

        applyLaunchPrefill();

        function applyPreset(button) {
            const mode = button.dataset.mode || config.defaultMode || "card lookup";
            const requestValue = button.dataset.request || "";

            setSelectedMode(mode);
            requestText.value = requestValue;
            hidePanels();
            setCopyFeedback("", "");
            updateModeUi();
            if (requestText) {
                requestText.focus();
            }
        }

        function clearAll() {
            setSelectedMode(config.defaultMode || "card lookup");
            resetViewState({
                requestField: requestText,
                resultReadableField: resultReadable,
                resultField: resultOutput,
                errorField: errorOutput,
                resultMetaField: resultMeta,
                errorMetaField: errorMeta,
                setCopyFeedback,
                hidePanels,
                hideManualCopy: hideManualCopyArea,
            });
            updateModeUi();
            if (requestText) {
                requestText.blur();
            }
        }

        async function copyResult() {
            const text = String(resultOutput.value || resultReadable.textContent || "").trim();
            if (!resultOutput.value && text) {
                resultOutput.value = text;
            }
            const mode = await copyTextWithFallback({
                text,
                clipboard: navigator.clipboard,
                documentRef: document,
                target: resultOutput,
                onManualFallback: () => {
                    showManualCopyArea({ shouldSelect: true });
                },
                setFeedback: setCopyFeedback,
                successText: config.copySuccessText || "Copied.",
                fallbackText: config.copyFallbackText || "Clipboard copy is blocked here. Miru selected the result so you can copy it manually.",
                emptyText: "There is no result to copy yet.",
            });
            if (mode === "clipboard" || mode === "legacy") {
                hideManualCopyArea();
            }
        }

        async function pasteQuestion() {
            const mode = await pasteTextWithFallback({
                clipboard: navigator.clipboard,
                target: requestText,
                setFeedback: setCopyFeedback,
                successText: config.pasteSuccessText || "Pasted into the question box.",
                fallbackText: config.pasteFallbackText || "Browser paste is blocked here. Tap the question box and use your device paste action.",
                emptyText: "Clipboard is empty.",
            });
            if (mode === "clipboard") {
                hidePanels();
            }
        }

        function shouldSkipDuplicatePasteTrigger() {
            const now = Date.now();
            if (now - lastPasteTriggerAt < 250) {
                return true;
            }
            lastPasteTriggerAt = now;
            return false;
        }

        function handlePasteTrigger(event) {
            if (event) {
                event.preventDefault();
            }
            if (shouldSkipDuplicatePasteTrigger()) {
                return;
            }
            void pasteQuestion();
        }

        function selectResult() {
            const text = String(resultOutput.value || resultReadable.textContent || "").trim();
            if (!text) {
                setCopyFeedback("There is no result to select yet.", "warn");
                return;
            }
            resultOutput.value = text;
            showManualCopyArea({ shouldSelect: true });
            setCopyFeedback("Result selected. Use your browser or device copy action.", "warn");
        }

        async function submitForm(event) {
            event.preventDefault();
            if (isRunning || config.runDisabled) {
                return;
            }

            isRunning = true;
            resetViewState({
                requestField: null,
                resultReadableField: resultReadable,
                resultField: resultOutput,
                errorField: errorOutput,
                resultMetaField: resultMeta,
                errorMetaField: errorMeta,
                setCopyFeedback,
                hidePanels,
                hideManualCopy: hideManualCopyArea,
            });
            runButton.disabled = true;
            runButton.textContent = "Asking...";
            if (clearButton) {
                clearButton.disabled = true;
            }
            if (pasteButton) {
                pasteButton.disabled = true;
            }
            if (loadingCard) {
                loadingCard.classList.remove("isHidden");
            }

            const runPayload = buildRunPayload({
                mode: getSelectedMode(),
                requestText: requestText.value,
                filePath: "",
            });
            try {
                const response = await fetch(getRunApiUrl(), {
                    method: "POST",
                    headers: {
                        Accept: "application/json",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(runPayload),
                });

                let payload;
                try {
                    payload = await response.json();
                } catch (error) {
                    payload = {
                        ok: false,
                        error: "The server returned an unreadable response.",
                        command: "",
                    };
                }

                if (loadingCard) {
                    loadingCard.classList.add("isHidden");
                }

                if (payload.ok) {
                    const outputText = String(payload.output || "").trim();
                    if (!outputText) {
                        errorMeta.textContent = payload.command_summary ? `Ran: ${payload.command_summary}` : "";
                        errorOutput.textContent = "Miru AI finished the request, but no result text came back.";
                        revealPanel(errorCard);
                        return;
                    }

                    resultMeta.textContent = payload.command_summary ? `Ran: ${payload.command_summary}` : "";
                    resultReadable.textContent = outputText;
                    resultOutput.value = outputText;
                    hideManualCopyArea();
                    const modeConfig = modeMap[payload.mode] || modeConfigs[0] || {};
                    resultHint.textContent = modeConfig.result_hint || "Copy Result will try direct clipboard first, then a safe fallback if the browser blocks it.";
                    revealPanel(resultCard);
                    return;
                }

                errorMeta.textContent = payload.command_summary ? `Ran: ${payload.command_summary}` : "";
                errorOutput.textContent = payload.error || "Miru AI could not complete that request.";
                revealPanel(errorCard);
            } catch (error) {
                if (loadingCard) {
                    loadingCard.classList.add("isHidden");
                }
                errorMeta.textContent = "";
                errorOutput.textContent = "The Flask sidecar could not be reached. Check that the server is still running, then reload and try again.";
                revealPanel(errorCard);
            } finally {
                isRunning = false;
                runButton.disabled = Boolean(config.runDisabled);
                runButton.textContent = "Ask Miru";
                if (clearButton) {
                    clearButton.disabled = false;
                }
                if (pasteButton) {
                    pasteButton.disabled = false;
                }
            }
        }

        modeInputs.forEach((input) => input.addEventListener("change", updateModeUi));
        presetButtons.forEach((button) => button.addEventListener("click", () => applyPreset(button)));
        form.addEventListener("submit", submitForm);
        if (copyButton) {
            copyButton.addEventListener("click", copyResult);
        }
        if (selectButton) {
            selectButton.addEventListener("click", selectResult);
        }
        if (clearButton) {
            clearButton.addEventListener("click", clearAll);
        }
        if (pasteButton) {
            pasteButton.addEventListener("pointerdown", handlePasteTrigger);
            pasteButton.addEventListener("click", handlePasteTrigger);
        }

        hidePanels();
        updateModeUi();
    }

    initializePersistentNavigation();
    initializePageBehaviors();
})();

