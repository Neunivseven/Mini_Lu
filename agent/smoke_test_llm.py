"""
DeepSeek API 冒烟测试。

用法：
  1) 在 config/llm.local.yaml 填写 api_key，或设置 DEEPSEEK_API_KEY
  2) pip install openai PyYAML
  3) python -m agent.smoke_test_llm
  4) 可选：python -m agent.smoke_test_llm "用一句话介绍你自己"
"""
from __future__ import annotations

import sys

from agent.llm_client import LLMClient, load_llm_config


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    prompt = argv[0] if argv else "Hello，请用中文简单回复一句。"

    cfg = load_llm_config()
    print(f"provider={cfg.provider}")
    print(f"base_url={cfg.base_url}")
    print(f"model={cfg.model}")
    print(f"api_key={'set' if cfg.api_key else 'missing'}")
    print("---")
    try:
        client = LLMClient(cfg)
        reply = client.chat(prompt)
        print("assistant:", reply)
        return 0
    except Exception as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
