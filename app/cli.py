"""CLI для загрузки и обработки тендерной документации.

Использование:
    python -m app.cli <tender_id>
    python -m app.cli <tender_id> --force
    python -m app.cli 12345678 87654321    # несколько тендеров
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.logging import setup_logging
from app.services.pipeline import TenderPipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Скачать и обработать документацию тендера по ID"
    )
    parser.add_argument(
        "tender_ids",
        nargs="+",
        help="Один или несколько ID тендеров",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Принудительно перескачать и переобработать",
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=10,
        help="Количество параллельных воркеров для генерации паспортов (по умолчанию: 10)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Подробный вывод (DEBUG)",
    )
    args = parser.parse_args()

    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO, datefmt="%H:%M:%S")
    pipeline = TenderPipeline(passport_workers=args.workers)

    success = 0
    errors = 0
    for tender_id in args.tender_ids:
        try:
            manifest = pipeline.ingest(tender_id, force=args.force)
            print(f"\n[OK] Тендер {tender_id}: {len(manifest.documents)} документов")
            success += 1
        except Exception as exc:
            logging.error("[FAIL] Тендер %s: %s", tender_id, exc)
            errors += 1

    print(f"\nИтого: {success} успешно, {errors} с ошибками")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
