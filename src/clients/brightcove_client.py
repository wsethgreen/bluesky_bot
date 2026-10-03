from clients.http_client import HttpClient
from config.settings import get_settings
from models.brightcove import BrightcoveVideo


class BrightcoveClient:
    """Thin wrapper around the Brightcove Playback API (public content).

    Auth is the policy key in the ``Accept`` header — no OAuth, since this
    only reads published videos, not the CMS.
    """

    def __init__(self):
        settings = get_settings()
        policy_key = settings.brightcove_policy_key.get_secret_value()
        account_id = settings.brightcove_account_id.strip()
        if not policy_key:
            raise ValueError("BRIGHTCOVE_POLICY_KEY is not set")
        if not account_id:
            raise ValueError("BRIGHTCOVE_ACCOUNT_ID is not set")
        self.http = HttpClient()
        self.base_url = settings.brightcove_base_url
        self.account_id = account_id
        self.policy_key = policy_key

    @property
    def _headers(self) -> dict[str, str]:
        return {"Accept": f"application/json;pk={self.policy_key}"}

    async def get_video(self, video_id: int) -> BrightcoveVideo:
        """Fetch a video object (metadata + rendition sources) by ID.

        ``GET /accounts/{account}/videos/{id}`` — pick a progressive MP4
        from ``video.sources`` for downloading.
        """
        if video_id <= 0:
            raise ValueError(f"video_id must be positive, got {video_id!r}")
        response = await self.http.get(
            f"{self.base_url}/accounts/{self.account_id}/videos/{video_id}",
            headers=self._headers,
        )
        return BrightcoveVideo.model_validate(response.json())
