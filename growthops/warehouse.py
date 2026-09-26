"""Build local SQL staging and marts from the versioned view definitions."""

from __future__ import annotations

import argparse
from importlib.resources import files

from growthops.db import connect, initialize


def build(database: str) -> None:
    connection = connect(database)
    try:
        initialize(connection)
        sql = files("growthops").joinpath("sql/marts.sql").read_text(encoding="utf-8")
        connection.executescript(sql)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/growthops-sample.db")
    args = parser.parse_args()
    build(args.database)
    print(f"Built staging and marts in {args.database}")


if __name__ == "__main__":
    main()
