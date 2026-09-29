# Codex-Dkians

Codex Agent packages for Twin Stars commercial visual workflows.

## Agents

### twin-stars-agent-max

Full V6 commercial visual workflow: brief interpretation → research → art directions → plan → representative validation → batch production → independent final review.

- Entry: `twin-stars-agent-max/SKILL.md`
- Agent interface: `twin-stars-agent-max/agents/openai.yaml`
- Runtime: `twin-stars-agent-max/scripts/` (V6 CLI and helpers)
- Contracts & templates: `twin-stars-agent-max/references/`
- Tests: `twin-stars-agent-max/evals/`

Invoke with `$twin-stars-agent-max`.

### twin-stars-agent-pro

Independent fast workflow: understand product → targeted research → three directions (one sample each) → user picks one → full set + self-check delivery.

- Entry: `twin-stars-agent-pro/SKILL.md`
- Agent interface: `twin-stars-agent-pro/agents/openai.yaml`
- Aesthetic control: `twin-stars-agent-pro/references/aesthetic-control.md`

Invoke with `$twin-stars-agent-pro`.

## Install (Codex)

Copy each agent directory into your Codex skills path:

```text
~/.codex/skills/twin-stars-agent-max/
~/.codex/skills/twin-stars-agent-pro/
```

On Windows this is typically:

```text
%USERPROFILE%\.codex\skills\
```

After copying, the agents are available as `$twin-stars-agent-max` and `$twin-stars-agent-pro`.

## Notes

- User-provided product claims are execution inputs, not independently verified facts.
- Max and Pro remain separate workflows; do not silently migrate projects between them.
- Git HTTPS remote: `https://github.com/ThereisnoholidayonFriday/Codex-Dkians.git`
