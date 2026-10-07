---
description: Apply a finished glass-pipe job (clean path only; reverts itself if the test goes red on the main tree)
argument-hint: <job id>
allowed-tools: Bash(gp apply *), Bash(gp show *)
---

Run `gp apply $ARGUMENTS` and report the result in one line.

- `applied landed✓` → done.
- `dirty` → the files changed since the snapshot; tell the user which and why (the line says), and do not merge it by hand.
- `landing-red` → it was reverted automatically; tell the user the test fails on the main tree.
- `not-applied … needs a human stamp` → ask the user to run the `gp note` command themselves; never run it yourself.
