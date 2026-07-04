"""Point d'entrée : ``python -m ifc_openshell_mcp`` ou ``ifc-geometry-mcp``.

Transports FastMCP 3.x : ``stdio`` (défaut, clients locaux type Claude Desktop /
Cowork), ``http`` / ``streamable-http`` et ``sse`` (réseau).
"""

from __future__ import annotations

import argparse
import sys

from .server import mcp


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ifc_openshell_mcp",
        description="Serveur MCP ifc-geometry — audit géométrique IFC.",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=("stdio", "http", "sse", "streamable-http"),
        help="Transport MCP (défaut stdio).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Hôte d'écoute (transports réseau)."
    )
    parser.add_argument(
        "--port", type=int, default=8766, help="Port d'écoute (transports réseau)."
    )
    args = parser.parse_args()

    kwargs: dict = {}
    if args.transport in ("http", "sse", "streamable-http"):
        kwargs["host"] = args.host
        kwargs["port"] = args.port
        print(
            f"ifc-geometry MCP — transport={args.transport} "
            f"sur http://{args.host}:{args.port}",
            file=sys.stderr,
        )

    mcp.run(transport=args.transport, **kwargs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
