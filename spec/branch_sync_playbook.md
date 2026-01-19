# Branch Sync Playbook (three_u_0231)

This file documents the recommended sync workflow for the `three_u_0231` branch
and how Project Management AI should handle conflicts and pushes to Gitee.

## Goals
- Keep `three_u_0231` tracking upstream (official) changes.
- Preserve local customizations in `three_u_0231`.
- Push updates to `gitee/three_u_0231` and, when approved, force-sync to
  `gitee/master`.

## Recommended Workflow (merge-based)
Use merge to keep history clear and reduce rebase risks.

```bash
# 1) Ensure clean working tree
git checkout three_u_0231
git status -sb

# 2) If local changes exist, either commit or stash
# commit:
git add -A
git commit -m "wip: local changes"

# OR stash:
git stash -u

# 3) Bring in official updates
git fetch upstream
git merge upstream/main

# 4) Resolve conflicts if any, then commit the merge
git add <conflict-files>
git commit

# 5) Push to Gitee
git push gitee three_u_0231
```

## Conflict Resolution Checklist
1) `git status` to list conflicted files.
2) Open each file and resolve markers:
   - `<<<<<<<`
   - `=======`
   - `>>>>>>>`
3) Ensure the result keeps required 3U changes.
4) `git add <file>` for each resolved file.
5) `git commit` to finish the merge.

## Rebase Alternative (linear history, higher risk)
Only use rebase if you need a clean linear history and understand the risks.

```bash
git checkout three_u_0231
git fetch upstream
git rebase upstream/main

# resolve conflicts
git add <conflict-files>
git rebase --continue

# push updated history
git push gitee three_u_0231 --force-with-lease
```

## Force-Sync to Gitee master (destructive)
Only run when approved. This overwrites `gitee/master` to match
`three_u_0231`.

```bash
git push gitee three_u_0231:master --force
```

## Project Management AI Handoff (role notes)
- Primary branch: `three_u_0231`
- Official source: `upstream/main`
- Gitee target: `gitee/three_u_0231`
- When instructed, force-sync `gitee/master` from `three_u_0231`
- If conflicts arise:
  - Prefer keeping 3U logic and recent local changes.
  - If unclear, request human confirmation before finalizing.

## Minimal Safety Checks
- `git status -sb` before and after merges.
- `git log --oneline -5` after merging to verify the merge commit.
- Never force-push unless explicitly approved.
