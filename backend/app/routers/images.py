from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import current_user
from ..services import storage

router = APIRouter(prefix="/api/images", tags=["images"], dependencies=[Depends(current_user)])
_MAX = 20 * 1024 * 1024
_TYPES = {"image/png", "image/jpeg"}


@router.post("", status_code=201)
async def upload(file: UploadFile):
    if file.content_type not in _TYPES:
        raise HTTPException(415, "Only PNG/JPEG accepted")
    data = await file.read(_MAX + 1)
    if len(data) > _MAX:
        raise HTTPException(413, "File too large")
    ext = "png" if file.content_type == "image/png" else "jpg"
    key = f"{uuid4().hex}.{ext}"
    storage.put_image(key, data, file.content_type)
    return {"image_key": key}


@router.get("/{key}")
def download(key: str):
    if "/" in key or ".." in key:
        raise HTTPException(400, "Bad key")
    try:
        data = storage.get_image(key)
    except Exception:
        raise HTTPException(404, "Image not found")
    return Response(data, media_type="image/png" if key.endswith(".png") else "image/jpeg")
