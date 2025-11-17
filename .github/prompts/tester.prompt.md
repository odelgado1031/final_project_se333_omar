---
mode: "agent"
tools: ["echo", "safe_calc", "maven_test", "git_status", "git_add_all", "git_commit", "git_push", "parse_jacoco"]
description: "SE333 testing agent that can run Maven tests, analyze coverage, and commit improvements."
model: "GPT-5 mini"
---

## Follow instruction below: ##
1. Run the Maven tests on the provided codebase and report results.
2. If tests fail, suggest and apply fixes (with user approval), then re-run.
3. Generate JaCoCo coverage and report % lines/branches; suggest next tests.
4. If coverage improves, `git add`, `git commit` (include coverage in message), and `git push`.
