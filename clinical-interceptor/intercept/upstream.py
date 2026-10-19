"""Upstream LLM client (PLAN 5.5). OpenAI-compatible POST {base_url}/chat/completions, with a 120 s timeout.

Clients name a logical model; the routing table maps it to the provider's model name, so provider URLs and names never
reach clients. Errors never carry the upstream body: it may contain clinical text.

Open item (API.md 8.1): the prompt is sent upstream, so the provider needs a BAA before this carries real PHI.
The FHIR bundle is never sent upstream.
"""

import json

import httpx

DEFAULT_TIMEOUT_SECONDS = 120.0  # API.md 13


class UpstreamError(Exception):
    """The upstream returned an error or an unusable response."""


class UpstreamTimeout(Exception):
    """The upstream did not respond in time."""


class UpstreamClient:
    def __init__(self, base_url, api_key, model_routes, timeout=DEFAULT_TIMEOUT_SECONDS, transport=None):
        self.base_url = base_url.rstrip("/")
        self.model_routes = dict(model_routes)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    def knows(self, logical_model):
        return logical_model in self.model_routes

    def _payload(self, logical_model, messages, stream, temperature, max_tokens):
        payload = {"model": self.model_routes[logical_model], "messages": messages, "stream": stream}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    async def complete(self, logical_model, messages, temperature=None, max_tokens=None):
        """Non-streaming call. Returns {"id", "created", "text", "finish_reason", "usage"}."""
        payload = self._payload(logical_model, messages, False, temperature, max_tokens)
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException:
            raise UpstreamTimeout()
        except httpx.HTTPError:
            raise UpstreamError("transport error")
        if response.status_code >= 400:
            raise UpstreamError(f"status {response.status_code}")
        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise UpstreamError("malformed response")
        if not isinstance(text, str):
            raise UpstreamError("malformed response")
        return {"id": data.get("id"), "created": data.get("created"), "text": text,
                "finish_reason": choice.get("finish_reason"), "usage": data.get("usage")}

    async def stream(self, logical_model, messages, temperature=None, max_tokens=None):
        """Streaming call. Yields {"delta": str, "finish_reason": str or None}. Raises before the first yield on an
        HTTP error, so the caller can still return a JSON error."""
        payload = self._payload(logical_model, messages, True, temperature, max_tokens)
        request = self._client.build_request("POST", "/chat/completions", json=payload)
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException:
            raise UpstreamTimeout()
        except httpx.HTTPError:
            raise UpstreamError("transport error")
        if response.status_code >= 400:
            await response.aclose()
            raise UpstreamError(f"status {response.status_code}")
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    choice = json.loads(data)["choices"][0]
                    delta = choice.get("delta", {}).get("content") or ""
                    finish = choice.get("finish_reason")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                    raise UpstreamError("malformed stream event")
                yield {"delta": delta, "finish_reason": finish}
        except httpx.TimeoutException:
            raise UpstreamTimeout()
        except httpx.HTTPError:
            raise UpstreamError("transport error")
        finally:
            await response.aclose()

    async def aclose(self):
        await self._client.aclose()
