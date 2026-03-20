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

## Operator process Mason should follow

This is the execution discipline Mason should copy when acting like a real coding agent instead of just a router.

1. Gather context before changing anything.
2. Read the existing task or story file first.
3. Read the smallest set of code and docs needed to understand the change.
4. Break the work into explicit task-sized units with a visible completion condition.
5. Execute one task end-to-end before starting the next one.
6. Verify the task with the nearest real check available: test, lint, smoke test, or contract validation.
7. Append completion notes to the existing task file.
8. Update trackers so an interrupted session can resume cleanly.
9. Commit and push after each completed task.

## Task packet rules

- Every task packet must answer five questions:
	- What is being changed?
	- Why is it being changed?
	- What must be true when it is done?
	- How will success be verified?
	- What file or artifact proves completion?
- Prefer task packets that affect one boundary, one contract, or one behavior.
- If a task packet grows beyond one coherent verification step, split it.

## Execution rules

- Fix the root cause, not the surface symptom.
- Reuse existing patterns already proven in the target system before inventing new ones.
- Prefer updating existing docs and task files over creating new summary files.
- If a repo already has sprint or task tracking, Mason must work inside that structure rather than bypass it.
- Keep each edit set tight enough that a commit message can describe it precisely.

## Progress reporting rules

- After context gathering, write a short delta update: what was learned and what comes next.
- After a burst of edits, summarize only the changed state, not the whole plan again.
- If interrupted, the tracker plus the task file must be enough to restart without guesswork.

## Commit discipline

- One completed task = one descriptive commit.
- Push immediately after the commit unless explicitly told not to.
- Do not mix unrelated cleanup into a task commit.

## Contract-first rule

- When work crosses agent or system boundaries, define or confirm the contract before code changes.
- Required fields, error semantics, ownership, and retry behavior must be explicit.
- If the system already has a mature contract model, align to it instead of introducing parallel semantics.

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
