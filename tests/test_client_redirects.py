from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codata.client import DataFlowClient, ProviderError
from codata.config import Settings


class RedirectTests(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
            host="https://dataflowserver.org",
            token="test-token",
            dataflow_allowed_hosts=("dataflowserver.org",),
        )

    def test_follows_same_host_redirect(self) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path == "/user/callremain/count":
                return httpx.Response(301, headers={"location": "/user/callremain/count/"})
            return httpx.Response(200, json={"code": 0, "data": {"remaining_calls": 9470}})

        client = DataFlowClient(self._settings())
        client._client.close()
        client._client = httpx.Client(base_url="https://dataflowserver.org", transport=httpx.MockTransport(handler))
        try:
            payload = client.get("/user/callremain/count")
            self.assertEqual(payload["data"]["remaining_calls"], 9470)
            self.assertEqual(len(requests), 2)
            self.assertTrue(requests[1].endswith("/user/callremain/count/"))
        finally:
            client.close()

    def test_rejects_absolute_initial_url(self) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, json={"code": 0})

        client = DataFlowClient(self._settings())
        client._client.close()
        client._client = httpx.Client(
            base_url="https://dataflowserver.org", transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ProviderError):
                client.get("https://evil.example/steal")
            with self.assertRaises(ProviderError):
                client.get("//evil.example/steal")
            self.assertEqual(requests, [])
        finally:
            client.close()

    def test_rejects_redirect_to_different_port(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                301, headers={"location": "https://dataflowserver.org:8443/token"}
            )

        client = DataFlowClient(self._settings())
        client._client.close()
        client._client = httpx.Client(
            base_url="https://dataflowserver.org", transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(ProviderError):
                client.get("/user/callremain/count")
        finally:
            client.close()

    def test_rejects_cross_host_redirect(self) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(301, headers={"location": "https://evil.example/token"})

        client = DataFlowClient(self._settings())
        client._client.close()
        client._client = httpx.Client(base_url="https://dataflowserver.org", transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(ProviderError):
                client.get("/user/callremain/count")
            self.assertEqual(len(requests), 1)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
