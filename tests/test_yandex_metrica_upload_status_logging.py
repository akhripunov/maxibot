import pytest
import httpx

from app.integrations.yandex_metrica import YandexMetricaClient


class ErrorResponseClient:
    async def request(self, method, path, **kwargs):
        return httpx.Response(
            400,
            request=httpx.Request(method, f"https://api-metrika.yandex.net{path}"),
            text="diagnostic contains private-row-marker",
        )

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_upload_status_http_error_log_omits_upload_id_and_response_body(caplog):
    client = YandexMetricaClient("test-token")
    client._client = ErrorResponseClient()

    with pytest.raises(httpx.HTTPStatusError):
        await client.get_offline_conversion_upload(123, "private-upload-marker")

    assert "HTTP_400" in caplog.text or '"http_status": 400' in caplog.text
    assert "private-upload-marker" not in caplog.text
    assert "private-row-marker" not in caplog.text
    await client.close()
