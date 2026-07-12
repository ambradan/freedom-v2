# Dual-substrate infrastructure validation - 2026-07-12

Same LiteLLM proxy, same scaffold path, two substrates. Switching = one alias in config/litellm.yaml.

- freedom-substrate -> Claude (Anthropic API) - production, Freedom v2's active substrate
- local-substrate -> Qwen3-30B-A3B-Instruct-2507 Q4_K_M via llama.cpp (Vulkan/RADV, Radeon 8060S, Strix Halo)

Result: local-substrate responded through the proxy at ~79 tok/s generation
(prompt ~72 tok/s), model loaded in ~4s from cache, 18GB in unified memory.

Scope note: infrastructure validation ONLY. No second instance was started, no memory
was written by the local model, Freedom's baseline on Claude is untouched. The actual
dual-substrate experiment starts after baseline weeks, per protocol (to be pre-registered on OSF).
