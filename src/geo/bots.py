"""AI crawler taxonomy. Training vs answer-time retrieval are reported separately:
conflating them produces advice that is wrong in both directions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Group = Literal["retrieval", "training"]


@dataclass(frozen=True)
class Bot:
    token: str  # robots.txt product token
    group: Group
    operator: str
    purpose: str
    user_agent: str  # full UA string (used only by the opt-in WAF probe)
    honors_robots: bool = True  # user-triggered fetchers may not
    note: str = ""


_MOZ = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible;"

BOTS: tuple[Bot, ...] = (
    # --- answer-time retrieval: what AI can say about the business right now ---
    Bot("OAI-SearchBot", "retrieval", "OpenAI", "ChatGPT search index",
        f"{_MOZ} OAI-SearchBot/1.0; +https://openai.com/searchbot"),
    Bot("ChatGPT-User", "retrieval", "OpenAI", "user-triggered page fetch in ChatGPT",
        f"{_MOZ} ChatGPT-User/1.0; +https://openai.com/bot", honors_robots=False,
        note="OpenAI states robots.txt may not apply to user-initiated fetches"),
    Bot("Claude-SearchBot", "retrieval", "Anthropic", "Claude search index",
        f"{_MOZ} Claude-SearchBot/1.0; +Claude-SearchBot@anthropic.com"),
    Bot("Claude-User", "retrieval", "Anthropic", "user-triggered page fetch in Claude",
        f"{_MOZ} Claude-User/1.0; +Claude-User@anthropic.com"),
    Bot("PerplexityBot", "retrieval", "Perplexity", "Perplexity search index",
        f"{_MOZ} PerplexityBot/1.0; +https://perplexity.ai/perplexitybot"),
    Bot("Perplexity-User", "retrieval", "Perplexity", "user-triggered page fetch in Perplexity",
        f"{_MOZ} Perplexity-User/1.0; +https://perplexity.ai/perplexity-user", honors_robots=False,
        note="Perplexity states user-triggered fetches generally ignore robots.txt"),
    Bot("Googlebot", "retrieval", "Google", "Google Search index (feeds AI Overviews)",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    # --- training: affects future model knowledge; blocking is a policy choice ---
    Bot("GPTBot", "training", "OpenAI", "model training",
        f"{_MOZ} GPTBot/1.1; +https://openai.com/gptbot"),
    Bot("ClaudeBot", "training", "Anthropic", "model training",
        f"{_MOZ} ClaudeBot/1.0; +claudebot@anthropic.com"),
    Bot("CCBot", "training", "Common Crawl", "open crawl used widely for training",
        "CCBot/2.0 (https://commoncrawl.org/faq/)"),
    Bot("Google-Extended", "training", "Google", "Gemini training/grounding opt-out token (not a crawler)",
        "", note="robots.txt control token only; Googlebot does the fetching"),
    Bot("Applebot-Extended", "training", "Apple", "Apple AI training opt-out token (not a crawler)",
        "", note="robots.txt control token only; Applebot does the fetching"),
    Bot("Meta-ExternalAgent", "training", "Meta", "model training",
        "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)"),
    Bot("Bytespider", "training", "ByteDance", "model training",
        "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)"),
)

RETRIEVAL = tuple(b for b in BOTS if b.group == "retrieval")
TRAINING = tuple(b for b in BOTS if b.group == "training")


def slug(token: str) -> str:
    return token.lower().replace("-", "_")
