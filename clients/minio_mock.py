# clients/minio_mock.py
# Local filesystem mock for MinIO — used when no MinIO server is available.
# Stores objects as files under /tmp/football_bronze_mock/<bucket>/<object>
# Drop-in replacement for clients/minio_client.py in local dev mode.

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

MOCK_ROOT = Path("/tmp/football_bronze_mock")


def _path(bucket: str, object_name: str) -> Path:
    p = MOCK_ROOT / bucket / object_name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def upload_json(bucket: str, object_name: str, data) -> bool:
    try:
        p = _path(bucket, object_name)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info("[MOCK] Saved s3://%s/%s (%d bytes)", bucket, object_name, p.stat().st_size)
        return True
    except Exception as e:
        logger.error("[MOCK] Upload failed: %s", e)
        return False


def read_latest_json(bucket: str, prefix: str):
    try:
        base = MOCK_ROOT / bucket / prefix.rstrip("/")
        if not base.exists():
            return None
        # Find all json files under prefix, pick latest by name
        files = sorted(base.rglob("*.json"), key=lambda f: f.name, reverse=True)
        if not files:
            return None
        data = json.loads(files[0].read_text())
        logger.info("[MOCK] Read s3://%s/%s", bucket, files[0].relative_to(MOCK_ROOT / bucket))
        return data
    except Exception as e:
        logger.error("[MOCK] Read failed: %s", e)
        return None


def read_latest_csv(bucket: str, prefix: str):
    try:
        base = MOCK_ROOT / bucket / prefix.rstrip("/")
        files = sorted(base.rglob("*.csv"), key=lambda f: f.name, reverse=True)
        if not files:
            return None
        return files[0].read_text()
    except Exception as e:
        logger.error("[MOCK] CSV read failed: %s", e)
        return None
