"""Live smoke test: GDELT news only. Stdlib only.

Run:  python3 scripts/smoke_gdelt.py [--limit 3] [--query climate]

Public artlist queries need no key. Retries on 429. Exits non-zero on failure.
"""

import sys

import click

sys.path.insert(0, ".")

from sources.gdelt import GdeltSource


@click.command()
@click.option("--limit", type=int, default=3)
@click.option("--query", type=str, default="climate")
def main(limit, query) -> None:
    print(f"[gdelt] GET artlist query={query} (limit={limit}) ...")
    articles = GdeltSource(query=query).fetch_normalized(limit=limit)
    print(f"[gdelt] got {len(articles)} articles")
    for a in articles[: limit]:
        print(f"  - {a.title[:100]} ({a.url[:60]})")
    if not articles:
        print("[gdelt] FAIL: empty result")
        sys.exit(1)

    print("SMOKE OK: gdelt returned data")
    sys.exit(0)


if __name__ == "__main__":
    main()
