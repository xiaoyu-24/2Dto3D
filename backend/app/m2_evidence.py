import struct
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


def transform(page, dpi=144):
    left, bottom, right, top = page["crop_box"]
    scale = dpi / 72
    matrices = {
        0: [scale, 0, 0, -scale, -left * scale, top * scale],
        90: [0, scale, scale, 0, -bottom * scale, -left * scale],
        180: [-scale, 0, 0, scale, right * scale, -bottom * scale],
        270: [0, -scale, -scale, 0, top * scale, right * scale],
    }
    return matrices[page["rotation"] % 360]


def pdf_rect(frame, rect):
    horizontal_x, vertical_x, horizontal_y, vertical_y, offset_x, offset_y = frame["pdf_to_pixels"]
    determinant = horizontal_x * vertical_y - vertical_x * horizontal_y
    left, top, width, height = rect
    points = [((vertical_y * (image_x - offset_x) - horizontal_y * (image_y - offset_y)) / determinant,
               (-vertical_x * (image_x - offset_x) + horizontal_x * (image_y - offset_y)) / determinant)
              for image_x in (left, left + width) for image_y in (top, top + height)]
    return [min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points)]


def render(source, checkpoint, regions=None, frames=None):
    dpi = 180
    render_pages = frames is None
    images, located = {}, {}
    if render_pages:
        frames = []
    with TemporaryDirectory() as directory:
        path = Path(directory, "source.pdf")
        path.write_bytes(source)

        def png(number, rect=None):
            args = ["pdftoppm", "-f", str(number), "-l", str(number), "-cropbox", "-singlefile", "-r", str(dpi), "-png"]
            if rect:
                args += [str(value) for pair in zip(("-x", "-y", "-W", "-H"), rect) for value in pair]
            return subprocess.run([*args, str(path)], check=True, capture_output=True, timeout=30).stdout

        for page in checkpoint["pages"] if render_pages else []:
            number = page["number"]
            image = png(number)
            width, height = struct.unpack(">II", image[16:24])
            frames.append({**page, "text": page.get("text", "")[:2000], "width": width, "height": height, "dpi": dpi,
                           "pdf_to_pixels": transform(page, dpi), "image_key": f"page-{number}"})
            images[f"page-{number}"] = image
        for key, region in (regions or {}).items():
            frame = frames[region["page"] - 1]
            min_x, min_y, max_x, max_y = region["rect"]
            if not (0 <= min_x < max_x <= 1 and 0 <= min_y < max_y <= 1):
                raise ValueError("证据选区超出原页")
            left, top = int(min_x * frame["width"]), int(min_y * frame["height"])
            right, bottom = min(frame["width"], round(max_x * frame["width"])), min(frame["height"], round(max_y * frame["height"]))
            rect = [left, top, max(1, right - left), max(1, bottom - top)]
            images[key] = png(region["page"], rect)
            located[key] = {"page": region["page"], "view": region["label"], "pixel_rect": rect,
                            "pdf_rect": pdf_rect(frame, rect), "source_sha256": checkpoint["source_sha256"]}
    return {"source_sha256": checkpoint["source_sha256"], "pages": frames, "regions": located}, images
