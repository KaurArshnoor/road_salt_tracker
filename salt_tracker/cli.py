from __future__ import annotations
import argparse
import importlib
from pathlib import Path

import yaml

from salt_tracker.discovery import wayback
from salt_tracker.ingestion import downloader
from salt_tracker.extraction import orchestrator as extraction_orchestrator
from salt_tracker.normalization import runner as normalization_runner
from salt_tracker.quality import checks as quality_checks
from salt_tracker.analytics import export as analytics_export

SOURCES_CONFIG = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"


def _load_source(state_code: str, cfg: dict):
    entry = cfg["states"].get(state_code)
    if not entry or not entry.get("parser"):
        print(f"  [skip] no discovery source configured for {state_code}")
        return None
    module_name, class_name = entry["parser"].split(".")
    module = importlib.import_module(f"salt_tracker.discovery.{module_name}")
    cls = getattr(module, class_name)
    return cls(listing_urls=entry.get("listing_urls"))


def cmd_discover(states: list[str], include_wayback: bool) -> None:
    with open(SOURCES_CONFIG) as f:
        cfg = yaml.safe_load(f)

    total = 0
    for state in states:
        source = _load_source(state, cfg)
        if source is None:
            continue
        print(f"Discovering {state}...")
        try:
            docs = source.discover()
        except Exception as e:
            print(f"  [error] live discovery failed for {state}: {e}")
            docs = []
        inserted = downloader.register_discovered(docs)
        print(f"  found {len(docs)} documents, {inserted} new")
        total += inserted

        if include_wayback:
            for listing_url in source.listing_urls:
                try:
                    archived = wayback.discover_from_archive(source, listing_url)
                except Exception as e:
                    print(f"  [error] wayback discovery failed for {state} ({listing_url}): {e}")
                    archived = []
                inserted = downloader.register_discovered(archived)
                print(f"  wayback: found {len(archived)} archived documents, {inserted} new")
                total += inserted

    print(f"Total new documents registered: {total}")


def cmd_ingest() -> None:
    results = downloader.download_pending()
    print(f"Ingest results: {results}")


def cmd_extract() -> None:
    results = extraction_orchestrator.process_pending()
    print(f"Extraction results: {results}")


def cmd_normalize() -> None:
    results = normalization_runner.run()
    print(f"Normalization results: {results}")


def cmd_quality() -> None:
    results = quality_checks.run()
    print(f"Quality check results: {results}")


def cmd_export(out: str) -> None:
    path = analytics_export.export_workbook(out)
    csv_path = analytics_export.export_csv(Path(out).with_suffix(".csv"))
    print(f"Exported workbook to {path}")
    print(f"Exported raw line items CSV to {csv_path}")


def cmd_run_all(states: list[str], include_wayback: bool, out: str) -> None:
    cmd_discover(states, include_wayback)
    cmd_ingest()
    cmd_extract()
    cmd_normalize()
    cmd_quality()
    cmd_export(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Road salt contract tracker pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_discover = sub.add_parser("discover")
    p_discover.add_argument("--states", nargs="+", required=True)
    p_discover.add_argument("--include-wayback", action="store_true")

    sub.add_parser("ingest")
    sub.add_parser("extract")
    sub.add_parser("normalize")
    sub.add_parser("quality")

    p_export = sub.add_parser("export")
    p_export.add_argument("--out", default="data/processed/road_salt_dataset.xlsx")

    p_run_all = sub.add_parser("run-all")
    p_run_all.add_argument("--states", nargs="+", required=True)
    p_run_all.add_argument("--include-wayback", action="store_true")
    p_run_all.add_argument("--out", default="data/processed/road_salt_dataset.xlsx")

    args = parser.parse_args()

    if args.command == "discover":
        cmd_discover(args.states, args.include_wayback)
    elif args.command == "ingest":
        cmd_ingest()
    elif args.command == "extract":
        cmd_extract()
    elif args.command == "normalize":
        cmd_normalize()
    elif args.command == "quality":
        cmd_quality()
    elif args.command == "export":
        cmd_export(args.out)
    elif args.command == "run-all":
        cmd_run_all(args.states, args.include_wayback, args.out)


if __name__ == "__main__":
    main()
