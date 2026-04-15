"""Интерактивный режим: задавай вопросы по тендерной документации.

Использование:
    python ask.py <tender_id>                              # LLM-роутинг (по умолчанию)
    python ask.py <tender_id> -q "Кто является заказчиком?"
    python ask.py <tender_id> -v
    python ask.py <tender_id> --mode hybrid                # hybrid search (требует --build-index)
"""

import argparse
import logging
import sys
from pathlib import Path

from tender_tools.answer_generator import AnswerGenerator
from tender_tools.config import DATA_DIR
from tender_tools.context_assembler import ContextAssembler
from tender_tools.llm_client import LLMClient


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def get_tender_dir(tender_id: str) -> Path:
    return DATA_DIR / "tenders" / tender_id


# ---------------------------------------------------------------------------
# Build index
# ---------------------------------------------------------------------------

def build_index(tender_id: str) -> None:
    """Строит embedding + BM25 индекс для тендера."""
    from tender_tools.embedding_client import EmbeddingClient
    from tender_tools.indexer import TenderIndexer

    tender_dir = get_tender_dir(tender_id)
    parsed_dir = tender_dir / "parsed"
    index_dir = tender_dir / "indexes"

    if not parsed_dir.exists():
        print(f"[ОШИБКА] Parsed-документы не найдены: {parsed_dir}")
        print(f"Сначала запустите: python ingest.py {tender_id}")
        sys.exit(1)

    print(f"Создание индекса для тендера {tender_id}...")
    emb = EmbeddingClient()
    indexer = TenderIndexer(emb)
    count = indexer.build_index(parsed_dir, index_dir)
    print(f"[OK] Индекс создан: {count} чанков")
    emb.close()


# ---------------------------------------------------------------------------
# Load components
# ---------------------------------------------------------------------------

def load_hybrid_components(tender_id: str):
    """Загружает компоненты для hybrid search."""
    from tender_tools.embedding_client import EmbeddingClient
    from tender_tools.hybrid_search import HybridSearcher, SearchIndex

    tender_dir = get_tender_dir(tender_id)
    index_dir = tender_dir / "indexes"

    if not (index_dir / "vectors.npy").exists():
        print(f"[ОШИБКА] Индекс не найден. Создайте его:")
        print(f"  python ask.py {tender_id} --build-index")
        sys.exit(1)

    llm = LLMClient()
    if not llm.ping():
        print("[ОШИБКА] LLM-сервер недоступен. Запустите LM Studio.")
        sys.exit(1)

    emb = EmbeddingClient()
    index = SearchIndex(index_dir)
    searcher = HybridSearcher(index, emb, use_reranker=True)
    assembler = ContextAssembler(parsed_dir=tender_dir / "parsed", llm=llm)
    generator = AnswerGenerator(llm)

    return llm, emb, searcher, assembler, generator


def load_llm_components(tender_id: str):
    """Загружает компоненты для LLM-роутинга."""
    from tender_tools.passport import DocumentMap
    from tender_tools.question_router import QuestionRouter

    tender_dir = get_tender_dir(tender_id)
    map_path = tender_dir / "passports" / "document_map.json"

    if not map_path.exists():
        print(f"[ОШИБКА] Карта документации не найдена: {map_path}")
        print(f"Сначала запустите: python ingest.py {tender_id}")
        sys.exit(1)

    doc_map = DocumentMap.load(map_path)
    llm = LLMClient()

    if not llm.ping():
        print("[ОШИБКА] LLM-сервер недоступен. Запустите LM Studio.")
        sys.exit(1)

    router = QuestionRouter(llm, doc_map)
    assembler = ContextAssembler(parsed_dir=tender_dir / "parsed", llm=llm)
    generator = AnswerGenerator(llm)

    return llm, router, assembler, generator


# ---------------------------------------------------------------------------
# Ask question
# ---------------------------------------------------------------------------

def ask_hybrid(
    question: str,
    searcher,
    assembler: ContextAssembler,
    generator: AnswerGenerator,
    show_debug: bool = False,
) -> str:
    """Вопрос → hybrid search → контекст → ответ."""
    route = searcher.search_to_route(question)

    if show_debug:
        print(f"  [search] docs={route.target_docs}, sections={route.target_sections}, scope={route.scope}")

    context = assembler.assemble(route)

    if show_debug:
        print(f"  [context] strategy={context.strategy}, ~{context.estimated_tokens} tokens, sources={context.source_docs}")

    return generator.answer(context)


def ask_llm(
    question: str,
    router,
    assembler: ContextAssembler,
    generator: AnswerGenerator,
    show_debug: bool = False,
) -> str:
    """Вопрос → LLM роутинг → контекст → ответ."""
    routing = router.route([question])
    if not routing.routes:
        return "Не удалось определить, в каких документах искать ответ."
    route = routing.routes[0]

    if show_debug:
        print(f"  [routing] docs={route.target_docs}, sections={route.target_sections}, scope={route.scope}")
        print(f"  [routing] reasoning: {route.reasoning}")

    context = assembler.assemble(route)

    if show_debug:
        print(f"  [context] strategy={context.strategy}, ~{context.estimated_tokens} tokens, sources={context.source_docs}")

    return generator.answer(context)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive_mode(ask_fn, tender_id: str, mode: str, verbose: bool = False):
    """Интерактивный цикл вопрос-ответ."""
    print(f"=== Тендер: {tender_id} | Режим: {mode} ===")
    print(f"Введите вопрос (или 'exit' для выхода)\n")

    while True:
        try:
            question = input("Вопрос> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            break

        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            print("Выход.")
            break

        try:
            answer = ask_fn(question, show_debug=verbose)
            print(f"\nОтвет:\n{answer}\n")
            print("-" * 60)
        except Exception as exc:
            print(f"\n[ОШИБКА] {exc}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Задавай вопросы по тендерной документации"
    )
    parser.add_argument("tender_id", help="ID тендера")
    parser.add_argument(
        "-q", "--question", default=None,
        help="Одиночный вопрос (без интерактивного режима)",
    )
    parser.add_argument(
        "-m", "--mode", choices=["llm", "hybrid"], default="llm",
        help="Режим роутинга: llm (через LLM, по умолчанию) или hybrid (BM25+embeddings, требует --build-index)",
    )
    parser.add_argument(
        "--build-index", action="store_true",
        help="Создать embedding+BM25 индекс и выйти",
    )
    parser.add_argument(
        "--no-rerank", action="store_true",
        help="Отключить reranker (hybrid режим)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Показывать отладочную информацию",
    )
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    # Build index
    if args.build_index:
        logging.getLogger().setLevel(logging.INFO)
        build_index(args.tender_id)
        return

    # LLM mode (по умолчанию)
    if args.mode == "llm":
        llm, router, assembler, generator = load_llm_components(args.tender_id)

        def ask_fn(q, show_debug=False):
            return ask_llm(q, router, assembler, generator, show_debug)

        try:
            if args.question:
                print(ask_fn(args.question, show_debug=args.verbose))
            else:
                interactive_mode(ask_fn, args.tender_id, "llm", args.verbose)
        finally:
            llm.close()

    # Hybrid mode (требует предварительного --build-index)
    elif args.mode == "hybrid":
        llm, emb, searcher, assembler, generator = load_hybrid_components(args.tender_id)
        if args.no_rerank:
            searcher.use_reranker = False

        def ask_fn(q, show_debug=False):
            return ask_hybrid(q, searcher, assembler, generator, show_debug)

        try:
            if args.question:
                print(ask_fn(args.question, show_debug=args.verbose))
            else:
                interactive_mode(ask_fn, args.tender_id, "hybrid", args.verbose)
        finally:
            llm.close()
            emb.close()


if __name__ == "__main__":
    main()
