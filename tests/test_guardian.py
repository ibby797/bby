"""Tests for the read-only Security Guardian auditor.

These run on any platform: they exercise the pure-logic pieces (scoring,
rendering, hosts-file parsing, port parsing, JSON shape) without depending on
the host's actual security configuration.
"""

import json
import sys
from pathlib import Path

import pytest

# guardian.py lives in the sibling `security/` package, not under quantbot.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "security"))

import guardian  # noqa: E402
from guardian import Result, Status  # noqa: E402


def test_run_missing_binary_is_safe():
    rc, out, err = guardian.run(["definitely-not-a-real-binary-xyz"])
    assert rc == 127
    assert out == ""
    assert "not found" in err


def test_score_math():
    results = [
        Result("a", Status.PASS),
        Result("b", Status.WARN),
        Result("c", Status.FAIL),
        Result("d", Status.MANUAL),  # excluded
        Result("e", Status.INFO),    # excluded
    ]
    pct, n_pass, n_warn, n_fail = guardian.score(results)
    # scored values: 1.0 + 0.5 + 0.0 over 3 checks = 50%
    assert pct == pytest.approx(50.0)
    assert (n_pass, n_warn, n_fail) == (1, 1, 1)


def test_score_empty_is_zero_not_crash():
    pct, n_pass, n_warn, n_fail = guardian.score([])
    assert pct == 0.0
    assert (n_pass, n_warn, n_fail) == (0, 0, 0)


def test_hosts_file_flags_hijack(tmp_path, monkeypatch):
    fake = tmp_path / "hosts"
    fake.write_text(
        "127.0.0.1 localhost\n"
        "# a comment\n"
        "203.0.113.5 www.google.com\n"  # sensitive domain -> non-local IP
    )
    monkeypatch.setattr(guardian.platform, "system", lambda: "Linux")
    orig_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: orig_open(fake, *a, **k))
    results = guardian.check_hosts_file()
    assert len(results) == 1
    assert results[0].status is Status.FAIL
    assert "google" in results[0].detail.lower()


def test_hosts_file_local_redirects_are_ok(tmp_path, monkeypatch):
    fake = tmp_path / "hosts"
    fake.write_text(
        "127.0.0.1 localhost\n"
        "0.0.0.0 ads.example.com\n"       # local-null = ad blocking, fine
        "127.0.0.1 www.facebook.com\n"    # self-block, fine
    )
    monkeypatch.setattr(guardian.platform, "system", lambda: "Linux")
    orig_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: orig_open(fake, *a, **k))
    results = guardian.check_hosts_file()
    assert results[0].status is Status.PASS


def test_gather_and_json_shape():
    results = guardian.gather()
    assert results, "gather() should always return checks"
    # advisory (habit) checks are always present regardless of platform
    names = {r.name for r in results}
    assert "Backups (3-2-1 rule)" in names
    assert "Multi-factor authentication (MFA)" in names

    # JSON path must be serializable and well-formed
    pct, *_ = guardian.score(results)
    payload = {
        "score_pct": round(pct, 1),
        "results": [r.to_dict() for r in results],
    }
    text = json.dumps(payload)
    reloaded = json.loads(text)
    assert reloaded["results"][0]["status"] in {s.value for s in Status}


def test_render_runs_without_color():
    results = guardian.gather()
    text = guardian.render(results, use_color=False)
    assert "SECURITY GUARDIAN" in text
    assert "Posture score" in text
    # no ANSI escape codes when color disabled
    assert "\033[" not in text


def test_section_filter():
    results = guardian.gather(sections={"Habits (verify yourself)"})
    assert results
    assert all(r.section == "Habits (verify yourself)" for r in results)


def test_main_json_exit_code(capsys):
    code = guardian.main(["--json"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "score_pct" in data
    # exit code is 0 (no fail) or 1 (some fail) — never anything else
    assert code in (0, 1)
    has_fail = any(r["status"] == "FAIL" for r in data["results"])
    assert code == (1 if has_fail else 0)
