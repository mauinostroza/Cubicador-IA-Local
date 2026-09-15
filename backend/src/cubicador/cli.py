import argparse
import json

from .adapters import HeuristicInterpreter, LlamaServerInterpreter
from .pipeline import process_pdf


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrae una tabla resumen de cantidades de un PDF con texto")
    parser.add_argument("pdf")
    parser.add_argument("--excel", required=True)
    parser.add_argument("--json")
    parser.add_argument("--text", help="Guarda el texto layout bruto sin truncar")
    parser.add_argument("--mode", choices=("heuristic", "llama-server"), default="heuristic")
    parser.add_argument("--llama-url", default="http://127.0.0.1:8080/v1/chat/completions")
    args = parser.parse_args()
    adapter = HeuristicInterpreter() if args.mode == "heuristic" else LlamaServerInterpreter(args.llama_url)
    result = process_pdf(args.pdf, args.excel, adapter, args.text)
    rendered = result.model_dump_json(indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as output:
            output.write(rendered + "\n")
    print(rendered)
    return 0 if result.tabla_encontrada and result.filas else 2


if __name__ == "__main__":
    raise SystemExit(main())
