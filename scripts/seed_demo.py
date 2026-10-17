"""Add demo watches to a running app. Needs MIN_INTERVAL_MINUTES=1 (make run-demo sets it)."""
import httpx

BASE = "http://localhost:8000"
WATCHES = [
    ("Demo: product page", "http://localhost:8001/", {"watch_selectors": ["#price"]}),
    ("Demo: flaky page (should never alert)", "http://localhost:8001/flaky", {}),
    ("Demo: CAPTCHA wall (should show blocked)", "http://localhost:8001/captcha", {}),
    ("Demo: site down (should show error)", "http://localhost:8001/down", {}),
]

for name, url, extra in WATCHES:
    body = {"name": name, "url": url, "interval_minutes": 1, "sensitivity": "high", **extra}
    r = httpx.post(f"{BASE}/api/watches", json=body, timeout=10)
    print(f"{r.status_code} {name}" + ("" if r.is_success else f"  {r.text}"))
print("\nOpen http://localhost:8000, then visit http://localhost:8001/bump to trigger a real change.")
