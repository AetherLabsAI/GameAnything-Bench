#!/usr/bin/env python3
"""Sandbox-side helper of the Stardew adapter (copied to <root>/bin/sdbr.py; stdlib only).

    sdbr.py req GABDIR JSON [--timeout S] [--pid PIDFILE]      one request ({"cmd": ...}) or a JSON list of them -> answer(s)
    sdbr.py wait GABDIR KEY=V1|V2 [--timeout S] [--pid PIDFILE] poll `ping` until answer[KEY] is one of the values
    sdbr.py errors SMAPI_LOG                                    ERROR / exception lines of a SMAPI log grouped by kind (JSON)

The bridge (GabBridge, a SMAPI mod) reads GABDIR/req/<id>.json on the game thread and answers in GABDIR/ans/<id>.json."""
import json, os, re, sys, time, uuid


def alive(pidfile):
    if not pidfile: return True
    try: os.kill(int(open(pidfile).read().strip()), 0); return True
    except (OSError, ValueError): return False


def request(d, reqs, timeout=5.0, pidfile=None):
    ids = []
    for r in reqs:
        i = f"{time.time_ns()}-{uuid.uuid4().hex[:6]}"; ids.append(i)
        tmp = os.path.join(d, "req", i + ".tmp")
        with open(tmp, "w") as f: json.dump(r, f)
        os.replace(tmp, os.path.join(d, "req", i + ".json"))
    out, t0 = {}, time.time()
    while len(out) < len(ids) and time.time() - t0 < timeout:
        for i in ids:
            if i in out: continue
            p = os.path.join(d, "ans", i + ".json")
            if os.path.exists(p):
                try:
                    with open(p) as f: out[i] = json.load(f)
                    os.remove(p)
                except (OSError, ValueError): pass
        if len(out) < len(ids):
            if not alive(pidfile): break
            time.sleep(0.03)
    res = []
    for i in ids:
        if i in out: res.append(out[i]); continue
        try: os.remove(os.path.join(d, "req", i + ".json"))            # never leave a stale request behind
        except OSError: pass
        res.append({"error": "game not running" if not alive(pidfile) else f"no answer in {timeout} s", "no_answer": True})
    return res


KIND = re.compile(r"^\[\d\d:\d\d:\d\d (ERROR|WARN |ALERT)\s*(.*?)\] (.*)$")


def errors(path):
    """{kind: count}: ERROR lines (source + message with numbers / paths / ids masked) + 'exceptions' total"""
    cnt, exc = {}, 0
    try: lines = open(path, errors="replace").read().splitlines()
    except OSError: return {"errors": {}, "exceptions": 0, "missing": True}
    for l in lines:
        m = KIND.match(l)
        if "Exception" in l: exc += 1
        if not m or m.group(1) != "ERROR": continue
        msg = re.sub(r"\d+", "N", m.group(3))[:120]
        k = f"{m.group(2).strip()}: {msg}"; cnt[k] = cnt.get(k, 0) + 1
    return {"errors": cnt, "exceptions": exc}


def main(a):
    def opt(n, d=None):
        if n in a: i = a.index(n); v = a[i + 1]; del a[i:i + 2]; return v
        return d
    timeout = float(opt("--timeout", 5)); pid = opt("--pid")
    if a[0] == "req":
        r = json.loads(a[2]); one = isinstance(r, dict)
        res = request(a[1], [r] if one else r, timeout, pid)
        print(json.dumps(res[0] if one else res))
    elif a[0] == "wait":
        k, _, v = a[2].partition("="); want = v.split("|"); t0 = time.time(); last = {}
        while time.time() - t0 < timeout:
            last = request(a[1], [{"cmd": "ping"}], 4.0, pid)[0]
            if str(last.get(k)) in want: break
            if last.get("error") == "game not running": break
            time.sleep(0.5)
        last["waited_s"] = round(time.time() - t0, 1); last["ok"] = str(last.get(k)) in want
        print(json.dumps(last))
    elif a[0] == "errors": print(json.dumps(errors(a[1])))


if __name__ == "__main__":
    main(sys.argv[1:])
