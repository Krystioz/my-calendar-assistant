import json
from types import SimpleNamespace

from agent_project import MockClient, append_trace
from agent_project.llm import OpenRouterClient, image_part, is_broken_reply


def test_mock_client() -> None:
    client = MockClient([{"text": "hello", "tool_calls": [], "usage": {}}])
    assert client.complete([{"role": "user", "content": "Hi"}])["text"] == "hello"


def test_trace_redacts_secrets(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    append_trace(path, "test", {"token": "secret", "value": 7})
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["payload"] == {"token": "[REDACTED]", "value": 7}


def test_broken_reply_detection() -> None:
    assert is_broken_reply(None, [])
    assert is_broken_reply("   ", None)
    assert is_broken_reply("<|channel>thought<tool_call|>", [])
    assert not is_broken_reply("A normal answer.", [])
    assert not is_broken_reply(None, [object()])


def _response(provider: str, content: str | None, tool_calls=None, cost=0.001):
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason="stop",
            )
        ],
        model="test-model",
        provider=provider,
        usage=SimpleNamespace(model_dump=lambda: {**usage, "cost": cost}),
    )


def test_broken_reply_is_retried_on_another_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    client = OpenRouterClient(model="test-model")
    call = SimpleNamespace(
        id="call_1", function=SimpleNamespace(name="search", arguments="{}")
    )
    replies = [_response("FlakyHost", ""), _response("GoodHost", None, [call])]
    requests = []

    def create(**request):
        requests.append(request)
        return replies.pop(0)

    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = client.complete([{"role": "user", "content": "Hi"}])

    assert result["tool_calls"][0]["name"] == "search"
    assert result["provider"] == "GoodHost"
    assert result["retried_after"] == "FlakyHost"
    assert requests[1]["extra_body"]["provider"] == {"ignore": ["FlakyHost"]}
    assert result["usage"]["cost"] == 0.002  # both attempts are billed
    assert result["usage"]["prompt_tokens"] == 20
    assert requests[0]["max_tokens"] == 1024


def test_image_part(tmp_path) -> None:
    image = tmp_path / "brief.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    part = image_part(image)
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,iVBORw0KGgo")
