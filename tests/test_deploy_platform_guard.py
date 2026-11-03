import pytest

from scripts.deploy_platform_guard import normalize_architecture, validate


def test_amd64_image_on_amd64_vps_passes():
    validate("amd64", "amd64", "a" * 40, "a" * 40)


@pytest.mark.parametrize(
    ("vps_arch", "image_arch"),
    [("x86_64", "amd64"), ("amd64", "x86_64"), ("aarch64", "arm64"), ("arm64", "aarch64")],
)
def test_supported_architecture_aliases_normalize_and_pass(vps_arch, image_arch):
    validate(vps_arch, image_arch, "a" * 40, "a" * 40)


@pytest.mark.parametrize(
    ("vps_arch", "image_arch"),
    [("amd64", "arm64"), ("arm64", "amd64"), ("x86_64", "arm64"), ("aarch64", "amd64")],
)
def test_normalized_architecture_mismatch_still_fails(vps_arch, image_arch):
    with pytest.raises(RuntimeError, match="platform mismatch"):
        validate(vps_arch, image_arch, "a" * 40, "a" * 40)


def test_architecture_normalization_is_explicit():
    assert normalize_architecture(" x86_64 ") == "amd64"
    assert normalize_architecture(" AARCH64 ") == "arm64"


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
