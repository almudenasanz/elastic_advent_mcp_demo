"""Read settings at startup, never during module import."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    elasticsearch_url: str = ""
    elasticsearch_api_key: str = ""
    workshop_url: str = "http://127.0.0.1:8001"

    def __post_init__(self):
        if not (self.elasticsearch_url and self.elasticsearch_api_key):
            raise ValueError("Set ELASTICSEARCH_URL and ELASTICSEARCH_API_KEY in .env")
        url = urlsplit(self.elasticsearch_url)
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ValueError("ELASTICSEARCH_URL must be a full http:// or https:// URL")
        if ".ingest." in url.hostname:
            raise ValueError(
                "ELASTICSEARCH_URL must use the project's Elasticsearch endpoint, "
                "not the ingest endpoint or its /_es path"
            )

    @classmethod
    def from_env(cls):
        return cls(
            elasticsearch_url=os.getenv("ELASTICSEARCH_URL", "").rstrip("/"),
            elasticsearch_api_key=os.getenv("ELASTICSEARCH_API_KEY", ""),
            workshop_url=os.getenv("WORKSHOP_URL", "http://127.0.0.1:8001").rstrip("/"),
        )
