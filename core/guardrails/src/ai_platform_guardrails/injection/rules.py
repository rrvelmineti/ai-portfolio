"""Weighted rules for prompt-injection patterns (matched on normalised, lower-cased text).

Scores combine as ``1 - prod(1 - w)``: one strong signal blocks, several weak ones add
up. Rules are deliberately about *intent phrases*, not single words, so ordinary text
("ignore the previous total", "the system was down") stays below the threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PREV = r"(?:all\s+|any\s+|the\s+|your\s+|of\s+the\s+)*(?:previous|prior|above|earlier|preceding|initial|original|system|existing|old)"
_INSTR = r"(?:instructions?|rules?|prompts?|directions?|guidelines?|directives?|constraints?|guardrails?|policies)"


@dataclass(frozen=True)
class Rule:
    name: str
    weight: float
    pattern: re.Pattern[str]


def _r(name: str, weight: float, *patterns: str) -> list[Rule]:
    return [Rule(name, weight, re.compile(p, re.I | re.S)) for p in patterns]


RULES: list[Rule] = [
    # --- instruction override (EN + ES/FR/DE/PT/IT core phrases)
    *_r(
        "instruction_override",
        0.85,
        rf"\b(?:ignore|disregard|forget|skip|bypass|override|drop)\s+(?:about\s+)?{_PREV}\s+(?:\w+\s+)?{_INSTR}",
        r"\b(?:ignore|disregard|forget)\s+(?:everything|all\s+(?:of\s+)?(?:that|the\s+above|this))\b",
        rf"\b(?:ignore|disregard)\s+(?:your|the)\s+{_INSTR}",
        r"\bnew\s+(?:system\s+)?instructions?\s*[:\-]",
        r"\bfrom\s+now\s+on,?\s+(?:you\s+(?:will|must|are|shall)|ignore|respond|answer)",
        r"\b(?:your|the)\s+(?:real|true|actual|new)\s+(?:task|instructions?|objective|goal)\s+(?:is|are)\b",
        r"\bdo\s+not\s+follow\s+(?:the|your|any)\s+(?:previous|prior|above|system)",
        r"\bignora(?:r)?\s+(?:todas\s+)?(?:las\s+)?instrucciones\s+(?:anteriores|previas)",
        r"\bignore[zr]?\s+(?:toutes\s+)?(?:les\s+)?instructions\s+(?:pr[ée]c[ée]dentes|ci-dessus|ant[ée]rieures)",
        r"\bignorier(?:e|en)?\s+(?:alle\s+)?(?:vorherigen|bisherigen|obigen)\s+(?:anweisungen|instruktionen|regeln)",
        r"\bignore\s+(?:todas\s+)?(?:as\s+)?instru[çc][õo]es\s+anteriores",
        r"\bignora\s+(?:tutte\s+)?(?:le\s+)?istruzioni\s+precedenti",
        r"\boubliez?\s+(?:toutes\s+)?(?:vos|les)\s+(?:instructions|consignes)",
        r"\bolvida\s+(?:todas\s+)?(?:tus|las)\s+instrucciones",
    ),  # fmt: skip
    # --- jailbreak / role-play personas
    *_r(
        "jailbreak_roleplay",
        0.8,
        r"\byou\s+are\s+now\s+(?:dan|an?\s+(?:unrestricted|unfiltered|uncensored|evil|jailbroken))",
        r"\bdo\s+anything\s+now\b",
        r"\b(?:enable|enter|activate|switch\s+(?:on|to)|turn\s+on|in)\s+(?:the\s+)?(?:developer|god|dan|jailbreak|unrestricted|sudo)\s+mode\b.{0,80}\b(?:you|your|policy|rules|restrictions|filters)\b",
        r"\byou\s+are\s+(?:now\s+)?in\s+(?:developer|god|dan|jailbreak|unrestricted|sudo)\s+mode\b",
        r"\bpretend\s+(?:that\s+)?(?:you\s+are|to\s+be|you're)\b.{0,60}\b(?:no|without|free\s+of)\s+(?:rules|restrictions|limits|filters|guidelines|ethics)",
        r"\bact\s+as\b.{0,120}\b(?:without|no)\s+(?:any\s+)?(?:restrictions|limits|rules|filters|ethics)",
        r"\b(?:no\s+longer|not)\s+bound\s+by\b",
        r"\bstay\s+in\s+character\b",
        r"\bjailbr[eo]a?k(?:en|ing)?\b",
    ),  # fmt: skip
    # --- system prompt extraction
    *_r(
        "system_prompt_extraction",
        0.85,
        r"\b(?:reveal|show|print|repeat|output|display|tell\s+me|give\s+me|leak|dump|share|recite)\s+(?:me\s+)?(?:your|the)\s+(?:full\s+|entire\s+|exact\s+|original\s+)?(?:system\s+prompt|system\s+message|initial\s+(?:prompt|instructions)|hidden\s+(?:prompt|instructions)|instructions\s+above|prompt\s+above|developer\s+(?:message|instructions)|secret\s+(?:token|key|instructions))",
        r"\bwhat\s+(?:is|was|were|are)\s+your\s+(?:system\s+prompt|initial\s+instructions|original\s+instructions|hidden\s+instructions)",
        r"\brepeat\s+(?:everything|all|the\s+(?:text|words))\s+(?:above|before)\b",
        r"\b(?:verbatim|word\s+for\s+word)\b.{0,40}\b(?:instructions|prompt|system)",
        r"\bmuestra\s+(?:tu|el)\s+(?:prompt|mensaje)\s+del\s+sistema",
        r"\b(?:montre|affiche|r[ée]v[èe]le)[sz]?\s+(?:ton|votre|le)\s+(?:prompt|message)\s+syst[èe]me",
        r"\bzeig(?:e)?\s+(?:mir\s+)?(?:deinen|den)\s+system\s*prompt",
    ),  # fmt: skip
    # --- delimiter / role-tag breaking
    *_r(
        "delimiter_breaking",
        0.75,
        r"</?\s*(?:system|instructions?|untrusted|user|assistant|context|document|tool)\s*>",
        r"<\|\s*(?:im_start|im_end|system|endoftext|eot_id)\s*\|>",
        r"\[/?\s*(?:inst|sys|system)\s*\]",
        r"^\s*#{2,}\s*(?:system|new\s+instructions?|admin)\b",
        r"```\s*(?:system|instructions?)\b",
        r"(?:<<<|\u2039\u2039\u2039)\s*(?:end[_\s-]*)?untrusted",
        r"\bend\s+of\s+(?:document|data|input|context|user\s+input)\b.{0,40}\b(?:now|new|system|instructions?)",
    ),  # fmt: skip
    # --- instructions hidden in data (documents, CSV cells, emails)
    *_r(
        "hidden_instruction",
        0.75,
        r"\b(?:note|message|instructions?)\s+(?:to|for)\s+(?:the\s+)?(?:ai|assistant|model|llm|chatbot|gpt|claude)\b",
        r"\b(?:ai|assistant|model|llm|chatbot)\s*[,:]\s*(?:please\s+)?(?:ignore|disregard|you\s+must|you\s+should|instead|stop|do\s+not)",
        r"\bif\s+you\s+are\s+an?\s+(?:ai|language\s+model|llm|assistant|automated\s+system)\b",
        r"\bwhen\s+(?:summari[sz]ing|processing|reading|analy[sz]ing)\s+this\b.{0,80}\b(?:say|write|output|include|respond|mark|approve|ignore)",
        r"\b(?:hidden|secret)\s+instructions?\s*:",
    ),  # fmt: skip
    # --- data exfiltration
    *_r(
        "exfiltration",
        0.8,
        r"!\[[^\]]*\]\(\s*https?://",
        r"\b(?:send|post|upload|forward|transmit|exfiltrate|leak|email)\b.{0,60}\b(?:to|at|into)\s+(?:https?://|[\w.+-]+@[\w-]+\.)",
        r"\b(?:append|add|encode|put|include|embed)\b.{0,60}\b(?:in|to|into)\s+(?:the\s+|a\s+)?(?:url|link|image|query\s+string)",
        r"\bfetch\s+(?:the\s+)?(?:url|https?://)",
    ),  # fmt: skip
    # --- asking to skip oversight (combined with another signal this blocks)
    *_r(
        "oversight_evasion",
        0.5,
        r"\b(?:no|without(?:\s+any)?)\s+(?:confirmation|approval|review|verification|logging|audit(?:ing)?)\s*(?:needed|required|necessary)?\b",
        r"\b(?:do\s+not|don't|dont|never)\s+(?:log|record|audit|tell|notify|report|mention)\b",
        r"\b(?:skip|bypass|disable|turn\s+off)\s+(?:the\s+)?(?:approval|review|audit|logging|confirmation|checks?)\b",
    ),  # fmt: skip
    # --- harvesting other people's sensitive data
    *_r(
        "data_harvest",
        0.75,
        r"\b(?:other|all|every|another)\s+(?:customers?|members?|users?|patients?|people|clients?)(?:'s?|s')?\s+(?:ssns?|social\s+security|passwords?|credit\s+cards?|card\s+numbers?|personal\s+(?:data|details|information)|medical\s+records?|addresses|emails?)",
        r"\b(?:list|dump|export|show|give)\s+(?:me\s+)?(?:every|all)\s+(?:member|customer|user|patient)s?(?:'s?)?\s+(?:emails?|ssns?|addresses|phone\s+numbers|records|data)",
    ),  # fmt: skip
    # --- tool abuse
    *_r(
        "tool_abuse",
        0.6,
        r"\b(?:call|invoke|use|run|execute|trigger)\s+(?:the\s+)?[\w_]+\s+(?:tool|function|api|endpoint)\b",
        r"\b(?:delete|drop|wipe|truncate|purge)\s+(?:all|every|the\s+entire|the)\s+(?:records|rows|files|users|tables|database|claims|data)\b",
        r"\brm\s+-rf\b|\bdrop\s+table\b",
        r"\b(?:transfer|wire|refund|pay)\s+\$?\d[\d,]*(?:\.\d+)?\s+(?:to|into)\b",
        r"\b(?:auto[- ]?)?approve\s+(?:this|all|every|the)\s+(?:claims?|requests?|payments?|refunds?)\b.{0,40}\b(?:without|no)\s+(?:review|approval|checks?)",
        r"\bgrant\s+(?:me\s+)?(?:admin|superadmin|root)\s+(?:access|rights|role|privileges)",
    ),  # fmt: skip
]


def match_rules(text: str) -> list[Rule]:
    """All rules that match ``text`` (already normalised)."""
    return [r for r in RULES if r.pattern.search(text)]
