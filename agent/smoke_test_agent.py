"""
LangChain Agent 冒烟测试（DeepSeek + 工具）。

用法：
  pip install -r requirements.txt
  python -m agent.smoke_test_agent
  python -m agent.smoke_test_agent "把『开会改到三点』记到记事里"
"""
from __future__ import annotations

import sys

from agent.llm_client import load_llm_config
from agent.pet_agent import PetAgent
from agent.tools import default_tools


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    prompt = (
        argv[0]
        if argv
        else "请调用 list_directory 工具，列出当前项目根目录下的条目，然后用一两句话总结。"
    )

    cfg = load_llm_config()
    print(f"model={cfg.model}")
    print(f"base_url={cfg.base_url}")
    print(f"api_key={'set' if cfg.api_key else 'missing'}")
    print(f"tools={[t.name for t in default_tools()]}")
    print("---")
    print("user:", prompt)

    try:
        agent = PetAgent(cfg)
        reply = agent.ask(prompt)
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = reply.encode(enc, errors="replace").decode(enc, errors="replace")
        print("assistant:", safe)
        return 0
    except Exception as e:
        msg = str(e).encode(getattr(sys.stderr, "encoding", None) or "utf-8", errors="replace")
        print("[FAIL]", msg.decode(errors="replace"), file=sys.stderr)
        return 1




if __name__ == "__main__":
    raise SystemExit(main())
