from pathlib import Path


def test_required_qa_artifacts_are_present():
    root = Path(__file__).parents[2]
    for relative in ("QA_ROLE.md", "QA_PROCESS.md", "qa/TEST_CASES.md", "RELEASE_STATUS.md"):
        assert (root / relative).exists()
