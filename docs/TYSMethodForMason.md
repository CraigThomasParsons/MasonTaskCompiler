# TYS Method for Mason Story->Code Pipeline

This is the execution method Mason follows for every feature, behavior change, or bug fix:

DevBacklog (Current Sprint Stories) -> Mason (Planner + Provider Router) -> Framework Provider -> Code

## Loop

0. Put the story in progress.
0a. Plan the implementation as small low-effort tasks.
0b. Write or refresh `context.md` in the target code folder.
0c. Write or refresh `goals.md` with sprint goals.
1. Build one task.
2. Test that task.
3. Did it work?

- Yes: Continue with the next task.
- No: Fix the smallest root cause and re-test the same task.

## Source of truth

- Mason must read from DevBacklog Current Sprint first.
- If no current sprint stories are available, Mason may fallback to Ready stories.
- Story project context decides code location (`local_location` / `code_folder`) and repository metadata.

## Decomposition rules

- Decompose stories into very small tasks.
- Prefer one acceptance criterion per task packet.
- Keep each task independently testable.
- Do not batch multiple unrelated behaviors into one task.

## Provider routing policy

- High-complexity tasks: prefer stronger hosted models.
- Low-complexity mechanical tasks: prefer local models (Ollama/Goose) when available.
- Provider failure (rate limits, transient API issues) triggers failover without consuming an implementation attempt.
- Execution failure consumes an attempt and requires retry guidance.

## Required observability

- Every task execution emits provider, status, and duration.
- Story-level plan and task progress must be visible from DevBacklog (API/UI integration).
- Context files written by Mason are part of the audit trail in the code folder.

## Non-negotiable rules

- Mason can only perform actions a human can perform via the same app/API.
- Mason does not bypass story workflows by direct DB writes.
- Mason processes one task at a time and validates before moving forward.
