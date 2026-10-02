from fastapi.testclient import TestClient

from app.analysis.onboarding import OnboardingAnswers, suggest
from app.main import app


def _s(r, h, e):
    return suggest(OnboardingAnswers(risk_tolerance=r, time_horizon=h, experience=e))


def test_default_answers_give_one_percent_swing():
    s = _s("medium", "weeks", "intermediate")
    assert s.risk_pct == 1.0 and s.style == "swing"


def test_beginner_is_lowered_and_floored():
    assert _s("medium", "weeks", "beginner").risk_pct == 0.5
    assert _s("low", "weeks", "beginner").risk_pct == 0.25


def test_advanced_bonus_only_with_high_tolerance():
    assert _s("high", "weeks", "advanced").risk_pct == 2.0
    assert _s("medium", "weeks", "advanced").risk_pct == 1.0


def test_horizon_maps_to_style():
    assert _s("medium", "days", "intermediate").style == "short swing"
    assert _s("medium", "months", "intermediate").style == "position"


def test_endpoint_suggests_without_changing_settings():
    client = TestClient(app)
    before = client.get("/api/settings").json()["default_risk_pct"]
    r = client.post(
        "/api/settings/onboarding-suggestion",
        json={"risk_tolerance": "low", "time_horizon": "months", "experience": "beginner"},
    )
    assert r.status_code == 200 and r.json()["risk_pct"] == 0.25
    assert client.get("/api/settings").json()["default_risk_pct"] == before
    bad = client.post(
        "/api/settings/onboarding-suggestion",
        json={"risk_tolerance": "x", "time_horizon": "days", "experience": "beginner"},
    )
    assert bad.status_code == 422
