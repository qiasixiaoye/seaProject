"""统一 LLM 客户端。

优先使用 langchain-openai ChatOpenAI 接入 DeepSeek；
没有 DEEPSEEK_API_KEY 或 import 失败时降级到旧的 deepseek_client。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

log = logging.getLogger("geo_agent.llm")

# ── Token 计费追踪 ──────────────────────────────────────────────────────────
_usage_accumulator: dict[str, int] = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}

PRICE_PER_1K_INPUT = 0.00014   # DeepSeek-chat USD per 1K input tokens
PRICE_PER_1K_OUTPUT = 0.00028  # DeepSeek-chat USD per 1K output tokens


def reset_usage() -> None:
    _usage_accumulator.update(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def get_usage() -> dict[str, Any]:
    u = dict(_usage_accumulator)
    u["estimated_cost_usd"] = round(
        u["prompt_tokens"] / 1000 * PRICE_PER_1K_INPUT
        + u["completion_tokens"] / 1000 * PRICE_PER_1K_OUTPUT,
        6,
    )
    return u


def _record_usage(response_or_dict: Any) -> None:
    """从 LangChain AIMessage 或 usage dict 中提取 token 用量。"""
    try:
        if hasattr(response_or_dict, "usage_metadata"):
            meta = response_or_dict.usage_metadata or {}
            _usage_accumulator["prompt_tokens"] += int(meta.get("input_tokens", 0))
            _usage_accumulator["completion_tokens"] += int(meta.get("output_tokens", 0))
            _usage_accumulator["total_tokens"] += int(meta.get("total_tokens", 0))
        elif isinstance(response_or_dict, dict):
            u = response_or_dict.get("usage", {}) or {}
            _usage_accumulator["prompt_tokens"] += int(u.get("prompt_tokens", 0))
            _usage_accumulator["completion_tokens"] += int(u.get("completion_tokens", 0))
            _usage_accumulator["total_tokens"] += int(u.get("total_tokens", 0))
    except Exception:
        pass


# ── LLM 工厂 ───────────────────────────────────────────────────────────────
def _make_langchain_llm(temperature: float = 0.3, timeout: float | None = None):
    """构建 LangChain ChatOpenAI 实例（指向 DeepSeek）。

    timeout 给评估裁判等"宁可降级也不能卡死"的场景用：超时即抛错，由调用方回退。
    """
    from langchain_openai import ChatOpenAI  # type: ignore

    kwargs: dict[str, Any] = dict(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") + "/v1",
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        temperature=temperature,
        max_retries=2,
    )
    if timeout is not None:
        kwargs["timeout"] = timeout
        kwargs["max_retries"] = 0   # 演示场景：超时不重试，直接降级
    return ChatOpenAI(**kwargs)


def configured() -> bool:
    return bool(os.getenv("DEEPSEEK_API_KEY", "").strip())


# ── 通用 chat 接口 ─────────────────────────────────────────────────────────
def chat(messages: list[dict[str, str]], temperature: float = 0.3) -> str:
    """发送对话，返回文本；失败时抛出异常。"""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        lc_messages = []
        for m in messages:
            role, content = m["role"], m["content"]
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))
        llm = _make_langchain_llm(temperature)
        resp = llm.invoke(lc_messages)
        _record_usage(resp)
        return resp.content
    except ImportError:
        # Fallback: 直接用旧客户端
        from ocean_agents_demo import deepseek_client  # type: ignore
        return deepseek_client.chat(messages)


def chat_json(messages: list[dict[str, str]], temperature: float = 0.1,
              timeout: float | None = None) -> Any:
    """发送对话，强制返回 JSON（dict/list）。timeout 用于评估裁判等防卡死场景。"""
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        lc_messages = []
        for m in messages:
            role, content = m["role"], m["content"]
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))
        llm = _make_langchain_llm(temperature, timeout=timeout).bind(
            response_format={"type": "json_object"}
        )
        resp = llm.invoke(lc_messages)
        _record_usage(resp)
        return json.loads(resp.content)
    except ImportError:
        from ocean_agents_demo import deepseek_client  # type: ignore
        return deepseek_client.chat_json(messages)


def chat_stream(messages: list[dict[str, str]], temperature: float = 0.3):
    """流式 chat，逐个 yield token 字符串。"""
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
        from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

        lc_messages = []
        for m in messages:
            role, content = m["role"], m["content"]
            if role == "system":
                lc_messages.append(SystemMessage(content=content))
            else:
                lc_messages.append(HumanMessage(content=content))
        llm = _make_langchain_llm(temperature)
        for chunk in llm.stream(lc_messages):
            if chunk.content:
                yield chunk.content
    except ImportError:
        # 无 LangChain 时退化为一次性返回
        result = chat(messages, temperature)
        yield result


def run_tool_loop(
    goal: str,
    tools: list[dict[str, Any]],
    dispatch_fn,
    system: str = "",
    max_steps: int = 5,
) -> dict[str, Any]:
    """ReAct 工具循环（LangChain Tool Calling 版）。

    优先用 LangChain bind_tools；失败时回退旧客户端实现。
    """
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
        from langchain_core.messages import (  # type: ignore
            AIMessage, HumanMessage, SystemMessage, ToolMessage,
        )

        llm = _make_langchain_llm(0.1)
        # 将 JSON Schema tool specs 转成 LangChain 格式
        lc_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {}),
                },
            }
            for t in tools
        ]
        llm_with_tools = llm.bind_tools(lc_tools)

        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=goal))

        steps = []
        for _ in range(max_steps):
            resp = llm_with_tools.invoke(messages)
            _record_usage(resp)
            messages.append(resp)

            tool_calls = getattr(resp, "tool_calls", []) or []
            if not tool_calls:
                steps.append({"action": "done", "content": resp.content})
                break

            for tc in tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"] if isinstance(tc["args"], dict) else {}
                steps.append({"action": "call_tool", "tool": tool_name, "args": tool_args})
                try:
                    result = dispatch_fn(tool_name, tool_args)
                except Exception as exc:
                    result = {"error": str(exc)}
                messages.append(
                    ToolMessage(
                        content=json.dumps(result, ensure_ascii=False)[:2000],
                        tool_call_id=tc["id"],
                    )
                )
                steps.append({"action": "tool_result", "tool": tool_name, "result": result})

        return {"steps": steps, "messages_count": len(messages)}

    except (ImportError, Exception) as exc:
        log.warning("LangChain tool loop failed, falling back to legacy: %s", exc)
        from ocean_agents_demo import deepseek_client  # type: ignore
        return deepseek_client.run_tool_loop(goal, tools, dispatch_fn, system=system, max_steps=max_steps)


def extract_json(text: str) -> Any:
    """从可能含有多余内容的字符串中提取 JSON。"""
    # 先尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 找第一个 { 或 [
    m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}
