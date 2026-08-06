import json
import logging
import os
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_USE_MOCK = os.getenv("MINIO_MOCK", "0") == "1"

if _USE_MOCK:
    from clients.minio_mock import upload_json, read_latest_json, read_latest_csv
    logger.info("MinIO: using LOCAL FILESYSTEM MOCK (MINIO_MOCK=1)")
else:
    from minio import Minio
    from minio.error import S3Error

    MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT",  "localhost:9100")
    MINIO_ACCESS   = os.getenv("MINIO_ACCESS_KEY", "minioadmin2152")
    MINIO_SECRET   = os.getenv("MINIO_SECRET_KEY", "minioadmin2152")
    MINIO_SECURE   = os.getenv("MINIO_SECURE",     "false").lower() == "true"

    def get_minio_client() -> Minio:
        return Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS,
                     secret_key=MINIO_SECRET, secure=MINIO_SECURE)

    def _ensure_bucket(client: Minio, bucket: str) -> None:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("Created bucket: %s", bucket)

    def upload_json(bucket: str, object_name: str, data) -> bool:
        try:
            client  = get_minio_client()
            _ensure_bucket(client, bucket)
            payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            client.put_object(bucket, object_name, BytesIO(payload),
                              len(payload), content_type="application/json")
            logger.info("Uploaded s3://%s/%s (%d bytes)", bucket, object_name, len(payload))
            return True
        except Exception as e:
            logger.error("MinIO upload failed: %s", e)
            return False

    def read_latest_json(bucket: str, prefix: str):
        try:
            client  = get_minio_client()
            objects = sorted(
                client.list_objects(bucket, prefix=prefix, recursive=True),
                key=lambda o: o.object_name, reverse=True,
            )
            if not objects:
                return None
            resp = client.get_object(bucket, objects[0].object_name)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.error("MinIO read failed: %s", e)
            return None

    def read_latest_csv(bucket: str, prefix: str):
        try:
            client  = get_minio_client()
            objects = sorted(
                client.list_objects(bucket, prefix=prefix, recursive=True),
                key=lambda o: o.object_name, reverse=True,
            )
            if not objects:
                return None
            resp = client.get_object(bucket, objects[0].object_name)
            return resp.read().decode("utf-8")
        except Exception as e:
            logger.error("MinIO CSV read failed: %s", e)
            return None