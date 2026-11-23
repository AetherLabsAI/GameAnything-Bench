"""OpenCode (`opencode run --format json`, headless) — the generic route for ANY OpenAI-compatible endpoint (OpenRouter, vLLM,
SGLang, DeepSeek, ...). The endpoint is a provider `gab` (@ai-sdk/openai-compatible) whose apiKey is the config reference
`{env:<api-key-env>}`: the key value lives only in the child process environment. A fresh config / data / state dir per run
(XDG_*), `--pure` (no external plugins), web tools denied, no access outside the workdir (external_directory deny).
The model's price (gab/agent/prices.yaml) is declared in the provider config so OpenCode reports USD per step.

    gab run --agent opencode --base-url https://openrouter.ai/api/v1 --api-key-env OPENROUTER_API_KEY --model qwen/qwen3.8-27b
"""
import json, os, shutil
from pathlib import Path

import yaml

from gab import config
from gab.agent._common import CLIAdapter
from gab.agent.base import PRICES


class Adapter(CLIAdapter):
    name = "opencode"
    binary = "opencode"
    can_resume = True

    def __init__(self, model=None, **opts):
        opts.setdefault("base_url", "https://openrouter.ai/api/v1")
        opts.setdefault("api_key_env", "OPENROUTER_API_KEY")
        super().__init__(model=model, **opts)

    def command(self, workdir: Path, instruction: str, home: Path):
        o = self.opts
        oc = home / "opencode"
        for d in ("config", "data", "state"): (oc / d).mkdir(parents=True, exist_ok=True)
        # the cache inside the agent home (bind-mounted into the trial container, which has no network): seeded with the
        # models.dev list and a static ripgrep (its grep / glob tools would download rg), model list fetch off
        cache = Path(o.get("cache_dir") or oc / "cache"); (cache / "opencode" / "bin").mkdir(parents=True, exist_ok=True)
        seed = Path(config.get("opencode_cache_seed") or Path.home() / ".cache" / "gab-opencode") / "opencode" / "models.json"
        if seed.exists() and not (cache / "opencode" / "models.json").exists(): shutil.copy(seed, cache / "opencode" / "models.json")
        rg = config.get("ripgrep_binary") or shutil.which("rg")
        if rg and Path(rg).exists() and not (cache / "opencode" / "bin" / "rg").exists():
            shutil.copy(rg, cache / "opencode" / "bin" / "rg"); os.chmod(cache / "opencode" / "bin" / "rg", 0o755)
        m = {"name": self.model, "attachment": True, "tool_call": True, "reasoning": True,
             "modalities": {"input": ["text", "image"], "output": ["text"]},
             "limit": {"context": int(o.get("context", 262144)), "output": int(o.get("max_output", 32768))}}
        prov = o.get("providers") or (config.get("openrouter_providers") or {}).get(self.model)
        if prov and "openrouter.ai" in o["base_url"]:   # pinned providers, no fallback (a provider once returned a Qwen tool
            # call as plain text, so OpenCode ended the run): official / full-precision first, recorded in the result
            m["options"] = {"provider": {"order": list(prov), "allow_fallbacks": False, "require_parameters": True}}
        self._providers = prov
        p = ((yaml.safe_load(PRICES.read_text()) or {}).get("models", {}).get(self.model) if PRICES.exists() else None)
        if p: m["cost"] = {"input": p["input"], "output": p["output"], "cache_read": p.get("cached_input", p["input"])}
        cfg = {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled",
               "model": f"gab/{self.model}", "small_model": f"gab/{self.model}",
               "provider": {"gab": {"npm": "@ai-sdk/openai-compatible", "name": "gab",
                                    "options": {"baseURL": o["base_url"], "apiKey": "{env:%s}" % o["api_key_env"],
                                                "includeUsage": True},
                                    "models": {self.model: m}}},
               "enabled_providers": ["gab"],
               "permission": {"edit": "allow", "bash": "allow", "webfetch": "deny", "websearch": "deny",
                              "external_directory": "deny", "doom_loop": "allow"},
               "mcp": {}, "lsp": False, "formatter": False}
        if o.get("reasoning_effort"):
            m.setdefault("options", {})["reasoningEffort"] = o["reasoning_effort"]
        cf = oc / "opencode.json"; cf.write_text(json.dumps(cfg, indent=1))
        extra = {"OPENCODE_CONFIG": str(cf), "XDG_CONFIG_HOME": str(oc / "config"), "XDG_DATA_HOME": str(oc / "data"),
                 "XDG_STATE_HOME": str(oc / "state"), "XDG_CACHE_HOME": str(cache), "OPENCODE_DISABLE_AUTOUPDATE": "1",
                 "OPENCODE_DISABLE_LSP_DOWNLOAD": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_CLAUDE_CODE": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1"}
        cmd = [self.bin(), "run", "--format", "json", "--pure", "--auto", "--dir", str(workdir), "-m", f"gab/{self.model}"]
        # the reasoning effort goes into the model's provider options: `--variant` alone does not reach a custom
        # openai-compatible provider (checked: the request carried no reasoning_effort)
        if o.get("resume"): cmd += ["--session", o["resume"]]
        return cmd + ["--", instruction], extra

    def usage(self, events, run):
        tin = tout = tcache = 0; usd = 0.0; steps = 0
        for e in events:
            part = e.get("part") or {}
            if e.get("type") == "step_finish" or part.get("type") == "step-finish":
                t = part.get("tokens") or {}; steps += 1
                tin += t.get("input", 0); tout += (t.get("output", 0) or 0) + (t.get("reasoning", 0) or 0)
                tcache += (t.get("cache") or {}).get("read", 0) or 0
                usd += part.get("cost", 0) or 0
            if e.get("type") == "error": run.extra.setdefault("errors", []).append(json.dumps(e)[:800])
        run.tokens_in, run.tokens_out, run.tokens_cached = tin, tout, tcache
        run.usd = round(usd, 4) if usd else None
        run.extra["steps"] = steps
        if getattr(self, "_providers", None): run.extra["providers"] = list(self._providers)
        run.extra["tool_calls"] = sum(1 for e in events if e.get("type") == "tool_use" or (e.get("part") or {}).get("type") == "tool")
