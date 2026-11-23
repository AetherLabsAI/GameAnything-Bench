#!/usr/bin/env python3
"""Inside a trial container: `gen-image "PROMPT" OUT.png [--aspect-ratio 16:9] [--resolution 2K] [--n 2]` asks an
image-generation model for an image and writes it to OUT.png (inside the agent's own work dir). Like `game` / `transcribe`, no
key is ever here: the call goes to the host's generation router (images.sock, a loopback forwarder, gab/host/keyproxy.py) over
http://127.0.0.1:$GAB_IMAGE_PORT/images; the host adds the credential and picks the model (config image_model).
--ref IMG (repeatable) gives reference images: the model redraws / edits them (e.g. turn a reference frame into game-style art,
keep a character's look across sprites). Prints one JSON line: {"ok": true, "path": ..., "extra_paths": [...], "cost": <usd or null>} or {"ok": false, "error": ...}.
On the HOST (an agent outside any container, gab/agent/hostgen.py) the same script talks to the router's unix socket
directly: GAB_IMAGE_SOCK, set by the tool's own wrapper (never in the agent's environment)."""
import argparse, base64, json, os, sys, urllib.error, urllib.request

DEFAULT_MODEL = None                     # the host router uses its configured model


def post_json(path, data, timeout):
    """POST data to the image router -> the answer's JSON; an HTTP error raises urllib.error.HTTPError either way.
    GAB_IMAGE_SOCK: the router's unix socket (host agent); else http://127.0.0.1:$GAB_IMAGE_PORT (container forwarder)"""
    sock = os.environ.get("GAB_IMAGE_SOCK")
    if sock:
        import http.client, io, socket
        class C(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.sock.settimeout(timeout); self.sock.connect(sock)
        c = C("localhost", timeout=timeout)
        c.request("POST", path, body=data, headers={"Content-Type": "application/json"})
        r = c.getresponse(); b = r.read(); c.close()
        if r.status >= 400: raise urllib.error.HTTPError(path, r.status, r.reason, r.headers, io.BytesIO(b))
        return json.loads(b)
    req = urllib.request.Request(f"http://127.0.0.1:{os.environ['GAB_IMAGE_PORT']}{path}", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt"); ap.add_argument("out")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--aspect-ratio", default=None)
    ap.add_argument("--resolution", default=None)
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--ref", action="append", default=[], help="reference image (repeatable, up to 4): edit it / keep its content")
    a = ap.parse_args()

    def fail(msg):
        print(json.dumps({"ok": False, "error": msg})); sys.exit(1)

    if not (os.environ.get("GAB_IMAGE_PORT") or os.environ.get("GAB_IMAGE_SOCK")): fail("image generation is not available this run (no image key configured)")
    body = {"model": a.model, "prompt": a.prompt, "n": a.n}
    if a.aspect_ratio: body["aspect_ratio"] = a.aspect_ratio
    if a.resolution: body["resolution"] = a.resolution
    if a.ref:                                     # sent as data URIs; the host's online model edits / follows them
        imgs = []
        for r in a.ref[:4]:
            try: raw = open(r, "rb").read()
            except OSError as e: fail(f"--ref {r}: {e}")
            if len(raw) > 12 * 2**20: fail(f"--ref {r}: larger than 12 MB")
            mime = "image/jpeg" if r.lower().endswith((".jpg", ".jpeg")) else "image/webp" if r.lower().endswith(".webp") else "image/png"
            imgs.append(f"data:{mime};base64," + base64.b64encode(raw).decode())
        body["images"] = imgs
    # the proxy forwards only <upstream base path>/images (e.g. /api/v1/images for OpenRouter); GAB_IMAGE_BASE is that path
    base = os.environ.get("GAB_IMAGE_BASE", "").rstrip("/")
    try:
        d = post_json(f"{base}/images", json.dumps(body).encode(), 600)       # a local GPU server queues requests
    except urllib.error.HTTPError as e:
        fail(e.read().decode(errors="replace")[:1000])
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
    imgs = d.get("data") or []
    if not imgs: fail(json.dumps(d.get("error")) if d.get("error") else "no image in the answer")
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(imgs[0].get("media_type"), "")
    out = a.out if not ext or a.out.lower().endswith(ext) else a.out + ext
    with open(out, "wb") as f: f.write(base64.b64decode(imgs[0]["b64_json"]))
    extra = []
    stem, dot, tail = out.rpartition(".")
    for i, im in enumerate(imgs[1:], 1):
        p = f"{stem or out}_{i}{dot}{tail}" if dot else f"{out}_{i}"
        with open(p, "wb") as f: f.write(base64.b64decode(im["b64_json"]))
        extra.append(p)
    print(json.dumps({"ok": True, "path": out, "extra_paths": extra, "cost": (d.get("usage") or {}).get("cost")}))


if __name__ == "__main__":
    main()
