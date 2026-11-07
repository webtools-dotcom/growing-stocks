"""Crash-safe data files. A plain write empties the file first, so a sleep, power cut or killed update midway
leaves it blank or full of NUL bytes and the app crashes on the next read. Write to a temp file, then swap it in."""
import json
import os
import time
from pathlib import Path


def _swap(tmp, path):
    for _ in range(10):  # Windows refuses the swap while a reader has the file open: wait a moment
        try:
            return os.replace(tmp, path)
        except PermissionError:
            time.sleep(.2)
    os.replace(tmp, path)


def save_text(path, text):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    _swap(tmp, path)


def save_json(path, data, **kw):
    save_text(path, json.dumps(data, **kw))


def save_csv(df, path, **kw):
    """Use as df.pipe(save_csv, path, index=False)."""
    save_text(path, df.to_csv(**kw))


def read_json(path, default=None):
    """-> parsed file, or `default` if it is missing, empty or damaged (it gets rebuilt on the next update)."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


if __name__ == "__main__":
    import tempfile
    p = Path(tempfile.mkdtemp()) / "x.json"
    assert read_json(p, {}) == {}
    p.write_bytes(b"\0" * 155)                      # the damage a crash mid-write leaves behind
    assert read_json(p) is None
    save_json(p, {"a": 1})
    assert read_json(p) == {"a": 1} and not p.with_name("x.json.tmp").exists()
    print("safeio ok")
