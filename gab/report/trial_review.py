"""Operator review page for finished trials (internal: it shows game screenshots and source keyframes, never publish it).
One page per run: per trial the scores, every rubric item with each judge's score and reason (Chinese translation next to the
English), what each evaluator did, its time and cost, and slideshow videos of the play verifier, the replays and the agent's own
development screenshots (encoded on the host).

    python3 -m gab.report.trial_review --name opus [--rubrics TGZ] [--serve 8894]
Writes $GAB_RUNS_DIR/<name>/_review/{index.html, data.json, media/...}."""
import argparse, glob, json, re, shutil, subprocess, tarfile, tempfile
from collections import Counter
from pathlib import Path

import yaml

from .. import config
from ..util import claude, loose_json

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
COLS = [("content_realization", "CR · 内容实现"), ("functions_playability", "FP · 功能与可玩性"), ("visual_presentation", "VP · 视觉与呈现"),
        ("overall", "Overall · 总分")]


def rj(p):
    try: return json.loads(Path(p).read_text())
    except Exception: return None


def slideshow(frames, dst, captions=None, sec=1.6):
    """frames -> mp4 slideshow (960 px, caption bottom-left); encoded on the host"""
    frames = [Path(f) for f in frames if Path(f).exists()]
    if not frames: return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists(): return dst
    with tempfile.TemporaryDirectory() as td:
        segs = []
        for i, f in enumerate(frames):
            seg = Path(td) / f"s{i:04d}.mp4"; cap = (captions or [None] * len(frames))[i] or f.stem
            cap = re.sub(r"[^\w .,:#/()+\-]", " ", cap)[:90].replace(":", "\\:")
            vf = (f"scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,"
                  f"drawtext=fontfile={FONT}:text='{cap}':x=10:y=h-30:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-loop", "1", "-t", str(sec), "-i", str(f), "-vf", vf, "-r", "10",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(seg)], check=True, timeout=120)
            segs.append(seg)
        lst = Path(td) / "list.txt"; lst.write_text("".join(f"file '{s}'\n" for s in segs))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                        "-movflags", "+faststart", str(dst)], check=True, timeout=300)
    return dst


def diff_text(ev):
    """probe evidence -> one readable English line (translated later): 'saturation: vanilla 0, with the mod 5'"""
    if isinstance(ev, dict):
        parts = []
        for k, v in ev.items():
            m = re.match(r"^\s*(.+?)\s*->\s*(.+?)\s*$", str(v))
            parts.append(f"{k}: vanilla {m.group(1)}, with the mod {m.group(2)}" if m else f"{k}: {v if v not in ([], {}) else 'none'}")
        return "; ".join(parts)
    s = str(ev)
    return {"same as vanilla": "same result as vanilla", "unchanged": "same result as vanilla"}.get(s.strip(), s)


def agent_summary(tr):
    """tools used, `game` commands and the final message, from a Claude Code (stream-json) or OpenCode (json events) transcript"""
    tools, game, last = Counter(), Counter(), None
    if not Path(tr).exists(): tr = Path(tr).parent.parent / "transcript.jsonl"          # CLI adapters write it next to agent/
    if not Path(tr).exists(): return {"tools": {}, "game_cmds": [], "final": None}
    for line in open(tr, errors="replace"):
        try: e = json.loads(line)
        except Exception: continue
        if e.get("type") == "assistant":                                                    # Claude Code
            for b in e["message"].get("content", []):
                if b.get("type") == "tool_use":
                    tools[b["name"]] += 1
                    c = (b.get("input") or {}).get("command", "").strip().split()
                    if b["name"] == "Bash" and c and c[0] == "game": game[" ".join(c[:2])] += 1
        if e.get("type") == "result": last = e.get("result")
        part = e.get("part") or {}                                                          # OpenCode
        if part.get("type") == "tool":
            tools[part.get("tool", "?")] += 1
            c = str(((part.get("state") or {}).get("input") or {}).get("command", "")).strip().split()
            if c and c[0] == "game": game[" ".join(c[:2]).rstrip(";")] += 1
        if part.get("type") == "text" and part.get("text"): last = part["text"]
    return {"tools": dict(tools), "game_cmds": game.most_common(), "final": last}


def rubric_items(task, tgz):
    """the rubric the trial was scored with: GAB_HIDDEN_DIR / rubrics, or an archived copy (--rubrics)"""
    if tgz:
        with tarfile.open(tgz) as t:
            m = next((x for x in t.getmembers() if x.name.endswith(f"{task}/rubric.yaml")), None)
            if m: return yaml.safe_load(t.extractfile(m))
    from ..run.task import Task
    return Task(task).hidden


def timelines(F):
    """{trace: [timeline entries]} of the replays (replay/<trace>/replay.json), and {action n: record} of the play verifier"""
    T = {}
    for f in sorted((F / "replay").glob("*/replay.json")) if (F / "replay").exists() else []:
        T[f.parent.name] = (rj(f) or {}).get("timeline") or []
    A = {}
    if (F / "play/actions.jsonl").exists():
        for l in open(F / "play/actions.jsonl"):
            try: x = json.loads(l); A[int(x["n"])] = x
            except Exception: pass
    return T, A


def cited(ev, F, md, tid, cap=8):
    """screenshots an FP verdict cites (replay: NNN_name.png / zz_end.png; play: "action N") -> [{img, label, diff}] with the
    state change at that screenshot point (docs/STATE_SPEC.md)"""
    from ..game.base import diff_brief
    T, A = timelines(F); out, seen = [], set()
    txt = json.dumps([((ev or {}).get(k) or {}).get("evidence") for k in ("replay", "play")], ensure_ascii=False)
    for m in re.finditer(r"([\w-]+/)?(\d{3}_[\w.-]+?\.png|zz_end\.png)", txt):
        name = m.group(2)
        for tr, tl in T.items():
            if m.group(1) and m.group(1)[:-1] != tr: continue
            e = next((x for x in tl if x.get("screenshot") == name), None)
            if e is None or (tr, name) in seen: continue
            seen.add((tr, name)); src = F / "replay" / tr / name
            if src.exists(): shutil.copy(src, md / f"cite_{tr}_{name}")
            out.append({"img": f"media/{tid}/cite_{tr}_{name}" if src.exists() else None, "label": f"回放 {tr} · {name}",
                        "diff": diff_brief(e.get("diff"), 900)})
    for m in re.finditer(r"[Aa]ction\s*#?(\d+)", txt):
        n = int(m.group(1)); x = A.get(n)
        if x is None or ("play", n) in seen: continue
        seen.add(("play", n)); src = Path(x.get("shot") or "")
        if src.is_file(): shutil.copy(src, md / f"cite_play_{n:03d}.png")
        out.append({"img": f"media/{tid}/cite_play_{n:03d}.png" if src.is_file() else None,
                    "label": f"试玩 动作 {n}: {x.get('cmd')} {' '.join(map(str, x.get('args') or []))}"[:80], "diff": diff_brief(x.get("state_diff"), 900)})
    return out[:cap]


def trial(d, out, tgz):
    res = rj(d / "result.json"); task, level = res["trial"]["task"], res["trial"]["level"]
    run_name = d.parent.parent.name; tid = f"{run_name}.{task}.{level}"; md = out / "media" / tid; md.mkdir(parents=True, exist_ok=True)
    rub = rubric_items(task, tgz); R = rub["rubric"]
    text = {i["id"]: i for s in ("content_realization", "functions_playability", "visual_presentation") for i in R.get(s) or []}
    E = d / "eval"; items = []
    gid = (yaml.safe_load((d / "work/task/task.yaml").read_text()) or {}).get("target_game", "")
    pf = config.REPO / "benchmark" / "pools" / gid / "pool.yaml"; pd = yaml.safe_load(pf.read_text()) if pf.exists() else {}
    pool = {i["id"]: i.get("capability") for i in (pd.get("pool") or []) + (pd.get("always") or []) if isinstance(i, dict)}
    notes = rj(out / "notes.json") or {}
    for it in res["items"]:
        r = {"id": it["id"], "column": it["column"], "score": it["score"], "could_not_test": it.get("could_not_test"),
             "item": (text.get(it["id"]) or {}).get("item"), "anchors": (text.get(it["id"]) or {}).get("anchors"), "judges": []}
        ev = it.get("evidence")
        if it["column"] == "host_preservation":
            r["item"] = pool.get(it["id"], it.get("probe")); r["probe"] = it.get("probe")
            r["judges"].append({"who": "第 1 次测量" if it.get("first_run") else "测量", "score": it["score"] if not it.get("first_run") else float(bool(it["first_run"][0])),
                                "reason": diff_text(it["first_run"][1] if it.get("first_run") else ev)})
            if it.get("first_run"): r["judges"].append({"who": "失败后在新实例上重测", "score": it["score"], "reason": diff_text(ev)})
            r["note"] = (notes.get(f"{task}|{it['id']}") or "")
        elif isinstance(ev, str) and it["column"] in ("content_realization", "functions_playability", "visual_presentation"):   # not judged (e.g. gate failed)
            r["judges"].append({"who": "未评分", "score": it["score"], "reason": ev})
        elif it["column"] in ("content_realization", "functions_playability"):
            for who, key in (("回放验证", "replay"), ("试玩验证", "play")):
                v = (ev or {}).get(key) or {}
                r["judges"].append({"who": who, "score": v.get("score"), "reason": v.get("reason"), "evidence": v.get("evidence")})
            r["cites"] = cited(ev, E / "functions", md, tid)
            if (ev or {}).get("arbiter"): r["judges"].append({"who": "仲裁", "score": ev["arbiter"].get("score"), "reason": ev["arbiter"].get("reason")})
        elif it["column"] == "visual_presentation":
            r["judges"].append({"who": "视觉评委", "score": (ev or {}).get("score"), "reason": (ev or {}).get("reason")})
            r["best"] = []
            for b in (ev or {}).get("best") or []:
                src = E / "visual" / b
                if src.exists(): shutil.copy(src, md / f"best_{Path(b).name}"); r["best"].append(f"media/{tid}/best_{Path(b).name}")
            r["kf"] = []                                             # the reference pictures the judge compared with (keyframes, images)
            for rel in it.get("reference_files") or []:
                f = E / "visual" / rel
                if f.exists(): shutil.copy(f, md / f"kf_{f.name}"); r["kf"].append(f"media/{tid}/kf_{f.name}")
        items.append(r)
    arb = rj(E / "functions" / "arbiter.json")
    if arb:
        for a in (loose_json(arb.get("answer") or "") or {}).get("items") or []:
            for r in items:
                if r["id"] == a["id"] and not any(j["who"] == "仲裁" for j in r["judges"]):
                    r["judges"].append({"who": "仲裁", "score": a.get("score"), "reason": a.get("reason")})

    # evaluators: what they did, time, cost
    ev = []
    def rec(name, p, did):
        x = rj(p) or {}
        ev.append({"name": name, "did": did, "model": x.get("model"), "wall_s": x.get("wall_s"), "usd": x.get("usd"),
                   "tokens_out": x.get("tokens_out")})
    acts = [json.loads(l) for l in open(E / "functions/play/actions.jsonl")] if (E / "functions/play/actions.jsonl").exists() else []
    traces = sorted(p for p in (E / "functions" / "replay").iterdir() if p.is_dir()) if (E / "functions" / "replay").exists() else []
    rec("回放验证评委", E / "functions/replay/replay_verifier.json", f"看我们重放 {len(traces)} 条 agent 轨迹时截的图，按 CR / FP 条目打分")
    rec("试玩验证 agent", E / "functions/play/play_verifier.json", f"自己用玩家输入玩了 {len(acts)} 个动作（先盲玩，再解锁 ACCESS / 轨迹 / 建议），按 CR / FP 条目打分")
    if arb: rec("仲裁评委", E / "functions/arbiter.json", "两个验证器分数不同的条目：看双方理由和证据后定分")
    rec("视觉评委", E / "visual/visual_judge.json", f"从 {(rj(E / 'visual/visual.json') or {}).get('n_candidates')} 张候选截图里找最能体现每条 VP 的图，和参考关键帧 / 参考图片 / 参考文字段落对比打分")
    gp = rj(E / "regression/gate_probes.json") or {}
    ev.append({"name": "Gate 探针（稳定运行 + 原游戏仍在）", "did": f"原版基线 vs 装 mod 实例，{len(gp.get('checks') or [])} 项："
               + "；".join(f"{c['id']} {'通过' if c['ok'] else ('无法测试' if c['ok'] is None else '未通过')}" for c in gp.get("checks") or [])
               + "；未通过的重跑一次", "model": "probe", "wall_s": None, "usd": 0})

    from ..game.base import diff_brief
    from ..eval.functions import act_text
    tls = [{"trace": tr, "rows": [{"shot": e.get("screenshot"), "step": e.get("step"), "input": act_text(e.get("actions")),
                                  "diff": diff_brief(e.get("diff"), 600)} for e in tl]} for tr, tl in timelines(E / "functions")[0].items()]

    # videos
    vids = []
    v = slideshow([a["shot"] for a in acts if a.get("shot")], md / "play.mp4",
                  [f"#{a['n']} {a['t']} {a['cmd']} {' '.join(map(str, a.get('args') or []))}" for a in acts if a.get("shot")])
    if v: vids.append({"title": f"试玩验证 agent 的全部 {len(acts)} 个动作（每个动作后的截图，1.6 秒/张）", "src": f"media/{tid}/play.mp4"})
    for p in traces:
        v = slideshow(sorted(p.glob("*.png")), md / f"replay_{p.name}.mp4")
        if v: vids.append({"title": f"回放 agent 轨迹 {p.name}（我们重放时截的图）", "src": f"media/{tid}/replay_{p.name}.mp4"})
    devs = sorted((d / "work/.gab/shots").glob("*.png"), key=lambda f: f.stat().st_mtime)
    v = slideshow(devs, md / "dev.mp4")
    if v: vids.append({"title": f"开发 agent 自己开游戏测试时的截图（{len(devs)} 张，按时间顺序）", "src": f"media/{tid}/dev.mp4"})
    for k, ref in enumerate(sorted((d / "work/reference").glob("clip_*.mp4"))):
        shutil.copy(ref, md / f"reference_{k + 1:02d}.mp4"); vids.insert(k, {"title": f"参考视频片段 {k + 1}（agent 拿到的原片）", "src": f"media/{tid}/reference_{k + 1:02d}.mp4"})
    ref_imgs = []
    for f in sorted((d / "work/reference").glob("img*.jpg")):
        shutil.copy(f, md / f"refimg_{f.name}"); ref_imgs.append(f"media/{tid}/refimg_{f.name}")
    rt = d / "work/reference/text_01.md"

    ag = agent_summary(d / "agent/transcript.jsonl"); ag.update({k: res["cost"]["agent"].get(k) for k in ("usd", "wall_s", "exit_reason")})
    deliv = d / "deliverable"
    return {"id": tid, "run": run_name, "note": notes.get(f"trial:{tid}", ""), "task": task, "level": level, "game": (yaml.safe_load((d / "work/task/task.yaml").read_text()) or {}).get("target_game", ""),
            "model": res["trial"].get("model"), "agent_name": res["trial"].get("agent"),
            "status": res["status"], "gate": res["gate"], "scores": res["scores"], "explicit": res.get("explicit_requirements"),
            "cost": res["cost"], "timestamps": res.get("timestamps"), "items": items, "evaluators": ev, "videos": vids, "agent": ag, "timelines": tls,
            "reference_images": ref_imgs, "reference_text": rt.read_text(errors="replace")[:20000] if rt.exists() else "",
            "gate_log": (f"GATE {res['gate']['fail']}: {res['gate'].get('reason')}" if res["gate"].get("fail") in ("unstable", "host_removed")
                         else Path(res["gate"]["log"]).read_text(errors="replace")[:8000] if res["gate"].get("log") and not res["gate"].get("pass")
                         and Path(res["gate"]["log"]).exists() else ""),
            "instruction": (d / "work/task" / f"instruction_{level}.md").read_text() if (d / "work/task" / f"instruction_{level}.md").exists() else ""}


def translate(T, out):
    """every English reason / item text -> Chinese (Sonnet, one call per trial). Cache zh_cache.json is keyed by the English text,
    so a changed source text is translated again; returns {trial id|key: Chinese}"""
    cache = out / "zh_cache.json"; C = rj(cache) or {}; C = C if C.get("_by_text") else {"_by_text": True}
    zh = {}
    for t in T:
        src = {}
        for r in t["items"]:
            if r.get("item"): src[f"{r['id']}|item"] = r["item"]
            for k, a in (r.get("anchors") or {}).items(): src[f"{r['id']}|a{k}"] = a
            for j, x in enumerate(r["judges"]):
                if x.get("reason"): src[f"{r['id']}|j{j}"] = x["reason"]
        if t["agent"].get("final"): src["agent_final"] = t["agent"]["final"][:3000]
        for _ in range(3):
            todo = {k: v for k, v in src.items() if v not in C}
            if not todo: break
            ans, _r = claude("Translate every value of this JSON object into natural simplified Chinese (keep ids, game names, proper names and "
                             "numbers; keep it concise). Reply with ONLY the JSON object with the same keys.\n\n" + json.dumps(todo, ensure_ascii=False),
                             out / "_judge", f"zh_{t['id']}", timeout=900)
            for k, v in (loose_json(ans) or {}).items():
                if k in todo: C[todo[k]] = v
        zh.update({f"{t['id']}|{k}": C[v] for k, v in src.items() if v in C})
        cache.write_text(json.dumps(C, ensure_ascii=False, indent=1))
    return zh


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--name", required=True, help="run name(s), comma-separated: one page")
    ap.add_argument("--rubrics")
    ap.add_argument("--serve", type=int); a = ap.parse_args()
    names = a.name.split(","); out = config.path("runs_dir") / (f"{names[0]}/_review" if len(names) == 1 else "_review")
    out.mkdir(parents=True, exist_ok=True)
    T = [trial(Path(p).parent, out, a.rubrics) for n in names for p in sorted(glob.glob(str(config.path("runs_dir") / n / "*" / "*" / "result.json")))
         if Path(p).parent.name == "fine"]                               # not set-aside runs (fine.invalid-...)
    zh = translate(T, out)
    (out / "data.json").write_text(json.dumps({"run": a.name, "trials": T, "zh": zh}, ensure_ascii=False))
    shutil.copy(Path(__file__).with_name("trial_review.html"), out / "index.html")
    print(f"wrote {out}/index.html ({len(T)} trials)")
    if a.serve:
        subprocess.run(["python3", "-m", "http.server", str(a.serve), "--bind", "127.0.0.1", "--directory", str(out)])


if __name__ == "__main__":
    main()
