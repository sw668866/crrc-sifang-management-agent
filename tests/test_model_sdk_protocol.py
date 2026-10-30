"""使用真实OpenAI兼容客户端＋本地HTTP假服务验证工具调用协议格式。"""

import json
import sys
import unittest
from pathlib import Path

try:
    import httpx
    from openai import OpenAI
except ImportError:
    httpx = None
    OpenAI = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_core import run_agent  # noqa: E402


@unittest.skipUnless(OpenAI is not None and httpx is not None, "需要requirements.txt中的openai/httpx")
class TestSDKProtocol(unittest.TestCase):
    def test_real_sdk_tool_roundtrip_over_mock_http(self):
        seen_requests = []

        def handler(request):
            seen_requests.append(json.loads(request.content.decode()))
            if len(seen_requests) == 1:
                message = {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_test_1", "type": "function",
                        "function": {
                            "name": "optimize_t1",
                            "arguments": json.dumps({"overrides": {"price_per_unit": {"Y": 1700}}}),
                        },
                    }],
                }
            else:
                message = {"role": "assistant", "content": "已按工具结果完成说明。"}
            return httpx.Response(200, json={
                "id": "chatcmpl-mock-1", "object": "chat.completion",
                "created": 1700000000, "model": "qwen3.7-flash",
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if len(seen_requests) == 1 else "stop"}],
            })

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport)
        client = OpenAI(api_key="not-a-real-key", base_url="https://mock.example/v1",
                        http_client=http_client)
        try:
            out = run_agent("Y降价到1700元后T1最优组合是什么？",
                            api_key="", base_url="", client=client)
        finally:
            client.close()
        self.assertEqual(out.status, "verified_tool_output")
        self.assertEqual(out.tool_records[0]["result"]["result"]["x"], 99)
        self.assertEqual(out.tool_records[0]["result"]["result"]["y"], 68)
        self.assertIn("X=99件，Y=68件", out.answer)
        self.assertEqual(len(seen_requests), 2)
        self.assertEqual(seen_requests[0]["tool_choice"], "auto")
        self.assertEqual(seen_requests[0]["enable_thinking"], False)
        self.assertEqual(seen_requests[1]["enable_thinking"], False)
        self.assertEqual(seen_requests[1]["messages"][-1]["role"], "tool")


if __name__ == "__main__":
    unittest.main()
