---
name: update-feature
description: Guides updating an existing feature from a PRD or description. Reads ARCHITECTURE.md and FEATURES.md to reuse existing patterns, avoid overengineering, and prevent breaking existing functionality. Updates existing feature entries in docs rather than creating new ones.
---

# Update Existing Feature from PRD

This skill guides modifications to an existing feature using a PRD or description as the source of truth. It ensures updates integrate cleanly with existing architecture, reuse existing code, don't break other features, and are documented as updates to the existing feature entry — not as new features.

## Inputs

The user will provide a PRD or description — either as a file path, pasted text, or a verbal description of the update. The PRD may contain:
- What existing feature is being updated and why
- Specific changes, enhancements, or fixes
- Open questions or research items that need investigation
- Technical constraints or preferences

## Phase 1: Understand the Codebase and Existing Feature

Read and internalize these files before writing any code:

1. **Read `FEATURES.md`** — Find the existing feature entry that is being updated. Understand its current behavior, location, and details. Identify other features that interact with it or could be affected.
2. **Read `ARCHITECTURE.md`** — Understand how the feature fits into the system design, data flow, and frontend architecture. Identify existing utilities, helpers, and patterns that should be reused.

Report to the user:
- Which existing feature entry in `FEATURES.md` this update applies to (by number and name)
- Which other features interact with this feature and could be affected
- Which existing patterns, services, and utilities will be reused
- Any conflicts between the PRD and existing architecture

**IMPORTANT**: If the update does not clearly map to an existing feature in `FEATURES.md`, ask the user whether this should be treated as a new feature (use `/build-feature` instead) or as an update to a specific existing feature.

## Phase 2: Research and Resolve Questions

If the PRD contains open questions, research items, or decision points:

1. **Conduct research** — Search the codebase, read relevant files, and use web search if needed to gather information
2. **Prompt the user** — Present findings and options using `AskUserQuestion` for each decision that needs user input
3. **Do not assume** — If the PRD says "research X" or "decide between A and B", do the research and present options rather than picking one silently

Continue to Phase 3 only after all questions are resolved.

## Phase 3: Build or Plan

Behavior depends on the current Claude Code mode:

### In Plan Mode
- Design the update approach based on the PRD, resolved questions, and codebase understanding
- Write a detailed plan to the plan file specifying:
  - Files to modify (with specific line references where applicable)
  - Existing functions and patterns to reuse (with file paths)
  - Database changes (if any) using `ALTER TABLE ADD COLUMN` with defaults for idempotency
  - API endpoint changes (preserving existing response shapes)
  - Frontend changes (following existing JS/CSS conventions)
- Flag anything in the PRD that conflicts with existing architecture
- Call `ExitPlanMode` when the plan is ready

### In Edit Mode
- Implement the update following the PRD requirements
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
   - **Update the existing feature entry** in `FEATURES.md` — modify the Description, Location, and/or Details of the feature that was changed. Do NOT add a new numbered feature entry.
   - If the update scope feels like it warrants a new feature entry (e.g., it adds a substantially new capability rather than enhancing the existing one), ask the user: "This update adds significant new functionality. Should I add it as a new feature entry in FEATURES.md, or update the existing Feature #N entry?"
   - Update `ARCHITECTURE.md` if there are changes to endpoints, schema, data flow, or frontend structure
4. **Report** — Summarize what was updated, which tests pass, and any documentation changes made
