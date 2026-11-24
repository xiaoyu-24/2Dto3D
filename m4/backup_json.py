import json
import sys

from sqlalchemy import inspect, text

sys.path.insert(0, "/app")
from app.db import engine


def export():
    inspector = inspect(engine)
    tables = []
    with engine.connect() as conn:
        for name in inspector.get_table_names():
            columns = [column["name"] for column in inspector.get_columns(name) if str(column["type"]) == "JSON"]
            if not columns:
                continue
            keys = inspector.get_pk_constraint(name)["constrained_columns"]
            if not keys:
                raise ValueError(f"JSON backup requires a primary key: {name}")
            fields = ",".join(f"`{column}`" for column in [*keys, *columns])
            records = []
            for row in conn.execute(text(f"SELECT {fields} FROM `{name}`")).mappings():
                values = {}
                for column in columns:
                    original = row[column]
                    canonical = json.dumps(json.loads(original), ensure_ascii=True, separators=(",", ":")) if original is not None else None
                    restored = conn.execute(text("SELECT CAST(:body AS JSON)"), {"body": canonical}).scalar()
                    if restored != original:
                        raise ValueError(f"JSON cannot round-trip exactly: {name}.{column}")
                    values[column] = canonical
                identity = {key: {"hex": row[key].hex()} if isinstance(row[key], bytes) else row[key] for key in keys}
                records.append({"key": identity, "values": values})
            tables.append({"table": name, "records": records})
    return tables


if __name__ == "__main__":
    if sys.argv[1] != "export":
        raise ValueError("This diagnostic only checks read-only JSON round trips")
    print(json.dumps(export(), ensure_ascii=True, sort_keys=True))
