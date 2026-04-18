"""Dump the native tool index as tab-separated lines for regression diffing.

Usage:
    uv run python scripts/dump_tool_index.py [--obsidian-vault-path PATH] [--google-credentials-path PATH]

Output (one line per tool, sorted by name):
    name<TAB>app=<bool><TAB>vis=<val><TAB>ro=<bool><TAB>cs=<bool><TAB>intg=<str|None><TAB>ret=<int|None><TAB>max=<int>
"""

import argparse

from co_cli.agent._native_toolset import _build_native_toolset
from co_cli.config._core import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump native tool index for parity comparison.")
    parser.add_argument("--obsidian-vault-path", default=None)
    parser.add_argument("--google-credentials-path", default=None)
    args = parser.parse_args()

    base = load_config()
    overrides = {}
    if args.obsidian_vault_path is not None:
        overrides["obsidian_vault_path"] = args.obsidian_vault_path
    if args.google_credentials_path is not None:
        overrides["google_credentials_path"] = args.google_credentials_path
    config = base.model_copy(update=overrides) if overrides else base

    _, index = _build_native_toolset(config)

    for name in sorted(index):
        info = index[name]
        vis = info.visibility.value
        print(
            f"{name}\t"
            f"app={info.approval}\t"
            f"vis={vis}\t"
            f"ro={info.is_read_only}\t"
            f"cs={info.is_concurrent_safe}\t"
            f"intg={info.integration}\t"
            f"ret={info.retries}\t"
            f"max={info.max_result_size}"
        )


if __name__ == "__main__":
    main()
