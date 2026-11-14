---
name: hsa-plan-decision
description: Guide a person, step by step, through deciding whether an HSA-eligible high-deductible health plan (HDHP) or a traditional plan (PPO/HMO/EPO) is cheaper for them, using their own claims history and their employer's plan options, with the offline tools in this repository. Interviews them to find out what they have, tells them exactly what to fetch and where, converts whatever files they have (any insurer, any format, or none at all) into the toolkit's inputs, runs the de-identified extraction and the cost model, and explains the result. USE WHEN someone asks "should I switch to an HSA / HDHP?", "which health plan should I pick at open enrollment?", "is the high-deductible plan worth it for my family?", wants to run this toolkit on their own data, or wants a guided demo of it on the fictional demo family. DO NOT USE FOR medical advice, filing taxes, choosing HSA investments, or Medicare/ACA marketplace plan shopping.
license: MIT
---

# HSA vs. traditional plan decision (Claude Code entry point)

This is a pointer for Claude Code, which discovers skills in `.claude/skills/`. The skill itself lives in `.github/skills/hsa-plan-decision/` so every agent shares one copy.

Before doing anything else, read `.github/skills/hsa-plan-decision/SKILL.md` (path from the repository root) and follow it exactly, including its privacy protocol. Resolve the `references/...` links in that file relative to `.github/skills/hsa-plan-decision/`.

Where it says to use a structured question tool such as `ask_user`, use your own question tool (for example `AskUserQuestion`) if you have one.
