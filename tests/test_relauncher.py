from __future__ import annotations

from pathlib import Path

from yt_downloader.relauncher import run_relauncher


def test_run_relauncher_invokes_target(monkeypatch, tmp_path) -> None:
    target = tmp_path / "app.exe"
    target.write_text("")

    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class _Proc:  # pragma: no cover - behavior doesn't matter
            pass

        return _Proc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    exit_code = run_relauncher(target, ["--flag"], wait_seconds=0.0)

    assert exit_code == 0
    assert captured["cmd"] == [str(target), "--flag"]
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == str(Path(target).parent)
    assert kwargs["close_fds"] is True
