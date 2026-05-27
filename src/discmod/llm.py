import json
import logging

import anthropic

from .models import PackMod, SoftConflict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You analyze proposed additions to a Minecraft modpack for soft conflicts that a "
    "dependency graph won't catch: feature overlap (two shader pipelines, two inventory "
    "sorters), redundant systems, world-gen collisions, or performance concerns when "
    "combined. You output strictly valid JSON, no preamble, no markdown fences. "
    "Only flag genuine overlaps — two tech mods coexisting is fine."
)


async def soft_conflict_check(
    new_mod: PackMod,
    current_pack: list[PackMod],
    client: anthropic.AsyncAnthropic,
    model: str,
) -> tuple[str, list[SoftConflict]]:
    pack_lines = "\n".join(
        f"- {m.slug}: {m.title} — {m.description[:300]}" for m in current_pack
    )
    user_prompt = (
        f"Current pack contents:\n{pack_lines}\n\n"
        f"Proposed addition:\n"
        f"- {new_mod.slug}: {new_mod.title}\n"
        f"- {new_mod.description[:300]}\n\n"
        "Output schema:\n"
        '{\n'
        '  "summary": "one-line description of what this mod does",\n'
        '  "conflicts": [\n'
        '    {"with": "slug", "severity": "low|medium|high", "reason": "..."}\n'
        '  ]\n'
        '}'
    )

    try:
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = response.content[0].text.strip()
        # Defensively strip fences
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        summary = data.get("summary", "")
        conflicts = [
            SoftConflict(
                with_slug=c["with"],
                severity=c.get("severity", "low"),
                reason=c.get("reason", ""),
            )
            for c in data.get("conflicts", [])
        ]
        return summary, conflicts
    except Exception as exc:
        logger.error("LLM soft-conflict check failed: %s", exc)
        return "", []
