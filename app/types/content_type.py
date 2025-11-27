from enum import Enum


class ContentType(str, Enum):
    """
    Accepted `content_type` for files
    """

    jpg = "image/jpeg"
    png = "image/png"
    webp = "image/webp"
    pdf = "application/pdf"


class PillowImageFormat(str, Enum):
    """
    Accepted image formats for Pillow
    """

    jpg = "JPEG"
    png = "PNG"
    webp = "WEBP"
