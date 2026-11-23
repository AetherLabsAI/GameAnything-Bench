"""the critic's robustness (gab/refine/refine.py critic, 2026-11-12 Valheim: empty answers once many multi-MB contact sheets were in
its context): images shrunk by its key proxy only, a bounded number of continuations, then a FRESH finish session; no model calls"""
import base64, io, json
from pathlib import Path

from gab.refine import refine
from gab.agent.base import AgentRun
from gab.agent.until_done import UntilDone
from gab.host import keyproxy
from gab.host.keyproxy import CRITIC_SHRINK, KeyProxy, shrink_images


def _png_data_url(w=1920, h=810):
    import random
    from PIL import Image
    rnd = random.Random(0)
    im = Image.frombytes("RGB", (w, h), bytes(rnd.getrandbits(8) for _ in range(w * h * 3)))   # noise: a big PNG, like a 3D game
    b = io.BytesIO(); im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def _body(url):
    return json.dumps({"model": "m", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": url}}]}]}).encode()


def test_critic_shrink_applies_to_small_requests_only_with_its_options():
    url = _png_data_url(); body = _body(url)
    assert len(body) < 16_000_000
    same, info = shrink_images(body)                       # the agents' default: a request under SHRINK_ABOVE is untouched
    assert same is body and info[0] == 0
    out, (n, before, after) = shrink_images(body, **CRITIC_SHRINK)
    assert n == 1 and after < before / 2
    u = json.loads(out)["messages"][0]["content"][1]["image_url"]["url"]
    assert u.startswith("data:image/jpeg;base64,")
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(u.split(",", 1)[1])))
    assert max(im.size) <= CRITIC_SHRINK["side"]
    again, (n2, _, _) = shrink_images(body, **CRITIC_SHRINK)   # cached: the same bytes on every request (prompt cache)
    assert n2 == 1 and again == out


def test_small_images_and_text_requests_are_untouched():
    small = _png_data_url(64, 64); body = _body(small)
    out, info = shrink_images(body, **CRITIC_SHRINK)
    assert out is body and info[0] == 0
    txt = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    assert shrink_images(txt, **CRITIC_SHRINK)[0] is txt


def test_keyproxy_keeps_shrink_options(tmp_path):
    p = KeyProxy(tmp_path / "s.sock", "https://api.example.com/v1", key="k", shrink=CRITIC_SHRINK)
    assert p.shrink == CRITIC_SHRINK
    assert KeyProxy(tmp_path / "t.sock", "https://api.example.com/v1", key="k").shrink == {}


class Inner:
    """a fake in-container harness: `script(prompt, opts)` -> what the segment does in the work dir"""
    def __init__(self, script):
        self.model = "fake"; self.opts = {}; self.can_resume = True; self.calls = []; self.script = script
    def session_of(self, p): return "ses_" + str(len([c for c in self.calls if not c["resume"]]))
    def run(self, workdir, prompt, budget, tools):
        self.calls.append({"prompt": prompt, "resume": self.opts.get("resume")})
        tc = self.script(Path(workdir), prompt, self.opts)
        t = Path(workdir).parent / "agent" / "transcript.jsonl"; t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(json.dumps({"type": "step_finish", "n": len(self.calls)}) + "\n")
        return AgentRun(model="fake", exit_reason="done", wall_s=1.0, usd=0.01, tokens_in=10, tokens_out=1, transcript=str(t),
                        extra={"tool_calls": tc})


def test_until_done_max_continuations(tmp_path):
    inner = Inner(lambda w, p, o: 1)                       # reads an image every segment, never finishes (the Valheim pattern)
    r = UntilDone(inner, marker="REVIEW_DONE", message="go on", tag="critic", max_continuations=2).run(
        tmp_path, "brief", __import__("gab.agent.base", fromlist=["Budget"]).Budget(minutes=30), {})
    assert len(inner.calls) == 3 and r.extra["continuations_exhausted"] and r.extra["continuations"] == 2


class FakeTask:
    id, game = "T-IO-test", "stardew_valley"
    mdir = Path("/nonexistent")
    def instruction(self, level): return "Make sowing like in the pictures."
    def references(self, kind=None): return []


def _setup(tmp_path, monkeypatch, script):
    from gab.agent import base as abase, incontainer
    inner = Inner(script); seen = {}
    monkeypatch.setattr(abase, "get", lambda *a, **k: object())
    def wrap(A, seat, run_dir, max_usd=None, spent_usd=0.0, proxy_shrink=None):
        seen.setdefault("shrink", []).append(proxy_shrink); seen.setdefault("run_dirs", []).append(Path(run_dir)); return inner
    monkeypatch.setattr(incontainer, "wrap", wrap)
    monkeypatch.setattr(incontainer, "bridge_dir", lambda d: Path(tmp_path) / "bridge")
    monkeypatch.setattr(refine, "copy_targets", lambda task, w: 0)
    d = tmp_path / "trial"; (d / "deliverable" / "shots").mkdir(parents=True); (d / "deliverable" / "ACCESS.md").write_text("a")
    rr = d / "refine" / "r0"; (rr / "replay").mkdir(parents=True)
    return inner, seen, d, rr


def _write_review(w):
    (w / "feedback.md").write_text("Coverage\n..."); (w / "scores.json").write_text(json.dumps({"reference_likeness": 2}))
    (w / "REVIEW_DONE").write_text("")


def test_critic_fresh_finish_session_after_empty_answers(tmp_path, monkeypatch):
    def script(w, prompt, opts):
        if "NOTE: this review was started by an earlier session" in prompt:
            _write_review(w); return 3
        (w / "frames").mkdir(exist_ok=True); (w / "frames" / f"sheet_{len(list((w / 'frames').iterdir()))}.jpg").write_bytes(b"x")
        (w / "scores.json").write_text('{"reference_likeness": "in progress"}')   # a placeholder draft
        return 1                                                                # read an image, then an empty answer
    inner, seen, d, rr = _setup(tmp_path, monkeypatch, script)
    sc = refine.critic(d, FakeTask(), "fine", seat=None, round_dir=rr, replay_dir=rr / "replay")
    assert sc == {"reference_likeness": 2}
    assert seen["shrink"] == [CRITIC_SHRINK] and len(seen["run_dirs"]) == 1          # no full retry needed
    main = inner.calls[:1 + refine.CRITIC_MAX_CONTINUATIONS]; fin = inner.calls[1 + refine.CRITIC_MAX_CONTINUATIONS:]
    assert main[0]["resume"] is None and all(c["resume"] for c in main[1:])         # the review session was continued ...
    assert len(fin) == 1 and fin[0]["resume"] is None                                # ... the finish session is a fresh one
    p = fin[0]["prompt"]
    assert p.startswith(main[0]["prompt"])                                           # the same brief (same rubric)
    assert "frames/sheet_0.jpg" in p and "draft_scores.json" in p and f"at most {refine.CRITIC_FINISH_IMAGES} images" in p
    cj = json.loads((rr / "critic" / "critic.json").read_text())
    assert cj["complete"] and cj["finish_session"]["complete"] and cj["image_shrink"] == CRITIC_SHRINK
    assert cj["brief"] == refine.CRITIC_BRIEF_VERSION
    tr = (rr / "critic" / "transcript.jsonl").read_text().splitlines()
    assert len(tr) == 4                                       # the 3 review segments + the finish session, joined
    assert (rr / "critic" / "frames" / "sheet_0.jpg").exists()


def test_critic_retry_is_a_fresh_review_when_finish_fails(tmp_path, monkeypatch):
    n = {"finish": 0}
    def script(w, prompt, opts):
        if "NOTE: this review was started" in prompt:
            n["finish"] += 1
            if n["finish"] == 2: _write_review(w); return 2
        return 1
    inner, seen, d, rr = _setup(tmp_path, monkeypatch, script)
    sc = refine.critic(d, FakeTask(), "fine", seat=None, round_dir=rr, replay_dir=rr / "replay")
    assert sc == {"reference_likeness": 2} and n["finish"] == 2
    assert len(seen["run_dirs"]) == 2 and seen["run_dirs"][0] != seen["run_dirs"][1]   # the retry: a new run dir and HOME
    starts = [c for c in inner.calls if c["resume"] is None]
    assert len(starts) == 4                                    # review, finish, retry review, retry finish: each a fresh session
    assert len(list(rr.glob("critic_incomplete_*"))) == 1


def test_critic_brief_asks_for_compact_jpg_images():
    b = refine.CRITIC_BRIEF
    assert "frames/sheet_1_x.jpg" in b and "at most 1600 px" in b and "never more" in b
