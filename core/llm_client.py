"""Backward-compatible aliases for the old client module path.

New code should import from ``core.provider_client``.
"""

from core.provider_client import ChatCompletionsClient, FastChatCompletionsClient


LLMClient = ChatCompletionsClient
LLMClientFast = FastChatCompletionsClient
