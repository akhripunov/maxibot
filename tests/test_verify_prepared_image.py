from scripts.verify_prepared_image import inspect_mismatches


def test_prepared_image_inspect_accepts_exact_platform_id_and_source_labels():
    sha = "a" * 40
    assert inspect_mismatches(f"linux/amd64|sha256:config|{sha}|{sha}|{sha}",
                              "sha256:config", sha) == []


def test_prepared_image_inspect_reports_each_mismatched_field():
    sha = "a" * 40
    mismatches = inspect_mismatches(f"linux/arm64|sha256:other|old|{sha}|{sha}",
                                    "sha256:config", sha)
    assert len(mismatches) == 3
    assert "platform expected='linux/amd64' actual='linux/arm64'" in mismatches
    assert "image_id expected='sha256:config' actual='sha256:other'" in mismatches
    assert "revision_label expected=" in mismatches[2]


def test_prepared_image_inspect_rejects_malformed_output():
    assert inspect_mismatches("linux/amd64|truncated", "sha256:config", "a" * 40) == [
        "inspect_field_count expected=5 actual=2"
    ]
