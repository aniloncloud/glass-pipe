---
description: Ask the worker model a read-only question about this codebase (capped answer)
argument-hint: <question>
allowed-tools: Bash(gp ask *)
---

Run exactly one command and use its answer; do not re-explore what it already answered:

```
gp ask $ARGUMENTS
```

The answer is capped (~400 tokens) and ends with REFS and READ_NEXT. Read only the READ_NEXT files you actually need. Use `gp ask --long …` only if the capped answer was cut off and you still need the rest.
