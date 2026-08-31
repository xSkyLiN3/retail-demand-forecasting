from __future__ import annotations

import argparse
import json
import ssl
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if not __debug__:
    raise RuntimeError("The deployment smoke gate requires Python assertions to be enabled.")


def request(base_url: str, path: str, *, method: str = "GET"):
    url = f"{base_url.rstrip('/')}{path}"
    return urlopen(
        Request(url, method=method, headers={"Accept": "application/json, text/html;q=0.9"}),
        timeout=15,
        context=ssl.create_default_context(),
    )


def load_json(base_url: str, path: str) -> tuple[dict[str, object], object]:
    with request(base_url, path) as response:
        assert response.status == 200, (path, response.status)
        return json.load(response), response.headers


def verify(base_url: str) -> None:
    health, _ = load_json(base_url, "/health")
    assert health == {"status": "ok"}, health

    with request(base_url, "/") as response:
        dashboard = response.read().decode("utf-8")
        headers = response.headers
    assert "NO-GO for operational use" in dashboard
    assert "77.02%" in dashboard and "85% minimum" in dashboard
    assert '<link rel="stylesheet" href="/assets/dashboard.css">' in dashboard
    assert '<script defer src="/assets/dashboard.js"></script>' in dashboard
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"

    for asset in ("/assets/dashboard.css", "/assets/dashboard.js"):
        with request(base_url, asset) as response:
            assert response.status == 200
            assert len(response.read()) > 1_000

    forecasts, _ = load_json(base_url, "/api/forecasts?limit=2000")
    monitoring, _ = load_json(base_url, "/api/monitoring?limit=2000")
    assert forecasts["count"] == len(forecasts["items"]) == 1_680
    assert monitoring["count"] == len(monitoring["items"]) == 1_680

    first = forecasts["items"][0]
    selected = urlencode({"run_id": first["run_id"], "sku": first["sku"], "limit": 2000})
    selected_forecasts, _ = load_json(base_url, f"/api/forecasts?{selected}")
    selected_monitoring, _ = load_json(base_url, f"/api/monitoring?{selected}")
    assert selected_forecasts["count"] == 14
    assert selected_monitoring["count"] == 14

    for hidden_path in ("/docs", "/openapi.json"):
        try:
            request(base_url, hidden_path)
        except HTTPError as exc:
            assert exc.code == 404, (hidden_path, exc.code)
        else:
            raise AssertionError(f"Public route should be disabled: {hidden_path}")

    try:
        request(base_url, "/api/forecasts", method="POST")
    except HTTPError as exc:
        assert exc.code in {403, 405}, exc.code
    else:
        raise AssertionError("Public API unexpectedly accepted POST")

    if base_url.lower().startswith("https://"):
        with request(base_url, "/") as response:
            assert response.headers["Strict-Transport-Security"].startswith("max-age=")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the hardened public retail demo.")
    parser.add_argument("base_url")
    args = parser.parse_args()
    verify(args.base_url)
    print(f"Public demo smoke passed: {args.base_url}")


if __name__ == "__main__":
    main()
