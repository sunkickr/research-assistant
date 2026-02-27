---
name: feature-check
description: Checks ARCHITECTURE.md and FEATURES.md before and after implementing a new feature or major refactor to ensure existing features continue to work. Use when planning or completing any new feature, significant code change, or refactor.
---

# Feature Compatibility Check

This skill ensures new features and refactors don't break existing functionality by reviewing project documentation before and after implementation.

## Before Implementation

Read and analyze these files:

1. **Read `ARCHITECTURE.md`** — Understand the system design, data flow, service layer, database schema, and frontend architecture. Identify which patterns and conventions the new changes must follow.
2. **Read `FEATURES.md`** — Review every existing feature. Identify which features could be affected by the planned changes.

Then report:
- A summary of which existing features are at risk of breaking
- Specific areas of concern (e.g., database schema changes could affect upserts, new API endpoints could conflict with existing ones, frontend changes could break sorting/filtering)
- Recommendations for maintaining backward compatibility (e.g., use ALTER TABLE with defaults, preserve user data in ON CONFLICT upserts, maintain existing API response shapes)

## After Implementation

Read both files again and verify:

1. **No broken features** — Walk through the features list in `FEATURES.md` and confirm each one still works given the code changes made
2. **Docs are current** — Flag if `FEATURES.md` needs a new entry or updates to existing entries, and if `ARCHITECTURE.md` needs updates (new endpoints, schema changes, data flow changes)

Then report:
- Any features that may have been impacted and whether they were properly handled
- What documentation updates are needed
- Offer to make the documentation updates
