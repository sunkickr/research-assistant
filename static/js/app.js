// ===== Sidebar Toggle =====

function toggleSidebar() {
    const layout = document.querySelector('.layout');
    if (!layout) return;
    layout.classList.toggle('sidebar-collapsed');
    localStorage.setItem('sidebarCollapsed', layout.classList.contains('sidebar-collapsed'));
}

// Restore sidebar state on page load
(function() {
    if (localStorage.getItem('sidebarCollapsed') === 'true') {
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelector('.layout')?.classList.add('sidebar-collapsed');
        });
    }
})();

// ===== Research Mode Toggle =====

function setResearchMode(mode) {
    const generalPanel = document.getElementById('generalPanel');
    const productPanel = document.getElementById('productPanel');
    const modeGeneral = document.getElementById('modeGeneral');
    const modeProduct = document.getElementById('modeProduct');
    if (!generalPanel || !productPanel) return;

    if (mode === 'product') {
        generalPanel.style.display = 'none';
        productPanel.style.display = 'block';
        modeGeneral?.classList.remove('active');
        modeProduct?.classList.add('active');
    } else {
        generalPanel.style.display = 'block';
        productPanel.style.display = 'none';
        modeGeneral?.classList.add('active');
        modeProduct?.classList.remove('active');
    }
}

// ===== Research Form Submission & SSE Progress =====

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('researchForm');
    if (form) {
        form.addEventListener('submit', handleResearchSubmit);
    }
    const productForm = document.getElementById('productResearchForm');
    if (productForm) {
        productForm.addEventListener('submit', handleProductResearchSubmit);
    }
    // Load model names for the toggle
    const modelToggle = document.getElementById('modelToggleText');
    if (modelToggle) {
        fetch('/api/models').then(r => r.json()).then(data => {
            modelToggle.textContent = `Use ${data.alt_model}`;
            const sectionToggle = document.getElementById('sectionModelToggleText');
            if (sectionToggle) sectionToggle.textContent = `Use ${data.alt_model}`;
        }).catch(() => {});
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
    document.getElementById('generalPanel').style.display = 'none';
    document.getElementById('productPanel').style.display = 'none';
    const modeToggleEl = document.querySelector('.research-mode-toggle');
    if (modeToggleEl) modeToggleEl.style.display = 'none';
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

async function handleProductResearchSubmit(e) {
    e.preventDefault();

    const productName = document.getElementById('productNameInput').value.trim();
    if (!productName) return;

    const maxThreads = document.getElementById('productMaxThreads')?.value || '15';
    const maxComments = document.getElementById('productMaxComments')?.value || '100';
    const timeFilter = document.getElementById('productTimeFilter')?.value || 'all';

    const sources = [];
    if (document.getElementById('productSourceReddit')?.checked) sources.push('reddit');
    if (document.getElementById('productSourceHN')?.checked) sources.push('hackernews');
    if (document.getElementById('productSourceWeb')?.checked) sources.push('web');
    if (document.getElementById('productSourceReviews')?.checked) sources.push('reviews');
    if (document.getElementById('productSourcePH')?.checked) sources.push('producthunt');
    if (sources.length === 0) {
        alert('Please select at least one search source.');
        return;
    }

    // Hide form panels, show progress
    document.getElementById('generalPanel').style.display = 'none';
    document.getElementById('productPanel').style.display = 'none';
    const modeToggle = document.querySelector('.research-mode-toggle');
    if (modeToggle) modeToggle.style.display = 'none';
    document.getElementById('progressSection').style.display = 'block';

    try {
        const response = await fetch('/api/product-research', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                product_name: productName,
                max_threads: parseInt(maxThreads),
                max_comments_per_thread: parseInt(maxComments),
                time_filter: timeFilter,
                sources: sources,
            }),
        });

        if (!response.ok) {
            const data = await response.json();
            showError(data.error || 'Failed to start product research');
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
    let redirectedForScoring = false;

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

        // Redirect to results page when scoring starts (comments already saved raw)
        if (data.stage === 'scoring' && !redirectedForScoring) {
            redirectedForScoring = true;
            eventSource.close();
            window.location.href = `/results/${researchId}`;
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
        feedback = document.getElementById('feedbackInput').value.trim() || null;
    }

    const maxComments = withFeedback
        ? parseInt(document.getElementById('commentCountInput')?.value || '50', 10)
        : 50;

    const useAltModel = document.getElementById('useAltModel')?.checked || false;

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
            body: JSON.stringify({
                ...(feedback ? { feedback } : {}),
                max_comments: maxComments,
                use_alt_model: useAltModel,
            }),
        });

        if (!response.ok) {
            throw new Error('Failed to generate summary');
        }

        const { summary } = await response.json();
        renderSummary(summary);
        btn.textContent = 'Regenerate Summary';
        btn.disabled = false;
        feedbackToggle.disabled = false;
        const pubBtn = document.getElementById('publishBtn');
        if (pubBtn) pubBtn.disabled = false;
    } catch (err) {
        summarySection.innerHTML = `<p class="error-message">Error: ${err.message}</p>`;
        btn.textContent = 'Retry Summary';
        btn.disabled = false;
        feedbackToggle.disabled = false;
    }
}

function renderSummary(text) {
    const section = document.getElementById('summarySection');

    // Build a map of comment id -> comment object from the globally loaded comments
    const commentMap = {};
    if (typeof allComments !== 'undefined') {
        for (const c of allComments) {
            if (c.id) commentMap[c.id] = c;
        }
    }

    // Assign sequential numbers to citations in order of first appearance
    const citationOrder = []; // ids in appearance order
    const citationIndex = {}; // id -> 1-based number
    text.replace(/\[#([a-zA-Z0-9_-]+)\]/g, (match, id) => {
        if (commentMap[id] && !(id in citationIndex)) {
            citationIndex[id] = citationOrder.push(id); // push returns new length = number
        }
    });

    // Replace [#id] markers with numbered superscript reference links
    const resolved = text.replace(/\[#([a-zA-Z0-9_-]+)\]/g, (match, id) => {
        const num = citationIndex[id];
        if (!num) return '';
        const comment = commentMap[id];
        const href = comment && comment.permalink ? escapeHtmlAttr(comment.permalink) : '#';
        return `<a href="${href}" target="_blank" class="ref-link" title="View source">[${num}]</a>`;
    });

    // Process inline markdown: **bold** → <strong>, [text](url) → <a>
    function inlineMd(text) {
        text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        return text;
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

    // Append numbered sources section for all cited comments
    if (citationOrder.length > 0) {
        const itemsHtml = citationOrder.map((id, i) => {
            const comment = commentMap[id];
            if (!comment) return '';
            const num = i + 1;
            const snippet = comment.body.length > 150 ? comment.body.slice(0, 150).trimEnd() + '\u2026' : comment.body;
            const sourceLabel = comment.source === 'hackernews' ? 'HN' : comment.source === 'web' ? 'Web' : 'Reddit';
            const authorLabel = (!comment.source || comment.source === 'reddit') ? `u/${escapeHtmlAttr(comment.author)}` : escapeHtmlAttr(comment.author);
            const linkHtml = comment.permalink
                ? ` <a href="${escapeHtmlAttr(comment.permalink)}" target="_blank" class="source-ext-link" title="View original">&#8599;</a>`
                : '';
            return `<li class="summary-source-item"><span class="source-num">[${num}]</span><span class="source-tag source-tag-${comment.source || 'reddit'}">${sourceLabel}</span><strong class="source-author">${authorLabel}</strong> &mdash; <span class="source-snippet">&ldquo;${escapeHtmlAttr(snippet)}&rdquo;</span>${linkHtml}</li>`;
        }).filter(Boolean).join('');

        if (itemsHtml) {
            const sourcesEl = document.createElement('div');
            sourcesEl.className = 'summary-sources-section';
            sourcesEl.innerHTML = `<h2 class="summary-sources-heading">Sources</h2><ol class="summary-source-list">${itemsHtml}</ol>`;
            section.appendChild(sourcesEl);
        }
    }
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

// ===== Publish Research =====

async function handlePublishResearch(researchId) {
    const btn = document.getElementById('publishBtn');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = 'Publishing...';
    try {
        const resp = await fetch(`/api/research/${researchId}/publish`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
            alert(data.error || 'Failed to publish');
            btn.disabled = false;
            btn.textContent = 'Publish Research';
            return;
        }
        btn.outerHTML = `<a href="/published/${data.filename}" target="_blank" class="btn btn-primary">View Published</a>`;
    } catch (e) {
        alert('Failed to publish: ' + e.message);
        btn.disabled = false;
        btn.textContent = 'Publish Research';
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
        } else {
            btn.disabled = false;
            btn.title = '';
            const cfgBtn = document.getElementById('findMoreConfigBtn');
            if (cfgBtn) cfgBtn.disabled = false;
        }
    } catch (_) {}
}

function _initExpandConfig(researchSources, statusData) {
    _configureCheckbox('fmReddit', 'fmRedditLabel', 'reddit', researchSources, statusData.reddit_exhausted);
    _configureCheckbox('fmHN', 'fmHNLabel', 'hackernews', researchSources, statusData.hn_exhausted);
    _configureCheckbox('fmWeb', 'fmWebLabel', 'web', researchSources, statusData.web_exhausted);
    // Reviews checkbox only exists on product results page
    _configureCheckbox('fmReviews', 'fmReviewsLabel', 'reviews', researchSources, statusData.reviews_exhausted);
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

        // Track which sorts this click started so we can detect pipeline completion
        const respData = await resp.json();
        const sortsStarted = respData.sorts_used || [];

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
                btn.innerHTML = 'Find More Comments &amp; Articles';
                btn.disabled = false;
                checkExpandStatus(researchId);
                if (data.found_nothing) {
                    // Show a distinct warning entry and keep the feed visible briefly
                    if (feedEl) {
                        const item = document.createElement('div');
                        item.className = 'feed-item feed-nothing';
                        item.innerHTML = `<span class="feed-icon">!</span><span class="feed-text">${escapeHtml(data.message)} Try different sources or come back later.</span>`;
                        feedEl.appendChild(item);
                        feedEl.scrollTop = feedEl.scrollHeight;
                    }
                    setTimeout(() => { progressEl.style.display = 'none'; }, 4000);
                } else {
                    if (feedEl) completeFeed(feedEl);
                    progressEl.style.display = 'none';
                    loadResults(researchId);
                }
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
            // SSE dropped mid-pipeline — poll until the pipeline finishes saving,
            // then reload. Don't call loadResults immediately or it wipes live comments.
            _pollUntilExpandDone(researchId, sortsStarted, 0);
        };
    } catch (err) {
        progressEl.style.display = 'none';
        btn.innerHTML = 'Find More Comments &amp; Articles';
        btn.disabled = false;
    }
}

// Poll expand status until sortsStarted all appear in sorts_tried (pipeline done),
// then reload results. Gives up after ~3 minutes (36 attempts × 5s).
async function _pollUntilExpandDone(researchId, sortsStarted, attempts) {
    if (attempts >= 36 || sortsStarted.length === 0) return;
    await new Promise(r => setTimeout(r, 5000));
    try {
        const resp = await fetch(`/api/research/${researchId}/expand/status`);
        const data = await resp.json();
        const sortsTried = data.sorts_tried || [];
        const allDone = sortsStarted.every(s => sortsTried.includes(s));
        if (allDone) {
            loadResults(researchId);
            checkExpandStatus(researchId);
        } else {
            _pollUntilExpandDone(researchId, sortsStarted, attempts + 1);
        }
    } catch (_) {
        _pollUntilExpandDone(researchId, sortsStarted, attempts + 1);
    }
}

// ===== Initial Scoring Progress (results page, arrived mid-scoring) =====

function listenToScoringProgress(researchId) {
    const progressEl = document.getElementById('initialScoringProgress');
    const barEl = document.getElementById('initialScoringBar');
    const feedEl = document.getElementById('initialScoringFeed');

    const es = new EventSource(`/api/research/${researchId}/stream`);
    if (progressEl) progressEl.style.display = 'block';

    es.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (barEl && data.progress != null) {
            // Remap: pipeline scoring goes 62→95, normalize to 0→100
            const scoringPct = Math.min(100, Math.max(0,
                Math.round(((data.progress - 62) / 33) * 100)
            ));
            barEl.style.width = scoringPct + '%';
        }

        if (feedEl) {
            feedEl.innerHTML = `<div>${escapeHtml(data.message || '')}</div>`;
        }

        if (data.comments && data.comments.length > 0 && typeof insertLiveComments === 'function') {
            insertLiveComments(data.comments);
        }

        if (data.stage === 'complete') {
            es.close();
            if (progressEl) progressEl.style.display = 'none';
            loadResults(researchId);
            refreshResultsMeta(researchId);
            checkUnscoredComments(researchId);
            document.getElementById('summarizeBtn')?.removeAttribute('disabled');
            refreshSidebar();
        } else if (data.stage === 'error') {
            es.close();
            if (progressEl) progressEl.style.display = 'none';
            checkUnscoredComments(researchId);
        }
    };

    es.onerror = () => {
        es.close();
        if (progressEl) progressEl.style.display = 'none';
        // Pipeline may have finished while connecting — fall back to checking unscored
        checkUnscoredComments(researchId);
    };
}

async function refreshResultsMeta(researchId) {
    try {
        const resp = await fetch(`/api/research/${researchId}`);
        const data = await resp.json();
        const metaEls = document.querySelectorAll('.results-meta');
        if (metaEls.length > 0 && data.research) {
            const r = data.research;
            metaEls[0].innerHTML =
                `${r.num_threads || 0} threads &middot; ` +
                `${r.num_comments || 0} comments scored &middot; ` +
                `${(r.created_at || '').slice(0, 10)}`;
        }
    } catch (_) {}
}

// ===== Rescore Unscored Comments =====

async function checkUnscoredComments(researchId) {
    try {
        const resp = await fetch(`/api/research/${researchId}/unscored-count`);
        const data = await resp.json();
        const btn = document.getElementById('rescoreBtn');
        if (data.unscored_count > 0 && btn) {
            document.getElementById('unscoredCount').textContent = data.unscored_count;
            btn.style.display = '';
        }
    } catch (e) { /* ignore */ }
}

async function handleRescore(researchId) {
    const btn = document.getElementById('rescoreBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Scoring...';

    const progressEl = document.getElementById('addThreadProgress');
    const barEl = document.getElementById('addThreadProgressBar');
    const feedEl = document.getElementById('addThreadFeed');
    progressEl.style.display = 'block';

    try {
        const resp = await fetch(`/api/research/${researchId}/rescore`, { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.error || 'Failed to start rescore');
            btn.disabled = false;
            btn.innerHTML = `Score Unscored Comments (<span id="unscoredCount">${document.getElementById('unscoredCount')?.textContent || 0}</span>)`;
            progressEl.style.display = 'none';
            return;
        }

        const evtSource = new EventSource(`/api/research/${researchId}/rescore/stream`);
        evtSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            if (barEl) barEl.style.width = (data.progress || 0) + '%';
            if (feedEl) feedEl.innerHTML = `<div>${data.message || ''}</div>`;

            if (data.comments) insertLiveComments(data.comments);

            if (data.stage === 'complete' || data.stage === 'error') {
                evtSource.close();
                progressEl.style.display = 'none';
                btn.style.display = 'none';
                loadResults(researchId);
            }
        };
    } catch (e) {
        alert('Failed to rescore: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = 'Score Unscored Comments';
        progressEl.style.display = 'none';
    }
}


// ===== Product Research Summaries =====

function toggleProductFeedbackPanel() {
    const panel = document.getElementById('productFeedbackPanel');
    if (panel) panel.classList.toggle('visible');
}

async function handleGenerateProductSummaries(researchId, withCustom = false) {
    const btn = document.getElementById('generateSummariesBtn');
    const feedbackToggle = document.getElementById('productFeedbackToggleBtn');
    const feedbackPanel = document.getElementById('productFeedbackPanel');
    if (!btn) return;

    const maxComments = withCustom
        ? parseInt(document.getElementById('productCommentCountInput')?.value || '50', 10)
        : 50;

    let feedback = null;
    if (withCustom) {
        feedback = document.getElementById('productFeedbackInput')?.value.trim() || null;
    }

    const useAltModel = document.getElementById('useAltModel')?.checked || false;

    if (feedbackPanel) feedbackPanel.classList.remove('visible');
    btn.disabled = true;
    if (feedbackToggle) feedbackToggle.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Generating summaries...';

    try {
        const resp = await fetch(`/api/research/${researchId}/summarize-product`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                max_comments: maxComments,
                ...(feedback ? { feedback } : {}),
                use_alt_model: useAltModel,
            }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.error || 'Failed to generate summaries');
            return;
        }
        const data = await resp.json();
        renderProductSummaries(data.summaries);
        btn.textContent = 'Regenerate Summaries';
        const pubBtn = document.getElementById('publishBtn');
        if (pubBtn) pubBtn.disabled = false;
    } catch (e) {
        alert('Failed to generate summaries: ' + e.message);
    } finally {
        btn.disabled = false;
        if (feedbackToggle) feedbackToggle.disabled = false;
    }
}

const SECTION_LABELS = {
    general: 'General Information', issues: 'Top Issues',
    feature_requests: 'Feature Requests', benefits: 'Benefits & Strengths',
    competitors: 'Competitors', alternatives: 'Churn & Alternatives',
};

function toggleSectionVisibility(btn) {
    const card = btn.closest('.summary-card');
    if (!card) return;
    const body = card.querySelector('.summary-card-body');
    if (!body) return;
    const isHidden = body.style.display === 'none';
    body.style.display = isHidden ? '' : 'none';
    btn.innerHTML = isHidden ? '\u25B2' : '\u25BC';
    btn.title = isHidden ? 'Hide this section' : 'Show this section';
}

function openSectionFeedback(researchId, category) {
    const modal = document.getElementById('sectionFeedbackModal');
    const title = document.getElementById('sectionFeedbackTitle');
    const input = document.getElementById('sectionFeedbackInput');
    const submitBtn = document.getElementById('sectionFeedbackSubmit');
    if (!modal) return;

    title.textContent = `Regenerate: ${SECTION_LABELS[category] || category}`;
    input.value = '';
    modal.classList.add('visible');
    input.focus();

    submitBtn.onclick = () => handleRegenerateSection(researchId, category);
}

function closeSectionFeedback() {
    const modal = document.getElementById('sectionFeedbackModal');
    if (modal) modal.classList.remove('visible');
}

async function handleRegenerateSection(researchId, category) {
    const card = document.querySelector(`.summary-card[data-category="${category}"]`);
    const body = document.getElementById(`summary-${category}`);
    const btn = card?.querySelector('.btn-card-regenerate');
    if (!body || !btn) return;

    const feedback = document.getElementById('sectionFeedbackInput')?.value.trim() || null;
    const useAltModel = document.getElementById('sectionUseAltModel')?.checked || false;
    const maxComments = parseInt(document.getElementById('sectionMaxComments')?.value, 10) || 50;
    closeSectionFeedback();

    btn.disabled = true;
    const previousHtml = body.innerHTML;
    body.innerHTML = '<div class="summary-loading"><span class="spinner spinner-dark"></span> Regenerating...</div>';

    try {
        const resp = await fetch(`/api/research/${researchId}/summarize-product-section`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                category,
                ...(feedback ? { feedback } : {}),
                use_alt_model: useAltModel,
                max_comments: maxComments,
            }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.error || 'Failed to regenerate section');
            body.innerHTML = previousHtml;
            return;
        }
        const data = await resp.json();
        const { html, citationOrder } = renderProductSectionHtml(data.summary);
        body.innerHTML = html + buildProductSourcesHtml(citationOrder);
    } catch (e) {
        alert('Failed to regenerate section: ' + e.message);
        body.innerHTML = previousHtml;
    } finally {
        btn.disabled = false;
    }
}

function renderProductSummaries(summaries) {
    const grid = document.getElementById('productSummaries');
    if (!grid) return;
    grid.style.display = 'grid';

    for (const [category, text] of Object.entries(summaries)) {
        const el = document.getElementById(`summary-${category}`);
        if (el) {
            const { html, citationOrder } = renderProductSectionHtml(text);
            el.innerHTML = html + buildProductSourcesHtml(citationOrder);
        }
    }
}

function buildProductSourcesHtml(citationOrder) {
    if (!citationOrder || citationOrder.length === 0) return '';
    const commentMap = {};
    if (typeof allComments !== 'undefined' && allComments) {
        allComments.forEach(c => { commentMap[c.id] = c; });
    }
    const SOURCE_LABELS = { hackernews: 'HN', web: 'Web', reviews: 'Reviews', producthunt: 'PH' };
    const items = citationOrder.map((id, i) => {
        const c = commentMap[id];
        if (!c) return '';
        const num = i + 1;
        const snippet = c.body.length > 120 ? c.body.slice(0, 120).trimEnd() + '\u2026' : c.body;
        const srcLabel = SOURCE_LABELS[c.source] || 'Reddit';
        const author = (!c.source || c.source === 'reddit') ? `u/${escapeHtmlAttr(c.author)}` : escapeHtmlAttr(c.author);
        const link = c.permalink ? ` <a href="${escapeHtmlAttr(c.permalink)}" target="_blank" class="source-ext-link">&#8599;</a>` : '';
        return `<li class="summary-source-item"><span class="source-num">[${num}]</span><span class="source-tag source-tag-${c.source || 'reddit'}">${srcLabel}</span><strong class="source-author">${author}</strong> &mdash; <span class="source-snippet">&ldquo;${escapeHtmlAttr(snippet)}&rdquo;</span>${link}</li>`;
    }).filter(Boolean).join('');
    if (!items) return '';
    return `<div class="summary-sources-section product-card-sources"><h4 class="summary-sources-heading">Sources</h4><ol class="summary-source-list">${items}</ol></div>`;
}

function renderProductSectionHtml(text) {
    if (!text) return { html: '<p class="summary-empty">No data available.</p>', citationOrder: [] };

    // Build citation map
    const commentMap = {};
    if (typeof allComments !== 'undefined' && allComments) {
        allComments.forEach(c => { commentMap[c.id] = c; });
    }
    const citationOrder = [];
    const citationIndex = {};

    // Pre-scan to assign citation numbers in appearance order
    text.replace(/\[#([^\]]+)\]/g, (match, id) => {
        if (commentMap[id] && !(id in citationIndex)) {
            citationIndex[id] = citationOrder.push(id);
        }
    });

    function inlineMd(line) {
        // Bold
        line = line.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Resolve citation markers [#id] to numbered links
        line = line.replace(/\[#([^\]]+)\]/g, (match, id) => {
            const num = citationIndex[id];
            if (!num) return '';
            const c = commentMap[id];
            const link = c && c.permalink ? c.permalink : '#';
            return `<a href="${link}" target="_blank" class="citation-link" title="${escapeHtml(c.body?.substring(0, 100) || '')}">[${num}]</a>`;
        });
        // Markdown links [text](url) — run after citations to avoid conflicts
        line = line.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        return line;
    }

    // Line-by-line processing with list state tracking
    // Blank lines do NOT close lists — only non-list content does.
    let inOl = false;
    let inUl = false;
    let inLi = false;

    function closeLi() {
        let s = '';
        if (inLi) { inLi = false; s += '</li>'; }
        return s;
    }

    function closeList() {
        let close = closeLi();
        if (inOl) { inOl = false; close += '</ol>'; }
        if (inUl) { inUl = false; close += '</ul>'; }
        return close;
    }

    const lines = text.split('\n').map(line => {
        const trimmed = line.trim();
        // Blank lines: skip without closing lists (LLM puts blanks between numbered items)
        if (!trimmed) return '';
        // Headers
        if (trimmed.startsWith('### ')) {
            return closeList() + `<h5>${inlineMd(trimmed.slice(4))}</h5>`;
        }
        if (trimmed.startsWith('## ')) {
            return closeList() + `<h4>${inlineMd(trimmed.slice(3))}</h4>`;
        }
        // Blockquotes — keep inside list item if in a list
        if (trimmed.startsWith('> ')) {
            if (inOl || inUl) {
                return `<blockquote>${inlineMd(trimmed.slice(2))}</blockquote>`;
            }
            return `<blockquote>${inlineMd(trimmed.slice(2))}</blockquote>`;
        }
        // Numbered list items
        const olMatch = trimmed.match(/^\d+\.\s+(.+)$/);
        if (olMatch) {
            let close = '';
            if (inUl) { close += closeList(); }
            close += closeLi();
            if (!inOl) { inOl = true; close += '<ol>'; }
            inLi = true;
            return close + `<li>${inlineMd(olMatch[1])}`;
        }
        // Bullet list items
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
            let close = '';
            if (inOl) { close += closeList(); }
            close += closeLi();
            if (!inUl) { inUl = true; close += '<ul>'; }
            inLi = true;
            return close + `<li>${inlineMd(trimmed.slice(2))}`;
        }
        // Regular paragraph — keep inside list item if in a list
        if (inOl || inUl) {
            return `<p>${inlineMd(trimmed)}</p>`;
        }
        return closeList() + `<p>${inlineMd(trimmed)}</p>`;
    });
    // Set inLi for all new list items
    // Close any list still open at the end
    const trailing = closeList();
    if (trailing) lines.push(trailing);

    return { html: lines.join('\n'), citationOrder };
}

// ===== Category Filter Tabs =====

const CATEGORY_LABELS = {
    'issues': 'Issues',
    'feature_requests': 'Feature Requests',
    'general': 'General',
    'competitors': 'Competitors',
    'benefits': 'Benefits',
    'alternatives': 'Alternatives',
};

function renderCategoryTabs() {
    const container = document.getElementById('categoryTabs');
    if (!container) return;

    // Only show tabs if we have category data
    const categories = [...new Set(
        (typeof allComments !== 'undefined' ? allComments : [])
            .map(c => c.category)
            .filter(Boolean)
    )];

    if (categories.length === 0) {
        container.innerHTML = '';
        return;
    }

    let html = `<button class="cat-tab ${typeof activeCategoryFilter === 'undefined' || activeCategoryFilter === 'all' ? 'active' : ''}" onclick="setCategoryFilter('all')">All</button>`;
    for (const cat of ['general', 'issues', 'feature_requests', 'benefits', 'competitors', 'alternatives']) {
        if (!categories.includes(cat)) continue;
        const count = allComments.filter(c => c.category === cat).length;
        const label = CATEGORY_LABELS[cat] || cat;
        const active = (typeof activeCategoryFilter !== 'undefined' && activeCategoryFilter === cat) ? 'active' : '';
        html += `<button class="cat-tab ${active}" onclick="setCategoryFilter('${cat}')">${label} (${count})</button>`;
    }
    container.innerHTML = html;
}

