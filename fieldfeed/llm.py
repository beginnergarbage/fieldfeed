"""Bring your own model, or your own harness.

    llm:
      provider: anthropic          # anthropic | openai | xai | openrouter | groq | gemini
                                   # | deepseek | mistral | ollama | openai-compatible | command
      fast_model: claude-haiku-5-5 # triage pass: sees every item, scores relevance
      smart_model: claude-opus-5-5 # writing pass: sees the top stories only
      # base_url: https://...      # openai-compatible only
      # api_key_env: MY_KEY        # override the env var the key is read from
      # command: claude -p         # provider: command. Prompt on stdin, reply on stdout.
      # fast_command: claude -p --model haiku   # optional per-tier commands

The model never fetches anything. It gets a prompt and returns text, which is
why any agent CLI (Claude Code, Codex, a local script) can stand in for an API.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time

import httpx

PRESETS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "openai-compatible": (None, "LLM_API_KEY"),
}

ANTHROPIC_DEFAULTS = {"fast": "claude-haiku-5-5", "smart": "claude-opus-5-5"}
# Models that accept server-side refusal fallbacks. Biology and security feeds
# touch topics that trip an occasional false-positive decline; the fallback
# re-runs the request on a model that can answer.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.provider = self.cfg.get("provider", "anthropic")
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.enabled, self.reason = self._check()

    # setup -----------------------------------------------------------------

    def _check(self) -> tuple[bool, str]:
        p = self.provider
        if self.cfg.get("enabled") is False or p == "none":
            return False, "disabled in field.yaml"
        if p == "command":
            if not self.cfg.get("command"):
                return False, "provider is `command` but no `command` is set"
            return True, f"command: {self.cfg['command']}"
        if p == "anthropic":
            env = self.cfg.get("api_key_env", "ANTHROPIC_API_KEY")
            if not (os.environ.get(env) or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                return False, f"{env} is not set"
            return True, f"anthropic: {self.model('fast')} / {self.model('smart')}"
        if p not in PRESETS:
            return False, f"unknown provider {p!r}"
        base, env = PRESETS[p]
        env = self.cfg.get("api_key_env", env)
        if not (self.cfg.get("base_url") or base):
            return False, "openai-compatible provider needs `base_url`"
        if env and not os.environ.get(env):
            return False, f"{env} is not set"
        if not (self.cfg.get("fast_model") and self.cfg.get("smart_model")):
            return False, f"set fast_model and smart_model for provider {p}"
        return True, f"{p}: {self.model('fast')} / {self.model('smart')}"

    def model(self, tier: str) -> str:
        m = self.cfg.get(f"{tier}_model")
        if not m and self.provider == "anthropic":
            m = ANTHROPIC_DEFAULTS[tier]
        return m or ""

    def describe(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "fast_model": self.model("fast") if self.provider != "command" else "",
            "smart_model": self.model("smart") if self.provider != "command" else "",
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    # calls -----------------------------------------------------------------

    def complete(self, system: str, prompt: str, tier: str = "fast", max_tokens: int = 8000) -> str:
        if not self.enabled:
            raise LLMError(self.reason)
        self.calls += 1
        if self.provider == "command":
            return self._command(system, prompt, tier)
        if self.provider == "anthropic":
            return self._anthropic(system, prompt, tier, max_tokens)
        return self._openai(system, prompt, tier)

    def complete_json(self, system: str, prompt: str, tier: str = "fast", max_tokens: int = 8000):
        text = self.complete(system, prompt + "\n\nReply with the JSON object only.", tier, max_tokens)
        return extract_json(text)

    def _anthropic(self, system: str, prompt: str, tier: str, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic(
            api_key=os.environ.get(self.cfg.get("api_key_env", "ANTHROPIC_API_KEY")) or None,
            max_retries=3,
            timeout=600,
        )
        model = self.model(tier)
        extra = {"output_config": {"effort": self.cfg.get(f"{tier}_effort", "low" if tier == "fast" else "medium")}}
        kwargs = dict(
            model=model,
            max_tokens=max(max_tokens, 16000),
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            if model in FALLBACK_MODELS:
                extra["fallbacks"] = "default"
                resp = client.beta.messages.create(
                    **kwargs, betas=["server-side-fallback-2026-07-01"], extra_body=extra
                )
            else:
                resp = client.messages.create(**kwargs, extra_body=extra)
        except anthropic.AuthenticationError as e:
            raise LLMError(f"Anthropic rejected the API key: {e.message}") from e
        except anthropic.NotFoundError as e:
            raise LLMError(f"unknown model {model!r}: {e.message}") from e
        except anthropic.RateLimitError as e:
            raise LLMError(f"rate limited by Anthropic: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError(f"could not reach the Anthropic API: {e}") from e
        if resp.usage:
            self.input_tokens += resp.usage.input_tokens or 0
            self.output_tokens += resp.usage.output_tokens or 0
        if resp.stop_reason == "refusal":
            raise LLMError("the model declined this batch")
        return "".join(b.text for b in resp.content if b.type == "text")

    def _openai(self, system: str, prompt: str, tier: str) -> str:
        base, env = PRESETS[self.provider]
        base = (self.cfg.get("base_url") or base).rstrip("/")
        key = os.environ.get(self.cfg.get("api_key_env", env) or "", "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        body = {
            "model": self.model(tier),
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        for attempt in range(4):
            r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=600)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt * 3)
                continue
            if r.status_code != 200:
                raise LLMError(f"{self.provider} returned {r.status_code}: {r.text[:300]}")
            data = r.json()
            usage = data.get("usage") or {}
            self.input_tokens += usage.get("prompt_tokens", 0)
            self.output_tokens += usage.get("completion_tokens", 0)
            return data["choices"][0]["message"]["content"] or ""
        raise LLMError(f"{self.provider} kept failing")

    def _command(self, system: str, prompt: str, tier: str) -> str:
        full = f"{system}\n\n{prompt}"
        command = self.cfg.get(f"{tier}_command") or self.cfg["command"]
        try:
            proc = subprocess.run(
                shlex.split(command), input=full, capture_output=True, text=True,
                timeout=int(self.cfg.get("timeout", 900)),
            )
        except subprocess.TimeoutExpired as e:
            raise LLMError("harness command timed out") from e
        if proc.returncode != 0:
            raise LLMError(f"harness command exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        return proc.stdout


def extract_json(text: str):
    """Pull the first JSON object out of a model reply, fences and chatter included."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise LLMError(f"no JSON object in reply: {text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"reply was not valid JSON: {e}") from e
