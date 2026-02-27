# Claude Code Instructions

## Project Conventions

- **Backend**: Python Flask, services in `services/`, models in `models/`, SQLite for storage
- **Frontend**: Vanilla JS (no frameworks), server-rendered Jinja2 templates
- **Database migrations**: Use `ALTER TABLE ADD COLUMN` wrapped in try/except for idempotency — never drop/recreate tables
- **User data preservation**: Use `INSERT ... ON CONFLICT DO UPDATE` that only updates AI-scored fields, never overwrite user-set columns
- **CSS**: All styles in `static/css/style.css`, grouped by feature with section comments
- **JS**: `app.js` for page-level logic (forms, SSE, sidebar), `tables.js` for table rendering/sorting/filtering
