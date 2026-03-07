// ===== Research Form Submission & SSE Progress =====

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('researchForm');
    if (form) {
        form.addEventListener('submit', handleResearchSubmit);
    }
});

async function handleResearchSubmit(e) {
    e.preventDefault();

    const question = document.getElementById('questionInput').value.trim();
    if (!question) return;

    const maxThreads = document.getElementById('maxThreads').value;
    const maxComments = document.getElementById('maxComments').value;
    const timeFilter = document.getElementById('timeFilter').value;

    const seedRaw = document.getElementById('seedThreadUrls')?.value || '';
    const seedUrls = seedRaw.split('\n').map(u => u.trim()).filter(u => u.length > 0);

    // Collect selected sources
    const sources = [];
    if (document.getElementById('sourceReddit')?.checked) sources.push('reddit');
    if (document.getElementById('sourceHN')?.checked) sources.push('hackernews');
    if (document.getElementById('sourceWeb')?.checked) sources.push('web');
    if (sources.length === 0) {
        alert('Please select at least one search source.');
        return;
    }

    // Hide search, show progress
    document.getElementById('searchSection').style.display = 'none';
    document.getElementById('progressSection').style.display = 'block';

    try {
        const response = await fetch('/api/research', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                question: question,
                max_threads: parseInt(maxThreads),
                max_comments_per_thread: parseInt(maxComments),
                time_filter: timeFilter,
                seed_urls: seedUrls,
                sources: sources,
            }),
        });

        if (!response.ok) {
            const data = await response.json();
            showError(data.error || 'Failed to start research');
            return;
        }

        const { research_id } = await response.json();
        listenToProgress(research_id);
    } catch (err) {
        showError('Network error: ' + err.message);
    }
}

// ===== Activity Feed Helpers =====

function appendFeedItem(feedEl, message, status) {
    // Mark previous active item as completed
    const prev = feedEl.querySelector('.feed-item.active');
    if (prev && status !== 'update') {
        prev.classList.remove('active');
        prev.classList.add('completed');
        prev.querySelector('.feed-icon').textContent = '\u2713';
    }
    if (status === 'update') {
        // Update the last item in-place instead of adding a new one
        const last = feedEl.querySelector('.feed-item:last-child');
        if (last) {
            last.querySelector('.feed-text').textContent = message;
            feedEl.scrollTop = feedEl.scrollHeight;
            return;
        }
    }
    const item = document.createElement('div');
    item.className = `feed-item ${status === 'completed' ? 'completed' : 'active'}`;
    const icon = status === 'completed' ? '\u2713' : '';
    item.innerHTML = `<span class="feed-icon">${icon}</span><span class="feed-text">${escapeHtml(message)}</span>`;
    feedEl.appendChild(item);
    feedEl.scrollTop = feedEl.scrollHeight;
}

function completeFeed(feedEl) {
    const active = feedEl.querySelector('.feed-item.active');
    if (active) {
        active.classList.remove('active');
        active.classList.add('completed');
        active.querySelector('.feed-icon').textContent = '\u2713';
    }
}

function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ===== Live Preview Table (index.html) =====

let livePreviewCount = 0;
let livePreviewRows = [];

function addLivePreviewComments(comments) {
    const preview = document.getElementById('livePreview');
    const tbody = document.getElementById('livePreviewBody');
    const counter = document.getElementById('livePreviewCount');
    if (!preview || !tbody) return;

    preview.style.display = 'block';

    for (const c of comments) {
        livePreviewCount++;
        if (c.relevancy_score === null || c.relevancy_score < 4) continue;

        const row = document.createElement('tr');
        row.className = 'live-preview-row';
        const scoreClass = c.relevancy_score >= 7 ? 'high' : 'mid';
        const body = c.body.length > 120 ? c.body.slice(0, 120) + '...' : c.body;
        row.innerHTML = `<td><span class="relevancy-badge ${scoreClass}">${c.relevancy_score}</span></td><td class="preview-body">${escapeHtml(body)}</td><td>${c.score}</td>`;

        // Insert sorted by relevancy desc
        let inserted = false;
        for (let i = 0; i < livePreviewRows.length; i++) {
            if (c.relevancy_score > livePreviewRows[i].score) {
                tbody.insertBefore(row, livePreviewRows[i].el);
                livePreviewRows.splice(i, 0, { score: c.relevancy_score, el: row });
                inserted = true;
                break;
            }
        }
        if (!inserted) {
            tbody.appendChild(row);
            livePreviewRows.push({ score: c.relevancy_score, el: row });
        }
    }
    if (counter) counter.textContent = livePreviewCount;
}

// ===== Main Research SSE =====

function listenToProgress(researchId) {
    const eventSource = new EventSource(`/api/research/${researchId}/stream`);
    const feedEl = document.getElementById('activityFeed');
    const progressBar = document.getElementById('progressBar');
    let lastStage = '';

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.stage === 'complete') {
            completeFeed(feedEl);
            appendFeedItem(feedEl, data.message, 'completed');
            eventSource.close();
            setTimeout(() => { window.location.href = `/results/${researchId}`; }, 500);
            return;
        }

        if (data.stage === 'error') {
            eventSource.close();
            showError(data.message);
            return;
        }

        if (progressBar) progressBar.style.width = data.progress + '%';

        // Determine if this is a new step or an update to the current one
        const isNewStage = data.stage !== lastStage;
        lastStage = data.stage;

        // Add feed item
        if (data.thread_comments !== undefined) {
            // Collection completion: update in place
            appendFeedItem(feedEl, data.message, 'update');
        } else {
            appendFeedItem(feedEl, data.message, isNewStage ? 'new' : 'new');
        }

        // Live preview: push scored comments during scoring phase
        if (data.comments && data.comments.length > 0) {
            addLivePreviewComments(data.comments);
        }
    };

    eventSource.onerror = () => {
        eventSource.close();
        showError('Lost connection to server. Please refresh and try again.');
    };
}

function showError(message) {
    document.getElementById('searchSection').style.display = 'none';
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('errorSection').style.display = 'block';
    document.getElementById('errorMessage').textContent = message;
}

// ===== Summarize Button =====

function toggleFeedbackPanel() {
    const panel = document.getElementById('feedbackPanel');
    panel.classList.toggle('visible');
    if (panel.classList.contains('visible')) {
        document.getElementById('feedbackInput').focus();
    }
}

async function handleSummarize(researchId, withFeedback = false) {
    const btn = document.getElementById('summarizeBtn');
    const feedbackToggle = document.getElementById('feedbackToggleBtn');
    const summarySection = document.getElementById('summarySection');
    const feedbackPanel = document.getElementById('feedbackPanel');

    let feedback = null;
    if (withFeedback) {
        feedback = document.getElementById('feedbackInput').value.trim();
        if (!feedback) {
            document.getElementById('feedbackInput').focus();
            return;
        }
    }

    feedbackPanel.classList.remove('visible');
    btn.disabled = true;
    feedbackToggle.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Summarizing...';

    summarySection.style.display = 'block';
    summarySection.innerHTML = '<div class="summary-loading"><span class="spinner spinner-dark"></span> Generating summary...</div>';

    try {
        const response = await fetch(`/api/research/${researchId}/summarize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(feedback ? { feedback } : {}),
        });

        if (!response.ok) {
            throw new Error('Failed to generate summary');
        }

        const { summary } = await response.json();
        renderSummary(summary);
        btn.textContent = 'Regenerate Summary';
        btn.disabled = false;
        feedbackToggle.disabled = false;
    } catch (err) {
        summarySection.innerHTML = `<p class="error-message">Error: ${err.message}</p>`;
        btn.textContent = 'Retry Summary';
        btn.disabled = false;
        feedbackToggle.disabled = false;
    }
}

function renderSummary(text) {
    const section = document.getElementById('summarySection');

    // Build a map of comment id -> permalink from the globally loaded comments
    const commentMap = {};
    if (typeof allComments !== 'undefined') {
        for (const c of allComments) {
            if (c.id && c.permalink) commentMap[c.id] = c.permalink;
        }
    }

    // Resolve [#id] citation markers into clickable links
    const resolved = text.replace(/\[#([a-zA-Z0-9_-]+)\]/g, (match, id) => {
        const permalink = commentMap[id];
        if (permalink) {
            return `<a href="${escapeHtmlAttr(permalink)}" target="_blank" class="citation-link" title="View source comment on Reddit">&#8599;</a>`;
        }
        return ''; // Drop unresolvable markers silently
    });

    // Process inline markdown: **bold** → <strong>
    function inlineMd(text) {
        return text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    }

    // Track whether we're inside a <ul> list
    let inList = false;
    const lines = resolved.split('\n').map(line => {
        const trimmed = line.trim();
        if (!trimmed) {
            if (inList) { inList = false; return '</ul>'; }
            return '';
        }
        if (trimmed.startsWith('#### ')) return `<h4>${inlineMd(trimmed.slice(5))}</h4>`;
        if (trimmed.startsWith('### ')) return `<h3>${inlineMd(trimmed.slice(4))}</h3>`;
        if (trimmed.startsWith('## ')) return `<h2>${inlineMd(trimmed.slice(3))}</h2>`;
        if (trimmed.startsWith('# ')) return `<h1>${inlineMd(trimmed.slice(2))}</h1>`;
        if (trimmed.startsWith('> ')) {
            const content = inlineMd(trimmed.slice(2));
            if (inList) { inList = false; return `</ul><blockquote>${content}</blockquote>`; }
            return `<blockquote>${content}</blockquote>`;
        }
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
            const item = `<li>${inlineMd(trimmed.slice(2))}</li>`;
            if (!inList) { inList = true; return `<ul>${item}`; }
            return item;
        }
        if (inList) { inList = false; return `</ul><p>${inlineMd(trimmed)}</p>`; }
        return `<p>${inlineMd(trimmed)}</p>`;
    });
    if (inList) lines.push('</ul>');

    section.innerHTML = `
        <h3>Summary</h3>
        <div class="summary-content">${lines.join('\n')}</div>
    `;
}

function escapeHtmlAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ===== Add Thread =====

async function handleAddThread(researchId) {
    const input = document.getElementById('addThreadUrl');
    const btn = document.getElementById('addThreadBtn');
    const progressEl = document.getElementById('addThreadProgress');
    const progressBar = document.getElementById('addThreadProgressBar');

    const url = input.value.trim();
    if (!url) return;

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner spinner-dark"></span>Processing...';
    progressEl.style.display = 'block';
    progressBar.style.width = '0%';

    try {
        const resp = await fetch(`/api/research/${researchId}/add-thread`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            progressEl.style.display = 'block';
            const feedEl = document.getElementById('addThreadFeed');
            if (feedEl) appendFeedItem(feedEl, data.error || 'Failed to add thread', 'completed');
            btn.innerHTML = 'Add Thread';
            btn.disabled = false;
            return;
        }

        if (data.already_exists) {
            btn.innerHTML = 'Add Thread';
            btn.disabled = false;
            progressBar.style.width = '100%';
            const feedEl = document.getElementById('addThreadFeed');
            if (feedEl) appendFeedItem(feedEl, data.message, 'completed');
            return;
        }

        // Listen to SSE stream for progress
        const feedEl = document.getElementById('addThreadFeed');
        if (feedEl) feedEl.innerHTML = '';
        const es = new EventSource(`/api/research/${researchId}/add-thread/stream`);
        es.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            progressBar.style.width = msg.progress + '%';

            if (feedEl) appendFeedItem(feedEl, msg.message, 'new');

            // Live insert scored comments into existing table
            if (msg.comments && msg.comments.length > 0 && typeof insertLiveComments === 'function') {
                insertLiveComments(msg.comments);
            }

            if (msg.stage === 'complete') {
                es.close();
                if (feedEl) completeFeed(feedEl);
                input.value = '';
                btn.innerHTML = 'Add Thread';
                btn.disabled = false;
                loadResults(researchId);
            } else if (msg.stage === 'error') {
                es.close();
                btn.innerHTML = 'Add Thread';
                btn.disabled = false;
            }
        };
        es.onerror = () => {
            es.close();
            btn.innerHTML = 'Add Thread';
            btn.disabled = false;
        };
    } catch (err) {
        progressEl.style.display = 'none';
        btn.innerHTML = 'Add Thread';
        btn.disabled = false;
    }
}

// ===== Find More Comments & Articles =====

// Per-source config state for the find-more configure panel
let expandSourceConfig = { reddit: true, hackernews: true, web: true };

async function checkExpandStatus(researchId) {
    try {
        const resp = await fetch(`/api/research/${researchId}/expand/status`);
        const data = await resp.json();
        const btn = document.getElementById('findMoreBtn');
        if (!btn) return;

        // Initialize configure checkboxes from status
        const researchSources = data.research_sources || ['reddit', 'hackernews', 'web'];
        _initExpandConfig(researchSources, data);

        if (!data.can_expand) {
            btn.disabled = true;
            btn.title = 'All search strategies have been tried for this query';
            const cfgBtn = document.getElementById('findMoreConfigBtn');
            if (cfgBtn) cfgBtn.disabled = true;
        }
    } catch (_) {}
}

function _initExpandConfig(researchSources, statusData) {
    _configureCheckbox('fmReddit', 'fmRedditLabel', 'reddit', researchSources, statusData.reddit_exhausted);
    _configureCheckbox('fmHN', 'fmHNLabel', 'hackernews', researchSources, statusData.hn_exhausted);
    _configureCheckbox('fmWeb', 'fmWebLabel', 'web', researchSources, statusData.web_exhausted);
}

function _configureCheckbox(cbId, labelId, source, researchSources, exhausted) {
    const cb = document.getElementById(cbId);
    const label = document.getElementById(labelId);
    if (!cb || !label) return;
    const enabled = researchSources.includes(source);
    const available = enabled && !exhausted;
    cb.checked = available;
    cb.disabled = !available;
    label.style.opacity = available ? '1' : '0.45';
    expandSourceConfig[source] = available;
    // Remove old listeners by cloning, then re-add
    const newCb = cb.cloneNode(true);
    cb.parentNode.replaceChild(newCb, cb);
    newCb.addEventListener('change', () => { expandSourceConfig[source] = newCb.checked; });
}

function toggleFindMoreConfig(event) {
    event.stopPropagation();
    const panel = document.getElementById('findMoreConfig');
    if (!panel) return;
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

// ===== Archive Research =====

let _pendingArchiveId = null;

function showArchiveConfirm(researchId, event) {
    event.preventDefault();
    event.stopPropagation();
    _pendingArchiveId = researchId;
    const popover = document.getElementById('archiveConfirm');
    const btn = event.currentTarget;
    const rect = btn.getBoundingClientRect();

    // Position the popover to the left of the button
    popover.style.display = 'block';
    popover.style.top = rect.top + 'px';
    popover.style.left = (rect.left - popover.offsetWidth - 8) + 'px';

    // Wire up the confirm button
    document.getElementById('archiveConfirmYes').onclick = async () => {
        hideArchiveConfirm();
        try {
            const resp = await fetch(`/api/research/${researchId}/archive`, { method: 'POST' });
            if (!resp.ok) throw new Error('Failed to archive');
            if (window.location.pathname === `/results/${researchId}`) {
                window.location.href = '/';
            } else {
                refreshSidebar();
            }
        } catch (err) {
            alert('Failed to archive: ' + err.message);
        }
    };
}

function hideArchiveConfirm() {
    document.getElementById('archiveConfirm').style.display = 'none';
    _pendingArchiveId = null;
}

// Close popovers when clicking elsewhere
document.addEventListener('click', (e) => {
    const popover = document.getElementById('archiveConfirm');
    if (popover && popover.style.display === 'block' && !popover.contains(e.target)) {
        hideArchiveConfirm();
    }
    const findMoreConfig = document.getElementById('findMoreConfig');
    if (findMoreConfig && findMoreConfig.style.display === 'block'
            && !findMoreConfig.contains(e.target)
            && e.target.id !== 'findMoreConfigBtn') {
        findMoreConfig.style.display = 'none';
    }
});

async function refreshSidebar() {
    try {
        const resp = await fetch('/api/history');
        const data = await resp.json();
        const list = document.getElementById('historyList');
        if (!list) return;
        if (data.history.length === 0) {
            list.innerHTML = '<li class="history-empty">No research history yet</li>';
            return;
        }
        list.innerHTML = data.history.map(item => {
            const isActive = window.location.pathname === `/results/${item.id}`;
            const question = item.question.length > 60 ? item.question.slice(0, 60) + '...' : item.question;
            return `<li class="history-item ${isActive ? 'active' : ''}">
                <a href="/results/${item.id}" class="history-link">
                    <span class="history-question">${escapeHtmlAttr(question)}</span>
                    <span class="history-meta">${item.num_comments || 0} comments &middot; ${item.created_at.slice(0, 10)}</span>
                </a>
                <button class="btn-archive-sidebar" onclick="showArchiveConfirm('${item.id}', event)" title="Archive">&times;</button>
            </li>`;
        }).join('');
    } catch (_) {}
}

async function openArchivedPopup() {
    document.getElementById('archivedOverlay').style.display = 'flex';
    const listEl = document.getElementById('archivedList');
    listEl.innerHTML = '<p style="color:#7c7c7c;">Loading...</p>';
    try {
        const resp = await fetch('/api/archived');
        const data = await resp.json();
        if (data.archived.length === 0) {
            listEl.innerHTML = '<p style="color:#7c7c7c;">No archived research.</p>';
            return;
        }
        listEl.innerHTML = data.archived.map(item => {
            const question = item.question.length > 60 ? item.question.slice(0, 60) + '...' : item.question;
            return `<div class="archived-item">
                <div class="archived-item-info">
                    <span class="archived-item-question">${escapeHtmlAttr(question)}</span>
                    <span class="archived-item-meta">${item.num_comments || 0} comments &middot; ${item.created_at.slice(0, 10)}</span>
                </div>
                <div class="archived-item-actions">
                    <button class="btn btn-outline btn-sm" onclick="restoreResearch('${item.id}')">Restore</button>
                    <button class="btn btn-danger btn-sm" onclick="permanentlyDelete('${item.id}')">Delete</button>
                </div>
            </div>`;
        }).join('');
    } catch (err) {
        listEl.innerHTML = `<p class="error-message">Failed to load: ${err.message}</p>`;
    }
}

function closeArchivedPopup() {
    document.getElementById('archivedOverlay').style.display = 'none';
}

async function restoreResearch(researchId) {
    if (!confirm('Restore this research to the sidebar?')) return;
    try {
        await fetch(`/api/research/${researchId}/unarchive`, { method: 'POST' });
        openArchivedPopup();
        refreshSidebar();
    } catch (err) {
        alert('Failed to restore: ' + err.message);
    }
}

async function permanentlyDelete(researchId) {
    if (!confirm('Permanently delete this research? This cannot be undone. (CSV export files will be preserved.)')) return;
    try {
        await fetch(`/api/research/${researchId}/delete`, { method: 'DELETE' });
        openArchivedPopup();
    } catch (err) {
        alert('Failed to delete: ' + err.message);
    }
}

async function handleFindMore(researchId) {
    // Close configure panel if open
    const configPanel = document.getElementById('findMoreConfig');
    if (configPanel) configPanel.style.display = 'none';

    const btn = document.getElementById('findMoreBtn');
    const progressEl = document.getElementById('expandProgress');
    const progressBar = document.getElementById('expandProgressBar');

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner spinner-dark"></span>Finding more...';
    progressEl.style.display = 'block';
    progressBar.style.width = '0%';

    const selectedSources = Object.keys(expandSourceConfig).filter(k => expandSourceConfig[k]);

    try {
        const resp = await fetch(`/api/research/${researchId}/expand`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ sources: selectedSources }),
        });
        if (!resp.ok) {
            const data = await resp.json();
            progressEl.style.display = 'none';
            btn.innerHTML = 'Find More Comments &amp; Articles';
            btn.disabled = true;
            btn.title = data.error || 'No more search strategies available';
            return;
        }

        // Listen to SSE stream for progress
        const feedEl = document.getElementById('expandFeed');
        if (feedEl) feedEl.innerHTML = '';
        const es = new EventSource(`/api/research/${researchId}/expand/stream`);
        es.onmessage = (event) => {
            const data = JSON.parse(event.data);

            progressBar.style.width = data.progress + '%';

            if (feedEl) appendFeedItem(feedEl, data.message, 'new');

            // Live insert scored comments into existing table
            if (data.comments && data.comments.length > 0 && typeof insertLiveComments === 'function') {
                insertLiveComments(data.comments);
            }

            if (data.stage === 'complete') {
                es.close();
                if (feedEl) completeFeed(feedEl);
                progressEl.style.display = 'none';
                btn.innerHTML = 'Find More Comments &amp; Articles';
                // Reload tables with new data
                loadResults(researchId);
                // Check if more expansions are still possible
                checkExpandStatus(researchId);
            } else if (data.stage === 'error') {
                es.close();
                progressEl.style.display = 'none';
                btn.innerHTML = 'Find More Comments &amp; Articles';
                btn.disabled = false;
            }
        };
        es.onerror = () => {
            es.close();
            progressEl.style.display = 'none';
            btn.innerHTML = 'Find More Comments &amp; Articles';
            btn.disabled = false;
        };
    } catch (err) {
        progressEl.style.display = 'none';
        btn.innerHTML = 'Find More Comments &amp; Articles';
        btn.disabled = false;
    }
}
