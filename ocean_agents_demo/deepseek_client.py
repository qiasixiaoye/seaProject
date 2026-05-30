from __future__ import annotations

import json
import re
import os
from typing import Any, Callable
from urllib.request import Request, urlopen


def configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def status() -> dict[str, Any]:
    return {
        "configured": configured(),
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
    }


def chat(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    max_tokens: int = 1600,
    json_mode: bool = False,
) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        # DeepSeek (OpenAI-compatible) structured output. Lets the IntentAgent /
        # ScreeningAgent / CriticAgent return machine-parseable decisions.
        payload["response_format"] = {"type": "json_object"}
    req = Request(
        base_url + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=45) as res:
        data = json.loads(res.read().decode("utf-8") or "{}")
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"empty LLM response: {data}")
    return choices[0].get("message", {}).get("content", "").strip()


def chat_json(
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_tokens: int = 1400,
) -> Any:
    """Call the model expecting a JSON object and parse it robustly.

    Raises RuntimeError if the response cannot be parsed as JSON so callers can
    fall back to a deterministic heuristic path.
    """
    raw = chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
    return parse_json(raw)


def parse_json(raw: str) -> Any:
    """Best-effort extraction of a JSON object/array from an LLM reply."""
    text = (raw or "").strip()
    if not text:
        raise RuntimeError("empty JSON response")
    # Strip ```json ... ``` / ``` ... ``` code fences if the model added them.
    fence = re.match(r"^```(?:json)?\s*(.+?)\s*```$", text, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced {...} or [...] span.
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(1))
    raise RuntimeError(f"could not parse JSON from LLM response: {text[:200]}")


def run_tool_loop(
    goal: str,
    tool_specs: list[dict[str, Any]],
    dispatch: "Callable[[str, dict[str, Any]], Any]",
    system: str | None = None,
    max_steps: int = 5,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """ReAct-style tool loop.

    The model is given the tool catalog and, at each step, returns a JSON object
    deciding whether to call a tool or finish. Tool results are fed back as
    observations until the model emits ``action="final"`` or ``max_steps`` is hit.
    Returns ``{"steps": [...]}`` recording every tool call + observation.
    """
    catalog = "\n".join(
        f"- {s['name']}({', '.join((s.get('parameters', {}).get('properties', {})).keys())}): {s['description']}"
        for s in tool_specs
    )
    sys = (system or "你是一个会使用工具收集信息的助手。") + (
        "\n可用工具：\n" + catalog +
        "\n每一步只输出一个 JSON 对象。"
        "\n调用工具：{\"thought\":\"...\",\"action\":\"call_tool\",\"tool\":\"工具名\",\"args\":{...}}。"
        "\n信息足够时：{\"thought\":\"...\",\"action\":\"final\"}。只输出 JSON，不要多余文字。"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": sys},
        {"role": "user", "content": goal},
    ]
    steps: list[dict[str, Any]] = []
    for _ in range(max(1, max_steps)):
        try:
            data = chat_json(messages, temperature=temperature)
        except Exception as exc:
            steps.append({"action": "error", "error": f"{type(exc).__name__}: {exc}"})
            break
        action = str((data or {}).get("action", "")).lower()
        if action == "call_tool":
            tool = str(data.get("tool", ""))
            args = data.get("args") or {}
            try:
                observation = dispatch(tool, args)
                ok = True
            except Exception as exc:
                observation = {"error": f"{type(exc).__name__}: {exc}"}
                ok = False
            steps.append({"action": "call_tool", "tool": tool, "args": args, "ok": ok, "observation": observation})
            messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})
            messages.append({"role": "user", "content": "工具结果：" + json.dumps(observation, ensure_ascii=False)[:2000]})
            continue
        steps.append({"action": "final", "thought": str((data or {}).get("thought", ""))})
        break
    return {"steps": steps}
