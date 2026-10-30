"""Run the single-agent news prototype on Kalshi topics (phrase terms).

Run:  python3 scripts/run_news_agent.py [--snapshot GZ] [--max-articles N]

Resolves the latest webngrams snapshot unless --snapshot is given.
Prints one Proposal per topic. Needs no LLM key (extractive fallback);
with MODEL_API_KEY set, bullets come from Muse Spark.
"""

import sys

import click

sys.path.insert(0, ".")

from sources.ngrams import resolve_snapshot  # noqa: E402
from squad.news_agent import NewsAgent  # noqa: E402

TASKS = [
    {"event_ticker": "KXWARSHOUT-26JUL",
     "terms": ["kevin warsh", "federal reserve", "rate cut"]},
    {"event_ticker": "KXSPACEXMARS-30",
     "terms": ["spacex", "starlink", "elon musk"]},
    {"event_ticker": "KXTOPLLMGENERAL-30",
     "terms": ["artificial intelligence", "openai", "anthropic"]},
]


@click.command()
@click.option("--snapshot", default=None)
@click.option("--max-articles", type=int, default=3)
@click.option("--langsmith-project", default=None)
def main(snapshot, max_articles, langsmith_project) -> None:
    from squad.tracing import setup_langsmith  # noqa: E402

    ls = setup_langsmith(project=langsmith_project)
    print(f"[agent] langsmith={'on' if ls['enabled'] else 'off'} "
          f"project={ls['project']}")

    snap = snapshot
    if not snap:
        try:
            ts, snap = resolve_snapshot()
        except RuntimeError:
            print("[agent] FAIL: no snapshot")
            sys.exit(1)
    print(f"[agent] snapshot {snap}")

    ag = NewsAgent(snapshot_gz=snap, max_articles=max_articles)
    for task in TASKS:
        for proposal in ag.run(task):
            print("=" * 100)
            print(f"{proposal.agent} | {proposal.action} | "
                  f"{proposal.entity_id} | conf={proposal.confidence}")
            print(proposal.detail)
    sys.exit(0)


if __name__ == "__main__":
    main()
