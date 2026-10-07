"""Receipts: one stdout line per job; the full receipt lives on disk (gp show)."""
from __future__ import annotations

from .util import tail

ELIGIBLE = {"ok"}                                   # unattended apply
NEEDS_STAMP = {"behavior-unchanged", "tests-edited"}  # human `gp note --kind stamp --harness human`
NEEDS_OVERRIDE = {"tamper"}                          # human `gp note --kind override --harness human`
SEMANTIC = "semantic conflicts unchecked"


def _files(st) -> tuple[int, int, int]:
    fs = st.get("files") or []
    return len(fs), sum(f["adds"] for f in fs), sum(f["dels"] for f in fs)


def line(job_id: str, st: dict) -> str:
    status = st.get("status", "?")
    parts = [f"gp#{job_id}", status]
    if st.get("worker"):
        parts.append(st["worker"])
    if st.get("attempts"):
        parts.append(f"{st['attempts']}try")
    n, a, d = _files(st)
    if n:
        parts.append(f"{n}f +{a}-{d}")
    if "accept_green" in st:
        parts.append("accept✓" if st["accept_green"] else "accept✗")
    if status in ELIGIBLE:
        parts.append(f"· {SEMANTIC}")
    elif status in NEEDS_STAMP:
        parts.append("· needs human stamp")
    elif status in ("running", "queued"):
        parts.append(f"→ gp wait {job_id}")
        return " ".join(parts)
    return " ".join(parts) + f" → .glass/jobs/{job_id}/receipt.txt"


def full(job_id: str, st: dict) -> str:
    status = st.get("status")
    out = [line(job_id, st), ""]
    out.append(f"status: {status}")
    if st.get("detail"):
        out.append(f"detail: {st['detail']}")
    if st.get("fell_back_from"):
        out.append(f"fallback: {st['fell_back_from']} -> {st.get('worker')}")
    u = st.get("usage") or {}
    if u.get("input") or u.get("cost"):
        out.append(f"worker usage: ${u.get('cost', 0):.4f}, {u.get('input', 0)} in / {u.get('output', 0)} out tokens")
    if st.get("summary"):
        out.append(f"worker summary: {st['summary']}")
    if st.get("files"):
        out.append("files:")
        for f in st["files"]:
            out.append(f"  {f['path']} +{f['adds']}-{f['dels']}")
    if st.get("symbols"):
        out.append("symbols (hints from hunk headers; may name the preceding symbol):")
        for path, syms in st["symbols"].items():
            out.append(f"  {path}: {'; '.join(syms[:5])}")
    for g in (st.get("glass") or [])[:3]:
        out.append(f"note: {g['kind']} | {g['text']}")
    if st.get("skipped_untracked"):
        out.append(f"untracked not copied (caps): {len(st['skipped_untracked'])}")
    if status in ELIGIBLE | NEEDS_STAMP | NEEDS_OVERRIDE and st.get("files"):
        out.append(SEMANTIC + ": accept covers only what it tests")
    if status in ELIGIBLE:
        out.append(f"next: gp apply {job_id}   (or gp diff {job_id})")
    elif status in NEEDS_STAMP:
        out.append(f"next: human runs `gp note --job {job_id} --kind stamp --harness human`, then gp apply {job_id}")
    elif status in NEEDS_OVERRIDE:
        out.append(f"next: if this is a false positive, a human runs `gp note --job {job_id} --kind override --harness human`")
    return "\n".join(out) + "\n"


def why(job_id: str, st: dict, last_accept: str) -> str:
    return full(job_id, st) + "\nlast acceptance output (tail):\n" + tail(last_accept, 40) + "\n"
