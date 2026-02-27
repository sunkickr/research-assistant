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

function listenToProgress(researchId) {
    const eventSource = new EventSource(`/api/research/${researchId}/stream`);

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);

        if (data.stage === 'complete') {
            eventSource.close();
            window.location.href = `/results/${researchId}`;
            return;
        }

        if (data.stage === 'error') {
            eventSource.close();
            showError(data.message);
            return;
        }

        // Update progress UI
        const progressBar = document.getElementById('progressBar');
        const progressMessage = document.getElementById('progressMessage');

        if (progressBar) {
            progressBar.style.width = data.progress + '%';
        }
        if (progressMessage) {
            progressMessage.textContent = data.message;
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

async function handleSummarize(researchId) {
    const btn = document.getElementById('summarizeBtn');
    const summarySection = document.getElementById('summarySection');

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>Summarizing...';

    summarySection.style.display = 'block';
    summarySection.innerHTML = '<div class="summary-loading"><span class="spinner spinner-dark"></span> Generating summary...</div>';

    try {
        const response = await fetch(`/api/research/${researchId}/summarize`, {
            method: 'POST',
        });

        if (!response.ok) {
            throw new Error('Failed to generate summary');
        }

        const { summary } = await response.json();
        renderSummary(summary);
        btn.textContent = 'Regenerate Summary';
        btn.disabled = false;
    } catch (err) {
        summarySection.innerHTML = `<p class="error-message">Error: ${err.message}</p>`;
        btn.textContent = 'Retry Summary';
        btn.disabled = false;
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
    const progressMsg = document.getElementById('addThreadMessage');

    const url = input.value.trim();
    if (!url) return;

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner spinner-dark"></span>Processing...';
    progressEl.style.display = 'block';
    progressBar.style.width = '0%';
    progressMsg.textContent = 'Starting...';

    try {
        const resp = await fetch(`/api/research/${researchId}/add-thread`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            progressEl.style.display = 'none';
            btn.innerHTML = 'Add Thread';
            btn.disabled = false;
            progressMsg.textContent = data.error || 'Failed to add thread';
            progressEl.style.display = 'block';
            return;
        }

        if (data.already_exists) {
            btn.innerHTML = 'Add Thread';
            btn.disabled = false;
            progressBar.style.width = '100%';
            progressMsg.textContent = data.message;
            return;
        }

        // Listen to SSE stream for progress
        const es = new EventSource(`/api/research/${researchId}/add-thread/stream`);
        es.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            progressBar.style.width = msg.progress + '%';
            progressMsg.textContent = msg.message;

            if (msg.stage === 'complete') {
                es.close();
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

// ===== Find More Comments =====

async function checkExpandStatus(researchId) {
    try {
        const resp = await fetch(`/api/research/${researchId}/expand/status`);
        const data = await resp.json();
        const btn = document.getElementById('findMoreBtn');
        if (!btn) return;
        if (!data.can_expand) {
            btn.disabled = true;
            btn.title = 'All search strategies have been tried for this query';
        }
    } catch (_) {}
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

// Close popover when clicking elsewhere
document.addEventListener('click', (e) => {
    const popover = document.getElementById('archiveConfirm');
    if (popover && popover.style.display === 'block' && !popover.contains(e.target)) {
        hideArchiveConfirm();
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

// ===== Find More Comments =====

async function handleFindMore(researchId) {
    const btn = document.getElementById('findMoreBtn');
    const progressEl = document.getElementById('expandProgress');
    const progressBar = document.getElementById('expandProgressBar');
    const progressMsg = document.getElementById('expandMessage');

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner spinner-dark"></span>Finding more...';
    progressEl.style.display = 'block';
    progressBar.style.width = '0%';
    progressMsg.textContent = 'Starting...';

    try {
        const resp = await fetch(`/api/research/${researchId}/expand`, { method: 'POST' });
        if (!resp.ok) {
            const data = await resp.json();
            progressEl.style.display = 'none';
            btn.innerHTML = 'Find More Comments';
            btn.disabled = true;
            btn.title = data.error || 'No more search strategies available';
            return;
        }

        // Listen to SSE stream for progress
        const es = new EventSource(`/api/research/${researchId}/expand/stream`);
        es.onmessage = (event) => {
            const data = JSON.parse(event.data);

            progressBar.style.width = data.progress + '%';
            progressMsg.textContent = data.message;

            if (data.stage === 'complete') {
                es.close();
                progressEl.style.display = 'none';
                btn.innerHTML = 'Find More Comments';
                // Reload tables with new data
                loadResults(researchId);
                // Check if more expansions are still possible
                checkExpandStatus(researchId);
            } else if (data.stage === 'error') {
                es.close();
                progressEl.style.display = 'none';
                progressMsg.textContent = 'Error: ' + data.message;
                progressEl.style.display = 'block';
                btn.innerHTML = 'Find More Comments';
                btn.disabled = false;
            }
        };
        es.onerror = () => {
            es.close();
            progressEl.style.display = 'none';
            btn.innerHTML = 'Find More Comments';
            btn.disabled = false;
        };
    } catch (err) {
        progressEl.style.display = 'none';
        btn.innerHTML = 'Find More Comments';
        btn.disabled = false;
    }
}
