def test_runtime_smoke_imports(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path / "uploads"))
    from app.main import app

    assert app.title == "MAXIBOT MVP"
