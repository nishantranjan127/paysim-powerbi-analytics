import argparse
import json
from pathlib import Path

from finance_lakehouse.local import run
from finance_lakehouse.sample_data import generate


def main() -> None:
    parser = argparse.ArgumentParser(description="Financial transactions lakehouse local demo")
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("generate", help="Generate synthetic CSV with quality edge cases")
    sample.add_argument("--output", type=Path, default=Path("data") / "landing" / "transactions.csv")
    sample.add_argument("--rows", type=int, default=1000)
    sample.add_argument("--seed", type=int, default=42)
    pipeline = commands.add_parser("run", help="Run the local reference pipeline")
    pipeline.add_argument("--input", type=Path, default=Path("data") / "landing" / "transactions.csv")
    pipeline.add_argument("--output", type=Path, default=Path("data") / "output")
    args = parser.parse_args()
    if args.command == "generate":
        print(f"Generated {generate(args.output, args.rows, args.seed)} synthetic rows at {args.output}")
    else:
        print(json.dumps(run(args.input, args.output), indent=2))


if __name__ == "__main__":
    main()
