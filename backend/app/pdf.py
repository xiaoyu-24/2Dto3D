import hashlib
from io import BytesIO

from pypdf import PdfReader


def extract_pdf(body: bytes):
    reader = PdfReader(BytesIO(body))
    if reader.is_encrypted:
        raise ValueError("PDF 已加密，请上传未加密的原图")
    if not reader.pages:
        raise ValueError("PDF 没有页面")
    pages = []
    for number, page in enumerate(reader.pages, 1):
        pages.append({"number": number, "rotation": page.rotation,
                      "media_box": [float(n) for n in page.mediabox],
                      "crop_box": [float(n) for n in page.cropbox],
                      "user_unit": page.user_unit, "text": page.extract_text()})
    return {"schema": "pdf-extract-v1", "source_sha256": hashlib.sha256(body).hexdigest(),
            "page_count": len(pages), "coordinate_unit": "PDF point × user_unit",
            "pages": pages, "next_step": "vision_read"}
