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

# Appended to every script prompt. The narration is synthesized by edge-tts, which
# escapes its input into SSML (see edge_tts.Communicate.__init__) — a <break> tag
# arrives at the service as literal text and comes back out as a caption token. So
# punctuation is the ONLY pacing control the model has.
#
# Spelling numbers out the way they are said does double duty: it fixes the delivery,
# and it is also what keeps the word timings matchable back to the script, because an
# expanded token ("$1.2B" -> "1.2 billion dollars") is what desynchronises the
# punctuation recovery that drives caption phrasing.
#
# MUST contain no { or } — these templates go through .format() and a stray brace
# takes out every script generation with a KeyError.
_SPOKEN_RHYTHM = """
DELIVERY — this is read aloud by a synthetic voice, and punctuation is your only pacing control:
- Short declarative sentences, 8-14 words. Vary the length so it doesn't chant.
- Every sentence ends in a full stop, question mark or exclamation mark. Never run two
  thoughts together without one; the voice will not breathe.
- Comma for a beat. Full stop for a breath. Three dots ... for a real pause before a
  reveal - use it at most twice, and never on the final line.
- One spaced em dash - like this - for a single sharp aside, at most once.
- Never use ALL CAPS for emphasis. The voice reads unknown capitals out letter by
  letter, so NULL becomes "en you ell ell". Get emphasis from word order and sentence
  length instead.
- Write numbers and symbols exactly as they should be spoken: "1.2 billion dollars",
  "three and a half percent", "GPT 5", "four oh four". Never bare $ % & + / =.
- No semicolons, no parentheses, no brackets, no emoji, no markdown, no code snippets,
  no file paths and no URLs - the voice mangles every one of them.
"""

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
{rhythm}
- Output ONLY the narration text. No labels, no timestamps, no stage directions."""

SCRIPT_PROMPT_LONG = """Write a YouTube long-form script (spoken narration only) about:

TOPIC: {topic}

RESEARCH BRIEF:
{research}

Requirements:
- 4-6 minutes when read aloud (~700-900 words).
- The first two sentences must earn the next five minutes: open on the most
  surprising concrete fact, never with "In this video" or the topic as a question.
- Deliver a new payoff every 30-60 seconds; cut any sentence that only restates.
- Clear sections that flow, concrete examples, a memorable close.
- Separate sections with a blank line, one idea per paragraph, each ending on a
  sentence that stands alone. Long scripts are synthesized in more than one pass and
  the seam lands on a paragraph break.
- Stick to what the research brief supports. Attribute claims ("according to X",
  "reportedly") and never invent numbers, dates, quotes or details. If something
  is unconfirmed, say so plainly.
- Do NOT ask for likes, follows, comments or subscriptions.
- Conversational and natural to narrate.
{rhythm}
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

LONG_METADATA_PROMPT = """Given this long-form video script, produce YouTube publishing metadata.

TITLE HINT / TOPIC: {topic}

SCRIPT:
{script}

Return JSON with keys:

- "title": <= 70 characters. Front-load the main keyword (the person, event or
  subject people are searching for). State what the video actually covers — no
  clickbait the script doesn't deliver, no fake urgency, no ALL CAPS.

- "description": 3-4 short paragraphs, <= 1500 chars. The first two sentences
  repeat the core topic with its main keywords (search indexes these). Then
  summarize the key points covered, in order. Do NOT ask for likes or
  subscriptions. Do NOT include timestamps.

- "hashtags": array of 6-10 tags WITHOUT '#'. Mix: 2-3 broad ("news", "trending"),
  4-5 specific to the story (names, places, events). Lowercase, no spaces.
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

# Long-form draws on general trending news as well as AI stories (see
# trends.discover_mixed), and a football or entertainment story must not carry the
# "Daily AI news for developers" line above — it misdescribes the video to viewers
# and confuses the topical signal YouTube builds from descriptions. Chosen per video
# from the topic's source; see metadata_node.
GENERAL_NEWS_FOOTER = """

---
The stories people are searching for, explained in a few minutes.

This video's narration and visuals are AI-generated. Stories are sourced from public
news reporting; verify details before acting on them."""

GENERAL_METADATA_SYSTEM = (
    "You are a YouTube SEO specialist for a general news-explainer channel. You "
    "optimize for search discovery and honest click-through — never for clickbait "
    "the video doesn't deliver, because that tanks retention and channel trust."
)

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
- Loop (10%): does the last line land hard and connect back to the opening?
Punish run-on sentences and any line that can't be read aloud in one breath — a
synthetic voice only pauses where the punctuation tells it to."""

LONG_JUDGE_CRITERIA = """Audience: general viewers who clicked on a trending-news video.
- Hook (30%): do the first two sentences make skipping feel like missing out?
- Structure (25%): clear progression with a new payoff every 30-60 seconds —
  retention dies at the first stretch of filler.
- Grounding (25%): sticks to what the research brief supports, attributes claims
  ("according to...", "reportedly"), never invents specifics. Punish speculation
  stated as fact.
- Close (20%): ends with a takeaway worth commenting on, not a trailing summary.
Punish run-on sentences and any line that can't be read aloud in one breath — a
synthetic voice only pauses where the punctuation tells it to."""

HUMOR_JUDGE_CRITERIA = """Audience: developers watching relatable bittersweet comedy.
- First line (30%): the metaphor must land within 2 seconds.
- Technical accuracy (20%): every term used correctly — developers punish wrong usage.
- Ache (25%): genuinely bittersweet, not silly; the feeling underneath is real.
- Quotability (25%): is the last line something people will screenshot, quote in
  comments, and send to a friend?"""

HEARTBREAK_JUDGE_CRITERIA = """Audience: developers who feel one-sided love and share sad quote reels.
- Ache (35%): does it genuinely hurt? Pining, not comedy. No punchline that breaks the mood.
- Technical accuracy (25%): the metaphor must be exactly right — one wrong term kills it.
- Quotability (25%): would someone screenshot this, put it in their story, send it to
  the person who will never know it's about them?
- Economy (15%): every word earns its place; shorter and sharper beats clever and long."""


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
- Write technical terms the way they are said, never as symbols or capitals: "null",
  "not equal", "four oh four", "detached head" - not NULL, != or 404.
{rhythm}

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


# ============================================================================
# CODE HEARTBREAK content type — sad one-sided-love coding quotes
# (inspired by quote-reel accounts like @codemetaphor / "broken developer")
#
# Distinct from dev_humor: humor is bittersweet COMEDY (30-45s monologue, a nod
# and a laugh); heartbreak is pure PINING (15-25s poetic quote, no punchline).
# Themes here stay strictly in one-sided-love territory so the two streams don't
# blur together on the channel.
# ============================================================================

CODE_HEARTBREAK_SYSTEM = (
    "You write short, devastating one-sided-love quotes for developers, using "
    "programming concepts as metaphors for pining, unsaid feelings, and watching "
    "someone from afar. Poetic and precise, never silly, never a joke. Every "
    "technical term must be used exactly correctly — your audience are developers "
    "who will feel seen only if the metaphor truly holds."
)

# Curated one-sided-love setups. Deliberately non-overlapping with
# DEV_HUMOR_THEMES: those cover breakups and loneliness broadly; these stay on
# unconfessed / unreturned love only.
CODE_HEARTBREAK_THEMES = [
    "loving her in silence, as a background process (always running, never in the foreground, she never reads the logs)",
    "a crush I never confessed, as a commented-out line (written with feeling, never executed, still in the source)",
    "waiting for her message, as long polling (I keep the connection open, the server never responds, I retry anyway)",
    "she loves someone else, as a foreign key (she references him, I reference her, the constraint will never let me in)",
    "being her backup plan, as a fallback server (I only get traffic when he is down, and I still answer every time)",
    "my feelings as an environment variable (present everywhere, visible nowhere, she never printed them)",
    "her name in my head, as a memory leak (allocated years ago, unreachable now, never freed)",
    "almost telling her, as a race condition (every time I reached for the words, someone else got there first)",
    "loving her quietly, as localhost (everything runs perfectly here, and no one outside can ever reach it)",
    "watching her life from afar, as a read replica (I receive every update, I can never write back)",
    "the message I typed and deleted, as a rollback (the transaction was ready, I never committed)",
    "she keeps me pending, as a promise (never resolved, never rejected, I stay in the queue forever)",
    "hoping she notices me, as SEO (I optimized everything about myself, she never searches for me)",
    "my unanswered love as ping requests (echo request sent, no reply, I tell myself the packet got lost)",
    "she replies but never texts first, as a webhook (I only exist when something happens on her side)",
    "a love that never shipped (feature-complete on my side, she never reviewed the pull request)",
    "the seat next to her, as a reserved keyword (I can see it, I can want it, I can never use it)",
    "my heart as an open-source repo (anyone can read it, she never starred it)",
    "our almost, versioned (v1 was a crush, v2 was hope, she deprecated us before v3)",
    "being just her friend, in HTTP (every request returns 200, but the body is always empty)",
]

CODE_HEARTBREAK_SCRIPT_PROMPT = """Write a YouTube SHORTS script: a short, aching one-sided-love quote for developers.

THEME: {topic}

Write a 15-25 second piece (~40-70 words) that:
- Extends ONE programming metaphor for unconfessed or unreturned love all the way through.
- Opens mid-ache — the first line should read like a thought they've had at 2am.
- Is technically exact: a developer must nod at every term, or the spell breaks.
- Is NOT funny. No punchline, no wink. Pure pining, quiet and dignified.
- Ends on the single most devastating line — the one people screenshot and send
  to someone who will never know it's about them.
- Reads naturally aloud, slowly. Short sentences. No stage directions, no emojis.
- One thought per sentence, a full stop after each. The pauses are the poem.
- Write technical terms the way they are said, never as symbols or capitals: "null",
  "two hundred OK", "primary key" - not NULL, 200 or !=.
{rhythm}

Example of the TONE (do not reuse it):
"I run on her localhost. Every feature works when it's just us. But she never
deployed me. Some things are only meant to run in private."

Output ONLY the quote text."""

CODE_HEARTBREAK_METADATA_PROMPT = """Produce YouTube metadata for this sad one-sided-love developer quote short.

SCRIPT:
{script}

Return JSON:
- "title": <= 60 chars, an unfinished ache that hints at the metaphor without
  spoiling the last line. e.g. "I kept the connection open...". No ALL CAPS.
- "description": 1-2 quiet lines, then let the quote speak. <= 400 chars.
- "hashtags": array of 6-10 WITHOUT '#'. Mix: codelove, sadquotes, developer,
  programmerlife, brokenheart, relatable, and 1-2 specific to the metaphor
  (sql, http, git, etc). Lowercase, no spaces.
"""

CODE_HEARTBREAK_IMAGE_SYSTEM = (
    "You write image prompts for melancholic, cinematic, sad-quote-reel aesthetics. "
    "Think: rain streaking a night window, empty late-night train, city lights out "
    "of focus, a dim room lit by a phone screen, wet streets reflecting neon, one "
    "person alone in a wide frame. Soft, muted, blue-hour tones. Emotional and "
    "atmospheric, never literal code, no people's faces in close-up. "
    "No text or words in the image."
)

CODE_HEARTBREAK_FOOTER = """

---
For every developer who loved in silence.
Written and voiced with AI. Send this to no one. They wouldn't get it anyway."""


# Fold the shared delivery block into every script template once, at import.
# `str.replace` rather than `.format` on purpose: the templates still hold their own
# {topic} / {research} / {script} placeholders for their callers to fill, and this
# must not consume them. `_SPOKEN_RHYTHM` contains no braces, so the result stays
# safe to .format() later.
SCRIPT_PROMPT_SHORT = SCRIPT_PROMPT_SHORT.replace("{rhythm}", _SPOKEN_RHYTHM)
SCRIPT_PROMPT_LONG = SCRIPT_PROMPT_LONG.replace("{rhythm}", _SPOKEN_RHYTHM)
DEV_HUMOR_SCRIPT_PROMPT = DEV_HUMOR_SCRIPT_PROMPT.replace("{rhythm}", _SPOKEN_RHYTHM)
CODE_HEARTBREAK_SCRIPT_PROMPT = CODE_HEARTBREAK_SCRIPT_PROMPT.replace(
    "{rhythm}", _SPOKEN_RHYTHM
)
