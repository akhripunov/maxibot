def test_runtime_smoke_imports():
    from app.main import app

    assert app.title == "MAXIBOT MVP"
