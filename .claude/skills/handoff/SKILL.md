---
name: handoff
description: Compresses the current conversation into only the context relevant to a specified next task. Produces a clean handoff message to start a new focused thread. Use when switching tasks, starting a new thread, or when the current conversation has grown large and you want to carry forward only what matters.
---

# Handoff

This skill analyzes the current conversation and compresses it into a focused handoff message containing only the information relevant to the user's next task. The goal is to produce the cleanest possible context so Claude performs optimally in the new thread.

## Phase 1: Get the Next Goal

Ask the user what they want to do next using `AskUserQuestion`. The question should be open-ended to let them describe their next task in their own words.

If the user already provided the next goal when invoking the skill (e.g., `/handoff I want to add tests for the search service`), skip this phase and use their stated goal.

## Phase 2: Analyze and Compress

Review the entire conversation history. For each piece of information, apply a strict relevance filter against the stated next goal:

**Include only if directly relevant to the next task:**
- Architectural decisions and design choices that affect the next task
- File paths and specific code locations (with line numbers) that will be touched or referenced
- Research findings, API discoveries, or technical constraints that apply
- Database schema changes or migrations that were made
- Bug root causes and fixes that provide necessary context
- Current state of in-progress work that the next task builds on
- Key user preferences or requirements that were established

**Exclude ruthlessly:**
- Conversation about unrelated features or bugs
- Failed approaches that were abandoned (unless the failure is informative for the next task)
- General project exploration that doesn't bear on the next task
- Pleasantries, status updates, and meta-discussion
- Redundant information — keep the most concise version

## Phase 3: Output the Handoff Message

Produce the handoff message in the following format. Output it directly as text (not in a code block) so the user can copy it:

---

**Goal:** [One sentence describing what needs to be done]

**Context:**
[2-5 bullet points of essential background. Each bullet should be a concrete fact, not a vague summary. Include file paths and line references where applicable.]

**Current State:**
[What has been done so far that's relevant. What's working, what's not. Include specific file paths of recently modified files if applicable.]

**Key Files:**
[Bulleted list of files that are relevant to the next task, with a brief note on why each matters]

**Constraints or Decisions:**
[Any decisions already made, user preferences, or technical constraints that the next thread needs to respect. Omit this section if there are none.]

---

Keep the entire handoff message as short as possible while preserving everything the next thread needs to succeed. Aim for under 30 lines. If context is minimal, the message can be just a few lines — don't pad it.
