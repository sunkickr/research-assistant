---
name: build-feature
description: Guides building or planning a new feature from a PRD. Reads ARCHITECTURE.md and FEATURES.md to reuse existing patterns, avoid overengineering, and prevent breaking existing functionality. Use when starting work on a new feature described in a PRD.
---

# Build Feature from PRD

This skill guides the implementation or planning of a new feature using a PRD (Product Requirements Document) as the source of truth. It ensures new features integrate cleanly with existing architecture, reuse existing code, and don't break existing functionality.

## Inputs

The user will provide a PRD — either as a file path, pasted text, or a description of the feature. The PRD may contain:
- Problem statement and proposed solution
- Specific requirements and acceptance criteria
- Open questions or research items that need investigation
- Technical constraints or preferences

## Phase 1: Understand the Codebase

Read and internalize these files before writing any code:

1. **Read `FEATURES.md`** — Review every existing feature. Identify which features the new work will build on, interact with, or could break.
2. **Read `ARCHITECTURE.md`** — Understand the system design, data flow, service layer, database schema, and frontend patterns. Identify existing utilities, helpers, and patterns that should be reused.

Report to the user:
- Which existing features are related to the new feature
- Which existing patterns, services, and utilities will be reused
- Which existing features are at risk of breaking and how to protect them
- Any conflicts between the PRD and existing architecture

## Phase 2: Research and Resolve Questions

If the PRD contains open questions, research items, or decision points:

1. **Conduct research** — Search the codebase, read relevant files, and use web search if needed to gather information
2. **Prompt the user** — Present findings and options using `AskUserQuestion` for each decision that needs user input
3. **Do not assume** — If the PRD says "research X" or "decide between A and B", do the research and present options rather than picking one silently

Continue to Phase 3 only after all questions are resolved.

## Phase 3: Build or Plan

Behavior depends on the current Claude Code mode:

### In Plan Mode
- Design the implementation approach based on the PRD, resolved questions, and codebase understanding
- Write a detailed plan to the plan file specifying:
  - Files to create or modify (with specific line references where applicable)
  - Existing functions and patterns to reuse (with file paths)
  - Database changes (if any) using `ALTER TABLE ADD COLUMN` with defaults for idempotency
  - API endpoint changes (preserving existing response shapes)
  - Frontend changes (following existing JS/CSS conventions)
- Flag anything in the PRD that conflicts with existing architecture
- Call `ExitPlanMode` when the plan is ready

### In Edit Mode
- Implement the feature following the PRD requirements
- Reuse existing code — prefer calling existing services, helpers, and patterns over writing new ones
- Follow project conventions from `CLAUDE.md`:
  - Database migrations: `ALTER TABLE ADD COLUMN` in try/except
  - User data preservation: `INSERT ... ON CONFLICT DO UPDATE` skipping user columns
  - CSS in `static/css/style.css` grouped by feature
  - JS split: `app.js` for page logic, `tables.js` for table rendering
- Keep it simple — implement exactly what the PRD specifies, nothing more
- Do not add extra error handling, configurability, or abstractions beyond what's needed

## Phase 4: Verify

After implementation is complete:

1. **Run tests** — Execute `python3 -m pytest tests/ -v` and fix any failures
2. **Feature compatibility check** — Read `FEATURES.md` and `ARCHITECTURE.md` again. Walk through every feature that was flagged as at-risk in Phase 1 and confirm it still works given the changes made
3. **Update documentation**:
   - Add a new entry to `FEATURES.md` for the new feature (following the existing numbered format with Description, Location, and Details)
   - Update `ARCHITECTURE.md` if there are new endpoints, schema changes, data flow changes, or frontend structure changes
4. **Report** — Summarize what was built, which tests pass, and any documentation updates made
