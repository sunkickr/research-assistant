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

    // Simple markdown-like rendering: headers and paragraphs
    const html = text
        .split('\n')
        .map(line => {
            line = line.trim();
            if (!line) return '';
            if (line.startsWith('#### ')) return `<h4>${line.slice(5)}</h4>`;
            if (line.startsWith('### ')) return `<h3>${line.slice(4)}</h3>`;
            if (line.startsWith('## ')) return `<h2>${line.slice(3)}</h2>`;
            if (line.startsWith('# ')) return `<h1>${line.slice(2)}</h1>`;
            if (line.startsWith('- ') || line.startsWith('* ')) return `<li>${line.slice(2)}</li>`;
            if (line.startsWith('**') && line.endsWith('**')) return `<h4>${line.slice(2, -2)}</h4>`;
            return `<p>${line}</p>`;
        })
        .join('\n');

    section.innerHTML = `
        <h3>Summary</h3>
        <div class="summary-content">${html}</div>
    `;
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
