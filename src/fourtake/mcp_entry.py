"""
Console-script shim for `4take-mcp`.

`mcp` is an optional dependency: it pulls starlette, uvicorn, sse-starlette, python-multipart,
pyjwt and ~12MB of cryptography into every install, none of which a CLI user ever runs. The
entry point must therefore not import `fourtake.mcp_server` at module level — doing so turns a
missing extra into an ImportError traceback instead of an instruction.
"""

import sys

INSTALL_HINT = (
    "4take-mcp requires the MCP server extra.\n"
    "  pip install '4take[mcp]'      (or)  uv tool install '4take[mcp]'\n"
    # `uvx <pkg> <args>` runs the console script named after the package, so the extra has
    # to be requested with --from or uvx would run `4take` with `4take-mcp` as an argument.
    "  uvx --from '4take[mcp]' 4take-mcp    to run it without installing"
)


def main() -> None:
    try:
        from .mcp_server import main as run_server
    except ImportError as exc:
        print(f"{INSTALL_HINT}\n\n(missing dependency: {exc.name or exc})", file=sys.stderr)
        raise SystemExit(1) from exc
    run_server()


if __name__ == "__main__":
    main()
