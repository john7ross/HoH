from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


contents = Path(sys.executable).resolve().parents[1]
os.environ.setdefault("HOH_DISTRIBUTION_ROOT", str(contents / "Resources" / "hoh"))

from llm_harness.cli import main
from llm_harness.frozen_entry import plan_frozen_invocation


if __name__ == "__main__":
    invocation = plan_frozen_invocation(sys.argv[1:])
    if invocation.python_code is not None:
        exec(compile(invocation.python_code, "<string>", "exec"), {"__name__": "__main__"})
        raise SystemExit(0)
    if invocation.module is not None:
        sys.argv = [sys.argv[0], *invocation.cli_args]
        runpy.run_module(invocation.module, run_name="__main__", alter_sys=True)
        raise SystemExit(0)
    raise SystemExit(main(list(invocation.cli_args)))
