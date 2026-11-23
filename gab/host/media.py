"""Host side of the media-generation tools beyond gen-image (gen-sfx, gen-music, gen-3d). The container / host tool scripts
(agent/container_files/gen_media.py) call the trial's generation router (keyproxy.ImageRouter) at <base>/media/audio and
<base>/media/3d; the router calls these functions with the credentials of gab's own environment. No key ever enters the
container, a log line or an answer.

  audio  an OpenAI-style POST <audio_base_url>/audio/speech {model, input, duration} with audio_sfx_model / audio_music_model
         (answers base64 audio or a URL that is downloaded here); off unless audio_base_url and its key are configured
  3d     Meshy (https://api.meshy.ai/openapi, key in the variable named by meshy_key_env): image-to-3d from a reference image,
         or text-to-3d preview + textured refine. GLB answered (plus the base-colour texture when there is one).
"""
import base64, json, os, time, urllib.error, urllib.request

from .. import config

MESHY = "https://api.meshy.ai/openapi"
MAX_DOWNLOAD = 80 * 2**20


def audio_route():
    """-> (base url, key, {kind: model}) of gen-sfx / gen-music, or None when not configured / no key"""
    base = str(config.get("audio_base_url") or "").rstrip("/"); env = config.get("audio_key_env") or ""
    models = {k: config.get(f"audio_{k}_model") for k in ("sfx", "music")}
    models = {k: v for k, v in models.items() if v}
    key = os.environ.get(env) if env else None
    return (base, key, models) if base and key and models else None


def meshy_key():
    env = config.get("meshy_key_env") or "MESHY_API_KEY"
    return os.environ.get(env)


def _download(url, timeout=300):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        b = r.read(MAX_DOWNLOAD + 1)
    if len(b) > MAX_DOWNLOAD: raise RuntimeError("generated file too large")
    return b


def _post_audio(base, key, obj, timeout=300):
    req = urllib.request.Request(base + "/audio/speech", data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key,
                                          "User-Agent": "gameanything-bench-gab"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()


def audio(body, post=None):
    """body: {kind: sfx|music, prompt, duration} -> {"data": [{"b64": ..., "format": "mp3"}], "duration": s}.
    post(path, obj) -> (status, bytes): a replacement transport (tests); default the configured audio endpoint"""
    r = audio_route()
    if r is None and post is None: raise ValueError("sound generation is not available this run (audio_base_url / audio_key_env not configured)")
    models = r[2] if r else {"sfx": "sfx", "music": "music"}
    kind = str(body.get("kind") or "sfx")
    if kind not in ("sfx", "music"): raise ValueError("kind must be one of ['music', 'sfx']")
    if kind not in models: raise ValueError(f"{kind} generation is not available this run (audio_{kind}_model not configured)")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt: raise ValueError("a prompt is needed")
    dur = float(body.get("duration") or (3 if kind == "sfx" else 20))
    dur = max(0.5, min(dur, 30.0 if kind == "sfx" else 120.0))
    obj = {"model": models[kind], "input": prompt[:1000], "duration": dur}
    st, b = post("/audio/speech", obj) if post else _post_audio(r[0], r[1], obj)
    if st != 200:
        try: msg = ((json.loads(b).get("error") or {}).get("message") or "")[:300]
        except Exception: msg = ""
        raise RuntimeError(f"audio generation refused: http{st} {msg}".strip())
    d = json.loads(b); a = d.get("audio") or ""
    raw = _download(a) if str(d.get("audio_type")) == "url" or a.startswith("http") else base64.b64decode(a)
    return {"data": [{"b64": base64.b64encode(raw).decode(), "format": d.get("format") or "mp3"}], "duration": d.get("duration") or dur}


def _meshy(method, path, obj=None, timeout=60):
    k = meshy_key()
    if not k: raise RuntimeError("3D generation is not configured this run")
    req = urllib.request.Request(MESHY + path, method=method, data=json.dumps(obj).encode() if obj is not None else None,
                                 headers={"Authorization": "Bearer " + k, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Meshy HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from None   # the key is in a header only


def _meshy_wait(kind, tid, timeout=1200):
    t = time.time()
    while time.time() - t < timeout:
        r = _meshy("GET", f"{kind}/{tid}")
        st = r.get("status")
        if st == "SUCCEEDED": return r
        if st in ("FAILED", "CANCELED", "EXPIRED"):
            raise RuntimeError(f"3D task {st.lower()}: {(r.get('task_error') or {}).get('message', '')}"[:300])
        time.sleep(5)
    raise RuntimeError(f"3D task did not finish in {timeout}s")


def model3d(body):
    """body: {image: data URI} (image-to-3d) or {prompt} (text-to-3d preview + textured refine); optional polycount, texture_prompt
    -> {"data": [{"b64": glb, "format": "glb"}], "texture_b64": png or None}"""
    poly = int(body.get("polycount") or 8000)
    if body.get("image"):
        req = {"image_url": str(body["image"]), "should_texture": True, "should_remesh": True, "topology": "triangle",
               "target_polycount": poly, "enable_pbr": False}
        if body.get("prompt"): req["texture_prompt"] = str(body["prompt"])[:600]
        kind = "/v1/image-to-3d"; res = _meshy_wait(kind, _meshy("POST", kind, req)["result"])
    else:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt: raise ValueError("give a reference image or a prompt")
        kind = "/v2/text-to-3d"
        pre = _meshy_wait(kind, _meshy("POST", kind, {"mode": "preview", "prompt": prompt[:600], "art_style": "realistic",
                                                       "should_remesh": True, "topology": "triangle", "target_polycount": poly})["result"])
        ref = {"mode": "refine", "preview_task_id": pre.get("id"), "enable_pbr": False}
        if body.get("texture_prompt"): ref["texture_prompt"] = str(body["texture_prompt"])[:600]
        res = _meshy_wait(kind, _meshy("POST", kind, ref)["result"])
    glb = (res.get("model_urls") or {}).get("glb")
    if not glb: raise RuntimeError("the 3D task gave no GLB")
    tex = None
    for t in res.get("texture_urls") or []:
        if t.get("base_color"):
            try: tex = base64.b64encode(_download(t["base_color"])).decode()
            except Exception: pass
            break
    return {"data": [{"b64": base64.b64encode(_download(glb)).decode(), "format": "glb"}], "texture_b64": tex}
