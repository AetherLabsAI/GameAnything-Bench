"""Paths and backends. Order: environment (GAB_*) > gab.local.json (`gab setup`) > gab.toml ($GAB_CONFIG, ./gab.toml, ~/.config/gab/gab.toml) > defaults.
Code defaults are generic (~/.gab/...); a machine's own paths go in gab.local.json (written by `gab setup`, gitignored)."""
import os, tomllib
from pathlib import Path

PKG = Path(__file__).resolve().parent            # gab/
REPO = PKG.parent                                # gameanything-bench/
HOME = Path(os.environ.get("GAB_HOME") or Path.home() / ".gab").expanduser().absolute()   # generic default data root (absolute: docker bind mounts)

DEFAULTS = {
    # ---- benchmark material and data dirs
    "tasks_dir": str(REPO / "benchmark" / "construction" / "tasks"),      # Construction task packages (public part)
    "rubrics_dir": str(REPO / "benchmark" / "construction" / "rubrics"),  # their rubrics + regression picks (never shown to agents)
    "hidden_dir": str(HOME / "hidden"),          # optional: rubrics of your own private tasks (<id>/rubric.yaml), read before rubrics_dir
    "edit_dir": str(REPO / "benchmark" / "editing"),                       # Editing tasks, rubrics, checkpoint manifest
    "host_fidelity_dir": str(REPO / "benchmark" / "host_fidelity"),        # host-fidelity checklist + scoring anchor
    "media_dir": str(HOME / "media"),            # reference clips / texts / images (<task>/...), filled by `gab media fetch`
    "edit_store": str(HOME / "edit_checkpoints"),  # editing start checkpoints (<key>/...), filled by `gab media fetch`
    "targets_dir": str(HOME / "targets"),        # target pictures per task (generated once, shared by every run)
    "games_dir": str(HOME / "games"),            # vanilla baselines per game version
    "runs_dir": str(HOME / "runs"),              # trials and results
    "hf_dataset": "GameAnything/GameAnything-Bench",  # Hugging Face dataset with the reference media + editing checkpoints
    # ---- where games run: one docker container per trial (gab/host/docker.py, docker/images.yaml)
    "host": "docker",
    "docker": "docker",
    "docker_host": "",                           # empty = the docker client's default
    "docker_images": "", "docker_image": "", "docker_tag": "", "docker_pull": "missing", "docker_gpus": "auto",
    "docker_network": "none", "docker_shm": "1g", "docker_run_args": "", "docker_agent_uid": "2000",
    "docker_gpu_exclude": [],
    "vglrun_device": "egl",                      # VirtualGL device of the 3D games: egl (headless GPU), or an X display like :99                    # GPU indices no trial is given (e.g. ones a local model server uses)
    # Steam games (gab/host/steam.py): one Steam hub container per machine, holding your own Steam login in steam_state
    "docker_steam": "hub", "steam_state": "", "steam_hub": "gab-steam-hub", "steam_hub_network": "bridge", "steam_hub_image": "",
    "steam_require_logon": True, "steam_logon_wait_min": 240, "steam_hub_seccomp": "", "steam_hub_apparmor": "",   # hub only; "" =
    # Docker's defaults (Steam then refuses to start: it needs user namespaces) -- docs/commercial_games.md
    # commercial games you bring yourself (host paths; mounted read-only into the trial container, never in an image)
    "stardew_game_dir": "",                      # Stardew Valley 1.6.15 Linux + SMAPI 4.5.2 (docker/stardew_valley/get-game.sh)
    "stardew_sources_dir": "",                   # optional decompiled game sources (docker/stardew_valley/decompile.sh)
    "commercial_dir": str(HOME / "commercial"),  # where `gab get-game` puts commercial game files (mode 700)
    "minecraft_dir": "",                         # a .minecraft-style folder with 26.3 + fabric-26.3-0.19.5 (docker/minecraft/fetch_game.py)
    # agent harness inside the trial container (gab/agent/incontainer.py): where harness binaries are cached / taken from
    "agents_dir": str(HOME / "agents"), "codex_container_binary": "", "claude_container_binary": "",   # "" = default (codex: pinned download; claude: the host CLI)
    # ---- parallel trials (gab/run/sched.py): per-trial needs on top of the game's mem_gb, GPU sharing, what the host keeps for itself
    "trial_ram_gb": 2.0, "trial_cores": 2, "trial_cores_nogpu_3d": 4, "trials_per_gpu": 2, "host_reserve_ram_gb": 4.0, "host_reserve_cores": 2,
    "replay_auto_shots": 10, "replay_wait_shot_s": 3.0,   # replay: automatic screenshots per trace (on top of the agent's shot steps)
    # ---- agents
    # OpenRouter provider order per model, no fallback (official / full precision first; gab/agent/opencode)
    "openrouter_providers": {"qwen/qwen3.8-27b": ["alibaba", "deepinfra/bf16"], "qwen/qwen3.8-max-0902": ["alibaba"],
                             "deepseek/deepseek-v4.1-flash": ["together", "fireworks", "deepinfra/fp8"],
                             "z-ai/glm-5.3-flash": ["z-ai/fp8", "fireworks", "together"]},
    "until_done_max_idle": 3,                    # stop after this many continuations without any tool call (gab/agent/until_done.py)
    "infra_resume_max": 4,                       # resume the session after a transient model-API crash at most this often
    "infra_resume": True,                        # gab run: continue a trial stopped by a model-API infra error in its session
    "provider_costs": True,                      # in-container runs: the provider's own charge per request (<base>/generation?id=)
    # cost guards: codex compacts its context past codex_auto_compact_tokens; the host key proxy of an in-container agent
    # (gab/host/keyproxy.py) refuses a request estimated above max_request_tokens and every request once the trial / round
    # has cost its cap in USD (gab run: run_max_usd; refine round: refine_max_usd; critic review: critic_max_usd; 0 / None =
    # no cap) -> infra code request_too_large / budget_exceeded (gab/run/infra.py)
    "codex_auto_compact_tokens": 300000, "max_request_tokens": 1_000_000,
    "run_max_usd": 20.0, "refine_max_usd": 15.0, "critic_max_usd": 3.0,
    "claude": "claude",                          # Claude Code CLI on the host (judges, play verifier, claude agent)
    "opencode": "opencode",                      # OpenCode CLI on the host (its native binary also runs in the trial container)
    "ripgrep_binary": "",                        # a static rg for OpenCode in the container (default: rg on PATH)
    "opencode_cache_seed": "",                   # dir with opencode/models.json to seed each run's cache (default ~/.cache/gab-opencode)
    "transcribe_cmd": str(PKG / "tools" / "transcribe_whisper"),   # offline speech-to-text given to agents
    "transcribe_home": "",
    # ---- generation tools given to the agent (and the target pictures). Each needs your own key; a tool whose key (or base
    # URL) is missing is switched off for the run with one log line, and the agent is told it is not available.
    # gen-image + target pictures: image_api "images" = OpenRouter's images API (POST <base>/images {model, prompt, n,
    # aspect_ratio}, reference images as input_references); "generations" = an OpenAI-style POST <base>/images/generations
    # (one call per image, size WxH, reference images as an `image` list of data URIs, Seedream style). Answers are converted
    # to PNG. image_fallback_url: optional
    # loopback server speaking gen-image's own API, used when the online call is refused (no credential).
    "image_base_url": "https://openrouter.ai/api/v1", "image_key_env": "OPENROUTER_API_KEY",
    "image_model": "bytedance-seed/seedream-5-0-lite", "image_api": "images", "image_fallback_url": "",
    # gen-sfx / gen-music: an OpenAI-style POST <base>/audio/speech {model, input, duration}; off unless audio_base_url is set
    "audio_base_url": "", "audio_key_env": "", "audio_sfx_model": "", "audio_music_model": "",
    # gen-3d: Meshy (https://api.meshy.ai), key in this variable
    "meshy_key_env": "MESHY_API_KEY",
    # ---- refinement (+Ours, gab/refine/refine.py): the reviewer agent (OpenCode + a vision model, OpenAI-compatible endpoint) and
    # the experience memory (gab/refine/memory.py: one memory per --name; lesson extraction through the critic's endpoint)
    "critic_model": "google/gemini-3.8-flash", "critic_minutes": 15, "critic_retries": 3,
    "critic_base_url": "https://openrouter.ai/api/v1", "critic_key_env": "OPENROUTER_API_KEY",
    "memory": True, "memory_dir": None, "memory_budget_chars": 6000, "memory_model": None, "memory_max_lessons": 3,
    # extraction answer budget: some models count their thinking tokens in max_tokens
    "memory_max_tokens": 8000, "memory_extract_retries": 1,
    # ---- evaluation
    "judge_model": "claude-sonnet-5-5",          # every LLM judge, run through the Claude Code CLI
    "play_actions": 40, "play_minutes": 30,      # play verifier budget (Full evaluation)
    "evidence": "full",                          # full (replay verifier + play verifier + adjudication) | lite (replay verifier only)
    "eval_record": "",                           # "1" = screen-record each trace replay -> <eval>/functions/replay/<trace>.mp4
    "eval_tag": "",                              # `gab eval --tag`: writes eval@<tag>/ + result@<tag>.json instead of eval/ + result.json
    "host_fidelity_in_eval": True,               # after CR / FP / VP, judge host fidelity (P = Preservation) and put P into Overall
    # never readable by an agent running on the host (claude agent deny rules); the hidden / rubric / media dirs are always added
    "agent_deny_read": [str(Path.home() / ".claude"), str(Path.home() / ".claude.json")],
}
SITE_NOTE = "site values (paths of one machine) belong in gab.local.json (`gab setup`, gitignored), not here"


def _toml():
    lj = REPO / "gab.local.json"                 # written by `gab setup`
    if lj.exists():
        import json; return json.loads(lj.read_text())
    for p in (os.environ.get("GAB_CONFIG"), "gab.toml", REPO / "gab.toml", Path.home() / ".config/gab/gab.toml"):
        if p and Path(p).exists(): return tomllib.loads(Path(p).read_text())
    return {}


def get(key):
    env = os.environ.get("GAB_" + key.upper())
    if env is not None: v = env.split(":") if isinstance(DEFAULTS.get(key), list) else env
    else: v = _toml().get(key, DEFAULTS[key])
    if key == "agent_deny_read":                 # media: the agent gets copies
        v = sorted(set(v) | {get("hidden_dir"), get("rubrics_dir"), get("media_dir"), get("edit_dir"), get("edit_store"), get("host_fidelity_dir")})
    return v


def path(key) -> Path: return Path(get(key))


def docker_env():
    env = dict(os.environ)
    if get("docker_host"): env["DOCKER_HOST"] = get("docker_host")
    return env
