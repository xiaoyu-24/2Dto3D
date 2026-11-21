import hashlib
import json


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_bytes())


def write_json(path, value):
    path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).encode())
