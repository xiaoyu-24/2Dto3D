import os

from sqlalchemy import URL, create_engine, text


def make_engine():
    url = URL.create(
        "mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ.get("DB_HOST", "mysql"),
        database=os.environ.get("DB_NAME", "drawings"),
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, isolation_level="READ COMMITTED", pool_pre_ping=True,
                         connect_args={"max_allowed_packet": 256 * 1024 * 1024})


engine = make_engine()


def one(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().first()


def rows(conn, sql, **params):
    return list(conn.execute(text(sql), params).mappings())
