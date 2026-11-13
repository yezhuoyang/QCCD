"""A stand-in `docker` for tests/test_agent_review.py: what the review's code asks of Docker, answered
without Docker, accounts or a network.

    version / image inspect / build / ps / network ... / rm / logs   the plumbing, logged
    run ... script ... claude setup-token     Claude's sign-in: its screen, a link, "Paste code here",
                                              then (after a line on stdin) a subscription token
    run ... script ... codex login ...        Codex's: a link and a one-time code, then auth.json in
                                              the mounted profile
    run ... claude -p PROMPT ... / codex exec  a review: out/review.md and out/notes.json in the mounted
                                              job folder, and the CLI's own JSON events on stdout

Every call is appended to FAKE_LOG as JSON: its argv, and the names (never the values) of the
variables a `-e NAME` passes from the client's environment.  The log's path is written into the
copy the test runs (or set by its .cmd on Windows): the docker client gets no environment of the
caller's, by design, so a variable would not reach it.
"""

import base64
import json
import os
import sys

TOKEN = "sk-ant-oat01-" + "T" * 40
args = sys.argv[1:]


def log(**kw):
    p = globals().get("FAKE_LOG") or os.environ.get("FAKE_DOCKER_LOG")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw) + "\n")


def mount(dst):
    for i, a in enumerate(args):
        if a == "--mount" and f"dst={dst}" in args[i + 1]:
            return next(p[4:] for p in args[i + 1].split(",") if p.startswith("src="))
    return None


passed = [args[i + 1] for i, a in enumerate(args) if a == "-e" and i + 1 < len(args) and "=" not in args[i + 1]]
log(argv=args, by_name={n: n in os.environ for n in passed})
cmd = args[0] if args else ""
if cmd == "version":
    print("27.0.0")
elif cmd == "info":
    print("4")
elif cmd == "image":
    sys.exit(0)
elif cmd == "build":
    print("#1 DONE 0.1s")
elif cmd in ("ps", "rm") or (cmd == "network" and args[1] in ("ls", "rm", "create", "connect")):
    pass
elif cmd == "logs":
    print(json.dumps({"host": "api.anthropic.com", "port": "443", "allowed": True}))
    print(json.dumps({"host": "example.com", "port": "443", "allowed": False}))
elif cmd == "run" and "-d" in args:
    print("f" * 64)                                  # the egress proxy, detached
elif cmd == "run" and any("setup-token" in a for a in args):
    # Claude Code's screen: words drawn with cursor moves, the link, then the prompt for the code
    sys.stdout.write("\x1b[2J\x1b[1CWelcome\x1b[1Cto\x1b[1CClaude\x1b[1CCode\r\n"
                     "Browser didn't open? Use the url below to sign in (c to copy)\r\n\r\n"
                     "https://claude.com/cai/oauth/authorize?code=true&client_id=x&state=y\r\n\r\n"
                     "Paste code here if prompted > ")
    sys.stdout.flush()
    code = ""
    while True:                                      # a terminal in raw mode: Enter is a carriage return
        ch = sys.stdin.read(1)
        if ch in ("", "\r", "\n"):
            break
        code += ch
    if code != "good-code#state":
        print("\r\nOAuth error: Invalid code")
        sys.exit(1)
    sys.stdout.write("\r\n\x1b[32m✓\x1b[0m Long-lived authentication token created successfully!\r\n\r\n"
                     f"Your OAuth token (valid for 1 year):\r\n\r\n{TOKEN}\r\n\r\nStore this token securely.\r\n")
    sys.stdout.flush()
elif cmd == "run" and any("codex login" in a for a in args):
    print("Follow these steps to sign in with ChatGPT using device code authorization:\n"
          "1. Open this link in your browser and sign in to your account\n   https://auth.openai.com/codex/device\n"
          "2. Enter this one-time code (expires in 15 minutes)\n   ABCD-12345\n", flush=True)
    claims = base64.urlsafe_b64encode(json.dumps({"email": "person@example.org"}).encode()).decode().rstrip("=")
    with open(os.path.join(mount("/profile"), "auth.json"), "w", encoding="utf-8") as f:
        json.dump({"tokens": {"id_token": f"h.{claims}.s", "access_token": "a", "refresh_token": "r"}}, f)
    print("Successfully logged in")
elif cmd == "run":
    job = mount("/job")
    rid = "claude" if "claude" in args else "codex"
    report = ("# Verdict\n\nThe rule check found one failure, R7: the gate at step 7 starts with q0 too hot.\n\n"
              "# Findings\n\n## R7 at step 7\n\n- q0 is shuttled at step 5 and gated hot at step 7.\n"
              "- **Fix:** insert `cool` between step 6 and step 7.\n\n# Next steps\n\n1. Cool q0 before step 7.\n"
              "<script>alert('x')</script> [a link](javascript:alert(1)) " + "and a stray token " + TOKEN + "\n")
    with open(os.path.join(job, "out", "review.md"), "w", encoding="utf-8") as f:
        f.write(report)
    with open(os.path.join(job, "out", "notes.json"), "w", encoding="utf-8") as f:
        json.dump([{"step": 7, "rule": "R7", "text": "q0 is hot here"}, {"step": 0, "text": "not a step"},
                   {"step": 999, "text": "past the end"}, {"text": "no step"}], f)
    seen = sorted(os.listdir(job))
    if rid == "claude":
        events = [{"type": "system", "subtype": "init", "model": "claude-test-model"},
                  {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                                 "input": {"command": "python3 -m qccd.debug ... inspect R7 7"}}]}},
                  {"type": "assistant", "message": {"content": [{"type": "text", "text": "the job holds " + ", ".join(seen)}]}},
                  {"type": "result", "result": "DONE", "num_turns": 3, "duration_ms": 900, "total_cost_usd": 0.01,
                   "usage": {"input_tokens": 100, "output_tokens": 50}}]
    else:
        events = [{"type": "item.started", "item": {"type": "command_execution", "command": "/bin/bash -lc 'python3 -m qccd.debug summary'"}},
                  {"type": "item.completed", "item": {"type": "agent_message", "text": "DONE"}},
                  {"type": "turn.completed", "usage": {"input_tokens": 80, "output_tokens": 20}}]
    for e in events:
        print(json.dumps(e), flush=True)
else:
    print(f"fake docker: no answer for {args[:3]}", file=sys.stderr)
    sys.exit(1)
