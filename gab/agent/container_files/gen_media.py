#!/usr/bin/env python3
"""Inside a trial container: sound and 3D generation, by the name it is called as (no key here: the host's image router
adds it, gab/host/media.py):

  gen-sfx   "PROMPT" OUT.mp3 [--duration S]           a sound effect (0.5-30 s)
  gen-music "PROMPT" OUT.mp3 [--duration S]           a music track (up to 120 s)
  gen-3d    OUT.glb (--image IMG | --prompt "TEXT") [--texture-prompt TEXT] [--polycount N]
                                                      a textured 3D model (GLB; the base-colour texture next to it as
                                                      OUT_texture.png when there is one). Takes several minutes.

Prints one JSON line: {"ok": true, "path": ..., ...} or {"ok": false, "error": ...}.
On the HOST (gab/agent/hostgen.py): the router's unix socket GAB_IMAGE_SOCK, set by the tool's own wrapper."""
import argparse, base64, json, os, sys, urllib.error, urllib.request


def fail(msg):
    print(json.dumps({"ok": False, "error": msg})); sys.exit(1)


def post_json(path, data, timeout):
    """POST to the image router -> the answer's JSON (HTTP errors raise urllib.error.HTTPError): GAB_IMAGE_SOCK, the router's
    unix socket (host agent), else http://127.0.0.1:$GAB_IMAGE_PORT (container forwarder)"""
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


def call(kind, body, timeout):
    if not (os.environ.get("GAB_IMAGE_PORT") or os.environ.get("GAB_IMAGE_SOCK")): fail("media generation is not available this run (no key configured)")
    base = os.environ.get("GAB_IMAGE_BASE", "").rstrip("/")
    try:
        return post_json(f"{base}/media/{kind}", json.dumps(body).encode(), timeout)
    except urllib.error.HTTPError as e:
        try: fail((json.loads(e.read()).get("error") or {}).get("message") or f"HTTP {e.code}")
        except ValueError: fail(f"HTTP {e.code}")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


def main():
    name = os.path.basename(sys.argv[0])
    ap = argparse.ArgumentParser(prog=name)
    if name in ("gen-sfx", "gen-music"):
        ap.add_argument("prompt"); ap.add_argument("out"); ap.add_argument("--duration", type=float, default=None)
        a = ap.parse_args()
        d = call("audio", {"kind": "sfx" if name == "gen-sfx" else "music", "prompt": a.prompt, "duration": a.duration}, 600)
        it = (d.get("data") or [{}])[0]
        ext = "." + (it.get("format") or "mp3")
        out = a.out if a.out.lower().endswith(ext) else a.out + ext
        with open(out, "wb") as f: f.write(base64.b64decode(it["b64"]))
        print(json.dumps({"ok": True, "path": out, "duration": d.get("duration")}))
    elif name == "gen-3d":
        ap.add_argument("out"); ap.add_argument("--image"); ap.add_argument("--prompt")
        ap.add_argument("--texture-prompt"); ap.add_argument("--polycount", type=int)
        a = ap.parse_args()
        if not (a.image or a.prompt): fail("give --image IMG or --prompt TEXT")
        body = {"prompt": a.prompt, "texture_prompt": a.texture_prompt, "polycount": a.polycount}
        if a.image:
            try: raw = open(a.image, "rb").read()
            except OSError as e: fail(f"--image: {e}")
            mime = "image/jpeg" if a.image.lower().endswith((".jpg", ".jpeg")) else "image/png"
            body["image"] = f"data:{mime};base64," + base64.b64encode(raw).decode()
        d = call("3d", body, 1800)
        out = a.out if a.out.lower().endswith(".glb") else a.out + ".glb"
        with open(out, "wb") as f: f.write(base64.b64decode(d["data"][0]["b64"]))
        res = {"ok": True, "path": out}
        if d.get("texture_b64"):
            tp = out[:-4] + "_texture.png"
            with open(tp, "wb") as f: f.write(base64.b64decode(d["texture_b64"]))
            res["texture"] = tp
        print(json.dumps(res))
    else:
        fail(f"unknown tool name {name}")


if __name__ == "__main__":
    main()
