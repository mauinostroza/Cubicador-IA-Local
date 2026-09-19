import argparse
import json
from pathlib import Path

from .adapters import HeuristicInterpreter, LlamaServerInterpreter
from .pipeline import process_pdf


def main() -> int:
    parser = argparse.ArgumentParser(description="Extrae una tabla resumen de cantidades de un PDF con texto")
    parser.add_argument("pdf")
    parser.add_argument("--output-dir", required=True, help="Única carpeta autorizada para salidas")
    parser.add_argument("--excel", required=True)
    parser.add_argument("--json")
    parser.add_argument("--text", help="Guarda el texto layout bruto sin truncar")
    parser.add_argument("--mode", choices=("heuristic", "llama-server"), default="heuristic")
    parser.add_argument("--ocr", action="store_true", help="Activa únicamente el bundle OCR fijado en esta versión")
    parser.add_argument("--developer-tools", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output_root = Path(args.output_dir).resolve(strict=True)
    adapter = HeuristicInterpreter() if args.mode == "heuristic" else LlamaServerInterpreter()
    from .security import SecurityPolicy
    policy = SecurityPolicy(developer_tools_enabled=args.developer_tools)
    ocr_provider = None
    if args.ocr:
        from .ocr import PaddleOcrProvider
        ocr_provider = PaddleOcrProvider()
    result = process_pdf(args.pdf, args.excel, adapter, args.text, output_root=output_root,
                         ocr_provider=ocr_provider, policy=policy)
    rendered = result.model_dump_json(indent=2)
    if args.json:
        from .runtime import atomic_write_bytes, safe_output_path
        target = safe_output_path(args.json, output_root, ".json")
        atomic_write_bytes(target, (rendered + "\n").encode("utf-8"))
    print(rendered)
    return 0 if result.tabla_encontrada and result.filas else 2


if __name__ == "__main__":
    raise SystemExit(main())
