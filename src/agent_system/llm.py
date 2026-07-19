import os
from functools import cache

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
import logging

logger = logging.getLogger(__name__)


def extract_json(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


@cache
def get_llm(temperature: float = 0.0, max_tokens: int = 2000):
    groq_api_key = os.environ.get('GROQ_API_KEY')

    if groq_api_key:
        logger.debug("Using Groq LLM (temperature=%s, max_tokens=%s)", temperature, max_tokens)
        return ChatGroq(
            model="llama-3.1-8b-instant",
            temperature=temperature,
            max_tokens=max_tokens,
        )

    base_url = os.environ.get("VLLM_BASE_URL", "http://localhost:8080/v1")
    logger.warning("GROQ_API_KEY not set, falling back to vLLM at %s", base_url)

    return ChatOpenAI(
        model="meta-llama/Meta-Llama-3.1-8B-Instruct",
        temperature=temperature,
        max_tokens=max_tokens,
        base_url=base_url,
        api_key='empty',
    )
