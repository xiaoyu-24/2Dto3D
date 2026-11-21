import json
import os
import re
import secrets
import struct
import sys
import zlib
from decimal import Decimal, InvalidOperation

WIDTH = 460
HEIGHT = 200
INK = (18, 28, 36)
GRAY = (205, 213, 216)
DIGIT_SEGMENTS = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgecd", "7": "abc", "8": "abcdefg", "9": "abfgcd",
}


class Canvas:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.pixels = bytearray([255]) * (width * height * 3)

    def pixel(self, x, y, color=INK):
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset:offset + 3] = bytes(color)

    def rect(self, x, y, width, height, color=INK):
        for py in range(max(0, y), min(self.height, y + height)):
            start = (py * self.width + max(0, x)) * 3
            end = (py * self.width + min(self.width, x + width)) * 3
            if end > start:
                self.pixels[start:end] = bytes(color) * ((end - start) // 3)

    def circle(self, cx, cy, radius, color=INK):
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                    self.pixel(x, y, color)

    def triangle(self, points, color=INK):
        (x1, y1), (x2, y2), (x3, y3) = points
        for y in range(max(0, min(y1, y2, y3)), min(self.height, max(y1, y2, y3) + 1)):
            intersections = []
            for (ax, ay), (bx, by) in (((x1, y1), (x2, y2)), ((x2, y2), (x3, y3)), ((x3, y3), (x1, y1))):
                if ay == by or y < min(ay, by) or y > max(ay, by):
                    continue
                intersections.append(ax + (y - ay) * (bx - ax) / (by - ay))
            if len(intersections) >= 2:
                for x in range(max(0, int(min(intersections))), min(self.width, int(max(intersections)) + 1)):
                    self.pixel(x, y, color)

    def png(self):
        def chunk(kind, body):
            checksum = zlib.crc32(kind + body) & 0xFFFFFFFF
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", checksum)

        rows = b"".join(
            b"\x00" + self.pixels[y * self.width * 3:(y + 1) * self.width * 3]
            for y in range(self.height)
        )
        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def draw_digit(canvas, digit, x, y):
    width, height, thickness = 28, 48, 6
    segments = {
        "a": (x + thickness, y, width - 2 * thickness, thickness),
        "g": (x + thickness, y + height // 2 - thickness // 2, width - 2 * thickness, thickness),
        "d": (x + thickness, y + height - thickness, width - 2 * thickness, thickness),
        "f": (x, y + thickness, thickness, height // 2 - thickness),
        "b": (x + width - thickness, y + thickness, thickness, height // 2 - thickness),
        "e": (x, y + height // 2, thickness, height // 2 - thickness),
        "c": (x + width - thickness, y + height // 2, thickness, height // 2 - thickness),
    }
    for segment in DIGIT_SEGMENTS[digit]:
        canvas.rect(*segments[segment])


def draw_arrow(canvas, direction):
    cx, cy = 394, 100
    if direction == "up":
        canvas.rect(cx - 5, cy - 5, 10, 48)
        canvas.triangle(((cx, cy - 42), (cx - 25, cy - 5), (cx + 25, cy - 5)))
    elif direction == "down":
        canvas.rect(cx - 5, cy - 43, 10, 48)
        canvas.triangle(((cx, cy + 42), (cx - 25, cy + 5), (cx + 25, cy + 5)))
    elif direction == "left":
        canvas.rect(cx - 5, cy - 5, 48, 10)
        canvas.triangle(((cx - 42, cy), (cx - 5, cy - 25), (cx - 5, cy + 25)))
    else:
        canvas.rect(cx - 43, cy - 5, 48, 10)
        canvas.triangle(((cx + 42, cy), (cx + 5, cy - 25), (cx + 5, cy + 25)))


def build_challenge():
    rng = secrets.SystemRandom()
    canvas = Canvas(WIDTH, HEIGHT)
    shape_types = [rng.choice(("circle", "square", "triangle")) for _ in range(6)]
    counts = {shape: shape_types.count(shape) for shape in ("circle", "square", "triangle")}
    positions = ((48, 55), (108, 55), (168, 55), (48, 135), (108, 135), (168, 135))
    for shape, (cx, cy) in zip(shape_types, positions):
        if shape == "circle":
            canvas.circle(cx, cy, 16)
        elif shape == "square":
            canvas.rect(cx - 16, cy - 16, 32, 32)
        else:
            canvas.triangle(((cx, cy - 19), (cx - 18, cy + 16), (cx + 18, cy + 16)))
    canvas.rect(220, 24, 2, 152, GRAY)
    canvas.rect(350, 24, 2, 152, GRAY)
    code = "".join(str(rng.randrange(10)) for _ in range(3))
    for index, digit in enumerate(code):
        draw_digit(canvas, digit, 258 + index * 30, 76)
    direction = rng.choice(("up", "down", "left", "right"))
    draw_arrow(canvas, direction)
    answer = {**counts, "code": code, "direction": direction}
    return canvas.png(), answer


def validate_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid PNG signature")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])[:4]
    if (width, height, bit_depth, color_type) != (WIDTH, HEIGHT, 8, 2):
        raise ValueError("unexpected PNG dimensions or color format")
    chunks = []
    offset = 8
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        chunks.append((kind, body))
        offset += 12 + length
        if kind == b"IEND":
            break
    raw = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))
    if len(raw) != height * (width * 3 + 1):
        raise ValueError("invalid PNG pixel data length")


def parse_answer(text):
    match = re.search(r"\{[^{}]*\}", text or "")
    if not match:
        raise ValueError("response did not contain a JSON object")
    value = json.loads(match.group(0))
    return {
        "circle": int(value["circle"]),
        "square": int(value["square"]),
        "triangle": int(value["triangle"]),
        "code": str(value["code"]),
        "direction": str(value["direction"]).lower(),
    }


def main():
    if "--self-test" in sys.argv:
        image, answer = build_challenge()
        validate_png(image)
        print(json.dumps({"status": "self_test_passed", "width": WIDTH, "height": HEIGHT,
                          "png_bytes": len(image), "challenge": answer}, ensure_ascii=False))
        return 0

    from m1_service import run_probe, settings

    try:
        budget = Decimal(os.environ.get("M1_VISION_BUDGET_CNY", ""))
    except InvalidOperation:
        budget = Decimal("0")
    if not settings() or not budget.is_finite() or budget < Decimal("2.0021"):
        print(json.dumps({"status": "真实读图待联调", "reason": "请配置获准的端点、模型、凭据和探针预算"},
                         ensure_ascii=False))
        return 2

    response = run_probe()
    passed = response["passed"] and bool(response["request_id"])
    print(json.dumps({**response, "passed": passed, "authorized_budget_cny": str(budget)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
