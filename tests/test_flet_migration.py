import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app
from app.flet_app.config import API_BASE_URL
from app.flet_app.api_client import CareerPulseApiClient

client = TestClient(app)

def test_api_health():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert data["service"] == "CareerPulse API"

def test_supabase_health():
    res = client.get("/api/supabase/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "connected"

def test_flet_gui_mounted():
    res = client.get("/gui/", follow_redirects=True)
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

def test_app_serves_spa():
    res = client.get("/app", follow_redirects=False)
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

def test_root_serves_landing():
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")

def test_gui_param_redirects():
    res = client.get("/app?gui=true", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/gui/"

def test_unauthenticated_profile_endpoint():
    res = client.get("/api/user/profile")
    assert res.status_code == 200
    data = res.json()
    assert data["authenticated"] is False
    assert "profile" in data

def test_unauthenticated_saved_opportunities():
    res = client.get("/api/user/opportunities/saved")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "saved" in data

def test_digest_status_requires_auth():
    res = client.get("/api/digest/status")
    # Endpoint is protected by require_auth_token_and_user
    assert res.status_code == 401
    assert "detail" in res.json()

def test_flet_api_client():
    client_inst = CareerPulseApiClient("http://testserver")
    assert client_inst.base_url == "http://testserver"
    client_inst.set_token("sample_token")
    headers = client_inst._get_headers()
    assert headers["Authorization"] == "Bearer sample_token"

if __name__ == "__main__":
    tests = [
        test_api_health,
        test_supabase_health,
        test_flet_gui_mounted,
        test_app_serves_spa,
        test_root_serves_landing,
        test_gui_param_redirects,
        test_unauthenticated_profile_endpoint,
        test_unauthenticated_saved_opportunities,
        test_digest_status_requires_auth,
        test_flet_api_client,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\nResult: {passed}/{len(tests)} tests passed.")
