// ===== Table State =====
let allThreads = [];
let allComments = [];
let filteredComments = [];
let activeThreadFilter = null;

// Sorting state
let threadSortCol = 'score';
let threadSortDir = 'desc';
let commentSortCol = 'relevancy_score';
let commentSortDir = 'desc';

// Pagination
const PAGE_SIZE = 50;
let currentPage = 1;

// ===== Load Results from API =====

async function loadResults(researchId) {
    try {
        const response = await fetch(`/api/research/${researchId}`);
        if (!response.ok) throw new Error('Failed to load results');
        const data = await response.json();

        allThreads = data.threads || [];
        allComments = data.comments || [];
        filteredComments = [...allComments];

        renderThreadsTable();
        renderCommentsTable();
        updateCommentsMeta();
    } catch (err) {
        document.getElementById('threadsTableContainer').innerHTML =
            `<p class="error-message">Error loading results: ${err.message}</p>`;
        document.getElementById('commentsTableContainer').innerHTML = '';
    }
}

// ===== Threads Table =====

function renderThreadsTable() {
    const sorted = sortData([...allThreads], threadSortCol, threadSortDir);
    const container = document.getElementById('threadsTableContainer');

    if (sorted.length === 0) {
        container.innerHTML = '<p style="color: #7c7c7c; padding: 12px;">No threads found.</p>';
        return;
    }

    const cols = [
        { key: 'title', label: 'Title' },
        { key: 'subreddit', label: 'Subreddit' },
        { key: 'score', label: 'Score' },
        { key: 'num_comments', label: 'Comments' },
        { key: 'created_utc', label: 'Date' },
        { key: 'link', label: 'Link', nosort: true },
    ];

    let html = '<div class="table-wrapper"><table>';
    html += '<thead><tr>';
    for (const col of cols) {
        const sortClass = !col.nosort && threadSortCol === col.key
            ? (threadSortDir === 'asc' ? 'sorted-asc' : 'sorted-desc')
            : '';
        const onclick = col.nosort ? '' : `onclick="sortThreads('${col.key}')"`;
        html += `<th class="${sortClass}" ${onclick}>${col.label}</th>`;
    }
    html += '</tr></thead><tbody>';

    for (const thread of sorted) {
        const isActive = activeThreadFilter === thread.id;
        html += `<tr class="thread-row ${isActive ? 'active' : ''}" onclick="filterByThread('${thread.id}', '${escapeHtml(thread.title)}')">`;
        html += `<td>${escapeHtml(thread.title)}</td>`;
        html += `<td>r/${escapeHtml(thread.subreddit)}</td>`;
        html += `<td>${formatNumber(thread.score)}</td>`;
        html += `<td>${formatNumber(thread.num_comments)}</td>`;
        html += `<td>${formatDate(thread.created_utc)}</td>`;
        html += `<td><a href="${escapeHtml(thread.permalink)}" target="_blank" class="link-external" onclick="event.stopPropagation()">View</a></td>`;
        html += '</tr>';
    }

    html += '</tbody></table></div>';
    container.innerHTML = html;
}

function sortThreads(col) {
    if (threadSortCol === col) {
        threadSortDir = threadSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        threadSortCol = col;
        threadSortDir = col === 'title' || col === 'subreddit' ? 'asc' : 'desc';
    }
    renderThreadsTable();
}

// ===== Comments Table =====

function renderCommentsTable() {
    const sorted = sortData([...filteredComments], commentSortCol, commentSortDir);
    const container = document.getElementById('commentsTableContainer');

    if (sorted.length === 0) {
        container.innerHTML = '<p style="color: #7c7c7c; padding: 12px;">No comments to display.</p>';
        document.querySelector('.pagination')?.remove();
        return;
    }

    // Pagination
    const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
    if (currentPage > totalPages) currentPage = totalPages;
    const start = (currentPage - 1) * PAGE_SIZE;
    const pageData = sorted.slice(start, start + PAGE_SIZE);

    const cols = [
        { key: 'relevancy_score', label: 'Relevancy' },
        { key: 'body', label: 'Comment', nosort: true },
        { key: 'author', label: 'Author' },
        { key: 'score', label: 'Score' },
        { key: 'thread_id', label: 'Thread', nosort: true },
        { key: 'created_utc', label: 'Date' },
        { key: 'link', label: 'Link', nosort: true },
    ];

    let html = '<div class="table-wrapper"><table>';
    html += '<thead><tr>';
    for (const col of cols) {
        const sortClass = !col.nosort && commentSortCol === col.key
            ? (commentSortDir === 'asc' ? 'sorted-asc' : 'sorted-desc')
            : '';
        const onclick = col.nosort ? '' : `onclick="sortComments('${col.key}')"`;
        html += `<th class="${sortClass}" ${onclick}>${col.label}</th>`;
    }
    html += '</tr></thead><tbody>';

    for (const comment of pageData) {
        const scoreClass = comment.relevancy_score >= 8 ? 'score-high'
            : comment.relevancy_score >= 5 ? 'score-medium' : 'score-low';
        const thread = allThreads.find(t => t.id === comment.thread_id);
        const threadTitle = thread ? thread.title.slice(0, 40) : comment.thread_id;
        const bodyPreview = comment.body.slice(0, 200);
        const hasMore = comment.body.length > 200;

        html += `<tr>`;
        html += `<td><span class="score-badge ${scoreClass}">${comment.relevancy_score}</span></td>`;
        html += `<td class="comment-body-cell">`;
        html += `<div class="comment-body-preview" id="preview-${comment.id}" onclick="toggleComment('${comment.id}')">${escapeHtml(bodyPreview)}${hasMore ? '...' : ''}</div>`;
        html += `<div class="comment-body-full" id="full-${comment.id}">${escapeHtml(comment.body)}</div>`;
        html += `<div class="comment-reasoning" id="reasoning-${comment.id}"><strong>AI Reasoning:</strong> ${escapeHtml(comment.reasoning || '')}</div>`;
        if (hasMore || comment.reasoning) {
            html += `<span class="expand-toggle" id="toggle-${comment.id}" onclick="toggleComment('${comment.id}')">Show more</span>`;
        }
        html += `</td>`;
        html += `<td>${escapeHtml(comment.author)}</td>`;
        html += `<td>${formatNumber(comment.score)}</td>`;
        html += `<td title="${escapeHtml(thread ? thread.title : '')}">${escapeHtml(threadTitle)}${threadTitle.length < (thread ? thread.title.length : 0) ? '...' : ''}</td>`;
        html += `<td>${formatDate(comment.created_utc)}</td>`;
        html += `<td><a href="${escapeHtml(comment.permalink)}" target="_blank" class="link-external">View</a></td>`;
        html += '</tr>';
    }

    html += '</tbody></table></div>';

    // Pagination controls
    if (totalPages > 1) {
        html += `<div class="pagination">
            <button onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>Previous</button>
            <span class="page-info">Page ${currentPage} of ${totalPages}</span>
            <button onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>Next</button>
        </div>`;
    }

    container.innerHTML = html;
}

function sortComments(col) {
    if (commentSortCol === col) {
        commentSortDir = commentSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        commentSortCol = col;
        commentSortDir = col === 'author' ? 'asc' : 'desc';
    }
    currentPage = 1;
    renderCommentsTable();
}

function goToPage(page) {
    currentPage = page;
    renderCommentsTable();
    // Scroll to comments table
    document.getElementById('commentsTableContainer').scrollIntoView({ behavior: 'smooth' });
}

// ===== Thread Filtering =====

function filterByThread(threadId, threadTitle) {
    if (activeThreadFilter === threadId) {
        clearThreadFilter();
        return;
    }
    activeThreadFilter = threadId;
    filteredComments = allComments.filter(c => c.thread_id === threadId);
    currentPage = 1;

    document.getElementById('filterThreadName').textContent = threadTitle;
    document.getElementById('threadFilterBanner').classList.add('visible');

    renderThreadsTable();
    renderCommentsTable();
    updateCommentsMeta();
}

function clearThreadFilter() {
    activeThreadFilter = null;
    filteredComments = [...allComments];
    currentPage = 1;

    document.getElementById('threadFilterBanner').classList.remove('visible');

    renderThreadsTable();
    renderCommentsTable();
    updateCommentsMeta();
}

function updateCommentsMeta() {
    const meta = document.getElementById('commentsMeta');
    if (!meta) return;

    if (activeThreadFilter) {
        meta.textContent = `Showing ${filteredComments.length} of ${allComments.length} comments`;
    } else {
        meta.textContent = `${allComments.length} comments, sorted by relevancy`;
    }
}

// ===== Comment Expansion =====

function toggleComment(commentId) {
    const preview = document.getElementById(`preview-${commentId}`);
    const full = document.getElementById(`full-${commentId}`);
    const reasoning = document.getElementById(`reasoning-${commentId}`);
    const toggle = document.getElementById(`toggle-${commentId}`);

    const isExpanded = full.classList.contains('expanded');

    if (isExpanded) {
        full.classList.remove('expanded');
        reasoning.classList.remove('expanded');
        preview.classList.remove('collapsed');
        if (toggle) toggle.textContent = 'Show more';
    } else {
        full.classList.add('expanded');
        reasoning.classList.add('expanded');
        preview.classList.add('collapsed');
        if (toggle) toggle.textContent = 'Show less';
    }
}

// ===== Utility Functions =====

function sortData(data, col, dir) {
    return data.sort((a, b) => {
        let valA = a[col];
        let valB = b[col];

        if (typeof valA === 'string') valA = valA.toLowerCase();
        if (typeof valB === 'string') valB = valB.toLowerCase();

        if (valA < valB) return dir === 'asc' ? -1 : 1;
        if (valA > valB) return dir === 'asc' ? 1 : -1;
        return 0;
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function formatNumber(num) {
    if (num === null || num === undefined) return '0';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
    return String(num);
}

function formatDate(utc) {
    if (!utc) return '';
    const date = new Date(utc * 1000);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
}
