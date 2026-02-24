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
