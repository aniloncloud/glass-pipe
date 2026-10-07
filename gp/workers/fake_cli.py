"""Process entry for FakeWorker: python -m gp.workers.fake_cli <script> <counter> <brief>."""
import json
import os
import sys
import time
from pathlib import Path


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main(script, counter, brief):
    calls = json.loads(Path(script).read_text())["calls"]
    try:
        i = int(Path(counter).read_text())
    except (FileNotFoundError, ValueError):
        i = 0
    Path(counter).write_text(str(i + 1))
    step = calls[min(i, len(calls) - 1)]
    sid = f"fake-{i}"
    emit({"type": "step_start", "sessionID": sid})
    sleep, beat = step.get("sleep", 0), step.get("heartbeat", 0)
    end = time.monotonic() + sleep
    while time.monotonic() < end:
        time.sleep(min(beat or sleep, max(0.0, end - time.monotonic())))
        if beat:
            emit({"type": "heartbeat", "sessionID": sid})
    for path, content in (step.get("write") or {}).items():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content)
    for path, content in (step.get("append") or {}).items():
        with open(path, "a") as fh:
            fh.write(content)
    for path, mode in (step.get("chmod") or {}).items():
        os.chmod(path, mode)
    for path in step.get("delete") or []:
        os.unlink(path)
    if step.get("error"):
        emit({"type": "error", "sessionID": sid, "error": {"type": step["error"]}})
        return 1
    text = step.get("text", "")
    if step.get("result") is not None and step.get("summary_env"):
        step["result"] = {**step["result"], "summary": os.environ.get(step["summary_env"], "")}
    if step.get("result") is not None:
        text += "\n<<<GP-RESULT\n" + json.dumps(step["result"])
    emit({"type": "text", "sessionID": sid, "part": {"text": text}})
    return step.get("exit", 0)


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
