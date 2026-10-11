import os
import subprocess
import sys

import pytest

from fastapi_ai_assistant.security import RateLimiter

# --- Configuration ---


@pytest.mark.parametrize(
    "auth_section",
    [
        "",
        '[auth]\nUSERNAME = "admin"\nPASSWORD = ""',
        '[auth]\nUSERNAME = "admin"\nPASSWORD = "contraseña"',
    ],
    ids=["no auth section", "empty password", "non-ASCII password"],
)
def test_app_refuses_to_start_without_usable_credentials(tmp_path, auth_section):
    config = tmp_path / "config.toml"
    config.write_text(f'[app]\nROOT_PATH = ""\n{auth_section}\n', encoding="utf-8")
    # A fresh interpreter, because this process already imported the app with the test config.
    result = subprocess.run(
        [sys.executable, "-c", "import fastapi_ai_assistant.security"],
        env={**os.environ, "FASTAPI_AI_ASSISTANT_CONFIG": str(config)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "set an ASCII USERNAME and PASSWORD" in result.stderr


# --- Rate limiter ---


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_allows_up_to_the_limit_then_says_how_long_to_wait():
    clock = FakeClock()
    limiter = RateLimiter([(2, 60)], clock=clock)
    assert limiter.acquire("ana") == 0
    clock.now += 10
    assert limiter.acquire("ana") == 0
    clock.now += 10
    assert limiter.acquire("ana") == 40  # The first hit leaves the window 60 s after it.


def test_the_window_slides():
    clock = FakeClock()
    limiter = RateLimiter([(1, 60)], clock=clock)
    assert limiter.acquire("ana") == 0
    clock.now += 59.9
    assert limiter.acquire("ana") > 0
    clock.now += 0.1
    assert limiter.acquire("ana") == 0


def test_rejected_hits_do_not_count():
    clock = FakeClock()
    limiter = RateLimiter([(1, 60)], clock=clock)
    assert limiter.acquire("ana") == 0
    for _ in range(5):
        clock.now += 10
        assert limiter.acquire("ana") > 0
    clock.now += 10  # 60 s after the only accepted hit.
    assert limiter.acquire("ana") == 0


def test_every_rule_must_allow_the_hit():
    clock = FakeClock()
    limiter = RateLimiter([(5, 60), (2, 3600)], clock=clock)
    assert limiter.acquire("ana") == 0
    clock.now += 100
    assert limiter.acquire("ana") == 0
    clock.now += 100
    assert limiter.acquire("ana") == 3400  # The hourly rule blocks it, not the per-minute one.


def test_each_key_has_its_own_limit():
    limiter = RateLimiter([(1, 60)], clock=FakeClock())
    assert limiter.acquire("ana") == 0
    assert limiter.acquire("bob") == 0
    assert limiter.acquire("ana") > 0


def test_limits_must_be_positive():
    with pytest.raises(ValueError):
        RateLimiter([(0, 60)])
