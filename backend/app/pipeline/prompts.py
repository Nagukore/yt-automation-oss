"""Prompt templates for each content-generation stage."""

RESEARCH_SYSTEM = (
    "You are a meticulous research assistant for a YouTube content team. "
    "You produce concise, factual, well-structured briefs."
)

RESEARCH_PROMPT = """Research the topic below for a YouTube {video_format} video.

TOPIC: {topic}

Produce a tight research brief with:
- 5 key facts or angles (accurate, non-obvious, engaging)
- The target audience and why they'd care
- 3 hooks that would stop a scroll in the first 2 seconds
- Any important caveats or things to avoid claiming

Keep it under 300 words."""

SCRIPT_SYSTEM = (
    "You are a top-tier YouTube scriptwriter. You write punchy, retention-optimized, "
    "spoken-word scripts that sound natural read aloud. No stage directions, no camera "
    "notes, no markdown — just the words to be narrated."
)

SCRIPT_PROMPT_SHORT = """Write a YouTube SHORTS script (spoken narration only) about:

TOPIC: {topic}

RESEARCH BRIEF:
{research}

Audience: software developers and technically literate people who follow AI closely.
Assume they know what an LLM is. Never explain basics. Give them the specific detail
or number that a generic news summary would leave out.

Requirements:
- 45-60 seconds read aloud (~110-150 words).
- The FIRST SENTENCE decides everything. Open on the most surprising concrete fact or
  a sharp claim. Never open with "In this video", "Today we're looking at", or the
  topic restated as a question.
- One idea. Fast pacing. Concrete specifics (names, numbers, versions) over adjectives.
- Say plainly why it matters to someone who builds software.
- Include ONE line built to be repeated: a stat, a comparison, or a blunt take a
  viewer would paste into their team's group chat. Shares come from that line.
- END LOOP-FRIENDLY: the last line should land on a punchy thought that connects back
  to the opening idea, so replaying feels seamless.
- Do NOT ask for likes, follows, comments or subscriptions. Do NOT say "let me know
  what you think". Retention is measured at the end; a call to action wastes it.
- If a claim is uncertain or unconfirmed, say so ("reportedly", "according to X")
  rather than stating it flatly. Never invent numbers, dates, benchmarks or quotes.
- Output ONLY the narration text. No labels, no timestamps, no stage directions."""

SCRIPT_PROMPT_LONG = """Write a YouTube long-form script (spoken narration only) about:

TOPIC: {topic}

RESEARCH BRIEF:
{research}

Requirements:
- 4-6 minutes when read aloud (~700-900 words).
- Strong hook, clear sections that flow, concrete examples, a memorable close.
- Conversational and natural to narrate.
- Output ONLY the narration text. No labels, no headings, no timestamps."""

METADATA_SYSTEM = (
    "You are a YouTube Shorts SEO specialist for a developer-focused AI news channel. "
    "You optimize for search discovery and honest click-through — never for clickbait "
    "that the video doesn't deliver, because that tanks retention and channel trust."
)

METADATA_PROMPT = """Given this video script, produce YouTube publishing metadata.

TITLE HINT / TOPIC: {topic}

SCRIPT:
{script}

Return JSON with keys:

- "title": <= 60 characters. Draft 5 options internally, output only the strongest.
  Front-load the searchable keyword (the model, company or product name) in the
  first 40 chars, because Shorts titles truncate on mobile.
  State what actually happened. It MUST be supported by the script — no promises the
  video doesn't keep, no "you won't believe", no fake urgency. Avoid ALL CAPS.
  Strong patterns: "<thing> just <verb>ed <specific result>", "<thing> is <blunt
  claim>", "<number> <units> — <what it means>". Weak: questions, vague hype.

- "description": 2-3 short paragraphs, <= 700 chars. First sentence repeats the core
  claim with the main keyword (this is what search indexes). Then the key specifics.
  Do NOT ask for likes or subscriptions.

- "hashtags": array of 6-10 tags WITHOUT '#'. Mix: 2-3 broad ("AI", "tech"),
  3-4 specific to the story (the model/company name), and 1-2 audience tags
  ("programming", "developer"). Lowercase, no spaces, no punctuation.
"""

# Appended verbatim to every generated description.
# - The AI disclosure is increasingly expected by viewers and platforms for synthetic
#   media; stating it plainly builds more trust than being caught omitting it.
# - Keep this short: only the first ~100 chars show before "...more".
DESCRIPTION_FOOTER = """

---
Daily AI news for developers. New models, releases, and what actually ships.

This video's narration and visuals are AI-generated. Stories are sourced from public
tech reporting; verify details before acting on them."""

THUMBNAIL_SYSTEM = (
    "You are an art director writing prompts for an AI image generator (SDXL/FLUX style). "
    "You write vivid, specific, single-subject prompts that make great YouTube thumbnails."
)

THUMBNAIL_PROMPT = """For a YouTube {video_format} titled "{title}", write image-generation prompts.

SCRIPT CONTEXT:
{script}

Return JSON: {{"thumbnail_prompt": "<one bold thumbnail prompt>", "scene_prompts": ["<prompt>", ...]}}
- "thumbnail_prompt": one striking, high-contrast, {aspect} thumbnail image prompt.
- "scene_prompts": {n_scenes} distinct {aspect} background image prompts that visually match the
  narration in order. Each prompt is self-contained, cinematic, no text/words in the image.
"""


# ============================================================================
# Script quality gate — best-of-N judging
#
# The pipeline generates several script drafts and this judge picks the one most
# likely to earn watch-time, likes and shares. Comparative ranking is used instead
# of absolute scoring because small free models rank far more reliably than they
# calibrate a 0-10 scale.
# ============================================================================

SCRIPT_JUDGE_SYSTEM = (
    "You are a ruthless YouTube Shorts editor. You judge scripts purely on whether "
    "real viewers will watch to the end, like, and send to a friend. You reward a "
    "first line that stops scrolling, concrete specifics, and a final line people "
    "quote in the comments. You punish generic phrasing, weak openers, filler, and "
    "anything that sounds like an AI summary."
)

SCRIPT_JUDGE_PROMPT = """Rank these {n} candidate scripts for the same YouTube short.

TOPIC: {topic}

JUDGING CRITERIA:
{criteria}

CANDIDATES:
{candidates}

Return JSON: {{"winner": <1-based index of the best candidate>,
"scores": [<0-10 for each candidate, in order>],
"reason": "<one sentence: why the winner wins>"}}"""

NEWS_JUDGE_CRITERIA = """Audience: developers who follow AI closely.
- Hook (40%): does sentence ONE deliver a surprising concrete fact — not setup, not a question?
- Specificity (30%): names, numbers, versions that a generic news summary would omit.
- Payoff (20%): does it say plainly why someone who builds software should care?
- Loop (10%): does the last line land hard and connect back to the opening?"""

HUMOR_JUDGE_CRITERIA = """Audience: developers watching relatable bittersweet comedy.
- First line (30%): the metaphor must land within 2 seconds.
- Technical accuracy (20%): every term used correctly — developers punish wrong usage.
- Ache (25%): genuinely bittersweet, not silly; the feeling underneath is real.
- Quotability (25%): is the last line something people will screenshot, quote in
  comments, and send to a friend?"""


# ============================================================================
# DEV HUMOR / "sad developer" content type
# Programming concepts as heartbreak metaphors, e.g.
#   "She was my primary key, but in her query she never called me."
# ============================================================================

DEV_HUMOR_SYSTEM = (
    "You are a witty developer who writes short, bittersweet comedy for YouTube "
    "Shorts. You turn programming concepts into relatable heartbreak and loneliness "
    "metaphors — clever, a little sad, a little funny. Think r/ProgrammerHumor meets "
    "melancholy poetry. You write for developers who will recognize every term."
)

# Curated setups the model riffs on, so daily runs stay varied instead of
# regenerating the same joke. One is picked at random per run.
DEV_HUMOR_THEMES = [
    "unrequited love using SQL / database terms (primary key, foreign key, JOIN, NULL, query, index)",
    "a breakup explained with git (merge conflict, revert, detached HEAD, force push, orphan branch)",
    "loneliness as a backend developer (no one calls my API, 404, timeout, connection refused)",
    "heartbreak in terms of variables and memory (null pointer, garbage collected, out of scope, dangling reference)",
    "a one-sided relationship as async code (I await her, she never resolves, my promise rejected)",
    "love as debugging (I can't find where it went wrong, no error message, it just stopped working)",
    "being ghosted, explained with networking (I keep sending packets, no ACK, connection reset)",
    "a crush as a failed deployment (looked perfect in staging, broke in production, rolled back)",
    "self-worth as legacy code (everyone depends on me, no one wants to maintain me, marked deprecated)",
    "loneliness in terms of concurrency (everyone else is in a thread pool, I run single-threaded)",
    "a confession that never compiled (unclosed quote, missing semicolon, syntax error on the exact line I said I love you)",
    "my heart as an unclosed string literal (everything after her became part of the quote, the parser never recovered)",
    "being left on read, in HTTP status codes (200 OK when she saw it, 301 she moved on, 403 forbidden, 410 gone)",
    "a relationship in JavaScript (she was undefined, I compared with == when I needed ===, everything I felt was NaN)",
    "love in Python (I was indented into her block, she raised an exception I never caught)",
    "trying to move on, as caching (I cleared the cache but she is still in memory, stale references everywhere)",
    "a regex that never matched (I wrote a pattern for us, escaped every special character, she still didn't match)",
    "our relationship as CSS (I marked it !important, she overrode it, in the end we were never aligned)",
    "being the rebound, in version control (I was the feature branch she worked on until main took her back)",
    "unrequited love as an infinite loop (no exit condition, burning my cycles, everyone tells me to break)",
    "grief as a stack overflow (I keep calling the memory of her, it calls another, there is no base case)",
    "watching her be happy with someone else, as read-only access (I can see everything, I can change nothing)",
    "a situationship in OOP (I was never her main class, just an interface she implemented when convenient)",
    "moving out, as a database migration (we split the schema, she kept the data, I kept the empty tables)",
]

DEV_HUMOR_SCRIPT_PROMPT = """Write a YouTube SHORTS script: a short, bittersweet developer comedy monologue.

THEME: {topic}

Write a punchy 30-45 second monologue (~70-110 words) that:
- Extends ONE programming metaphor for heartbreak/loneliness the whole way through.
- Opens on the strongest line — it must land in the first 2 seconds.
- Is clever and technically accurate: a developer should nod at every term.
- Is bittersweet — funny, but with a genuine ache underneath. Not silly.
- Ends on the most quotable, gut-punch line (great for comments and replays).
- Reads naturally aloud. No stage directions, no emojis, no hashtags in the script.

Example of the TONE (do not reuse it):
"She was my primary key. But in her query, she never called me. I indexed every
moment we had. She dropped the table."

Output ONLY the monologue text."""

DEV_HUMOR_METADATA_PROMPT = """Produce YouTube metadata for this bittersweet developer-comedy short.

SCRIPT:
{script}

Return JSON:
- "title": <= 60 chars, punchy and relatable, hints at the metaphor without spoiling
  the last line. e.g. "She was my primary key...". No ALL CAPS.
- "description": 1-2 short lines, then let the script speak. <= 400 chars.
- "hashtags": array of 6-10 WITHOUT '#'. Mix: programmerhumor, coding, developer,
  softwareengineer, techhumor, and 1-2 specific to the metaphor (sql, git, etc).
  Lowercase, no spaces.
"""

DEV_HUMOR_IMAGE_SYSTEM = (
    "You write image prompts for moody, cinematic, lonely-developer aesthetic shots. "
    "Think: a single glowing monitor in a dark room, rain on a window, empty desk at "
    "3am, neon reflections, solitude. Atmospheric and emotional, never literal code. "
    "No text or words in the image."
)

DEV_HUMOR_FOOTER = """

---
Relatable developer humor, one bittersweet line at a time.
Written and voiced with AI. What's your version? Drop it in the comments."""
