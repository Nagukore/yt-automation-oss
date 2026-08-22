"""Topic filtering for YouTube's "AI persona / sensitive topics" monetization rule.

YouTube's inauthentic-content policy (clarified 16 Jul 2026) makes a channel
ineligible for the Partner Program when it carries an excessive amount of
*AI-generated personas discussing finance, legal, healthcare or medical topics*.
This pipeline narrates every video with a synthetic voice, so it falls squarely
in scope and has to keep those topics off the channel by construction.

The filter is deliberately split in two, because a single blocklist gets this
wrong in both directions:

ADVICE — phrasing that offers guidance ("should you buy", "how to treat").
    This is what the policy actually targets, so it applies to *every* source,
    including the daily AI-news stream.

DOMAIN — subject matter that is inherently financial/medical/legal ("sensex",
    "IPO GMP", "dengue symptoms"). Applied only to raw trend feeds, where a
    two-word search term gives the script stage nothing to work from except the
    sensitive subject itself — a 6-minute narrated video about "gold rate today"
    becomes financial advice no matter how the prompt is worded.

    It is NOT applied to the AI-news stream: legitimate tech journalism reports
    on funding rounds, valuations and medical AI constantly ("Anthropic raises
    $2B", "model flags tumours earlier than radiologists"). Those are reporting,
    not advice, and blocking them would gut the stream.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------- advice-shaped
# Guidance phrasing across all three regulated domains. Applies everywhere.
ADVICE = re.compile(
    r"\b("
    # -- finance
    r"stocks?\s+to\s+buy|best\s+(ai\s+)?stocks?|should\s+(you|i)\s+(buy|sell|invest)|"
    r"no-brainer|unstoppable|millionaires?|billionaires?\s+are\s+buying|"
    r"price\s+targets?|buy\s+now|skyrocket|soar(s|ed|ing)?|surge[sd]?|"
    r"how\s+to\s+invest|investment\s+advice|where\s+to\s+invest|get\s+rich|"
    r"multibagger|penny\s+stocks?|guaranteed\s+returns?|double\s+your\s+money|"
    r"best\s+mutual\s+funds?|top\s+\d+\s+(stocks?|funds?|coins?)|"
    # -- health
    r"how\s+to\s+(cure|treat|lose\s+weight)|cure\s+for|symptoms\s+of|"
    r"home\s+remed(y|ies)|miracle\s+cure|natural\s+remed(y|ies)|"
    r"best\s+supplements?|should\s+(you|i)\s+take|safe\s+dosage|"
    # -- legal
    r"legal\s+advice|know\s+your\s+rights|how\s+to\s+file\s+(a\s+)?(case|lawsuit|petition|fir)|"
    r"can\s+(you|i)\s+sue|is\s+it\s+legal\s+to"
    r")\b",
    re.IGNORECASE,
)

# ------------------------------------------------------------- domain-specific
# Inherently regulated subject matter. Trend feeds only — see module docstring.
# India-weighted because the long-form stream runs on Google Trends IN.
DOMAIN = re.compile(
    r"\b("
    # -- finance
    r"sensex|nifty|share\s+price|stock\s+market|bse|nse|ipo|gmp|"
    r"mutual\s+funds?|sip\s+returns?|dividends?|bonus\s+issue|q[1-4]\s+results?|"
    r"crypto(currency)?|bitcoin|ethereum|forex|gold\s+rate|silver\s+rate|"
    r"income\s+tax|gst\s+rate|loan|emi|insurance\s+premium|pension|provident\s+fund|"
    # -- health
    r"symptoms?|diagnosis|treatments?|vaccines?|dengue|malaria|covid|"
    r"cancer|diabetes|blood\s+pressure|cholesterol|weight\s+loss|"
    r"medicines?|tablets?|dosage|side\s+effects?|mental\s+health|depression|"
    # -- legal
    r"supreme\s+court|high\s+court|verdicts?|bail|petitions?|"
    r"visa\s+rules?|immigration|citizenship|amendment\s+act"
    r")\b",
    re.IGNORECASE,
)


def rejection_reason(title: str, *, strict: bool) -> str | None:
    """Return why `title` is unsafe to auto-narrate, or None if it's fine.

    `strict` enables the DOMAIN tier and should be set for raw trend feeds.
    Returns a reason string rather than a bool so callers can log *what* was
    dropped — an unattended pipeline that silently discards half its topics is
    indistinguishable from one whose upstream feed died.
    """
    if m := ADVICE.search(title):
        return f"advice-shaped: '{m.group(0)}'"
    if strict and (m := DOMAIN.search(title)):
        return f"regulated subject: '{m.group(0)}'"
    return None


def is_safe(title: str, *, strict: bool = False) -> bool:
    """True when `title` can be narrated by a synthetic voice without policy risk."""
    return rejection_reason(title, strict=strict) is None
