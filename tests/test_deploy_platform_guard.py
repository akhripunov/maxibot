import pytest

from scripts.deploy_platform_guard import validate


def test_amd64_image_on_amd64_vps_passes():
    validate("amd64", "amd64", "a" * 40, "a" * 40)


@pytest.mark.parametrize(
    ("vps_arch", "image_arch", "source_sha", "expected_sha"),
    [
        ("amd64", "arm64", "a" * 40, "a" * 40),
        ("arm64", "amd64", "a" * 40, "a" * 40),
        ("UNKNOWN", "amd64", "a" * 40, "a" * 40),
        ("amd64", "UNKNOWN", "a" * 40, "a" * 40),
        ("amd64", "amd64", "UNKNOWN", "a" * 40),
        ("amd64", "amd64", "b" * 40, "a" * 40),
    ],
)
def test_guard_blocks_mismatch_unknown_and_sha_failure(vps_arch, image_arch, source_sha, expected_sha):
    with pytest.raises(RuntimeError):
        validate(vps_arch, image_arch, source_sha, expected_sha)
