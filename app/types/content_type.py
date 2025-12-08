from enum import Enum


class ContentType(str, Enum):
    """
    Accepted `content_type` for files
    """

    jpg = "image/jpeg"
    png = "image/png"
    webp = "image/webp"
    pdf = "application/pdf"

    @property
    def extension(self) -> str:
        """
        Get the file extension corresponding to the content type
        """
        if self == ContentType.jpg:
            return "jpg"
        if self == ContentType.png:
            return "png"
        if self == ContentType.webp:
            return "webp"
        if self == ContentType.pdf:
            return "pdf"
        raise ValueError(f"Unknown content type: {self}")


class PillowImageFormat(str, Enum):
    """
    Accepted image formats for Pillow
    """

    jpg = "JPEG"
    png = "PNG"
    webp = "WEBP"
