from openai import OpenAI
import requests


class OpenAICompatibleChatProvider:
    def __init__(self, config):
        client_kwargs = {
            "api_key": config.ai_api_key or "missing-kira-ai-api-key",
            "timeout": 20,
        }
        if config.ai_base_url:
            client_kwargs["base_url"] = config.ai_base_url
        self.client = OpenAI(**client_kwargs)
        self.config = config

    def generate(self, messages, model=None, max_tokens=300, temperature=None):
        kwargs = {
            "model": model or self.config.chat_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


class GeminiChatProvider:
    def __init__(self, config):
        self.config = config
        self.api_key = config.ai_api_key or "missing-kira-ai-api-key"
        self.base_url = (config.ai_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    def _payload_from_messages(self, messages, model=None, max_tokens=300, temperature=None):
        system_parts = []
        contents = []

        for message in messages:
            role = message.get("role", "user")
            text = message.get("content", "")
            if not text:
                continue
            if role == "system":
                system_parts.append({"text": text})
            else:
                contents.append({
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": text}],
                })

        payload = {
            "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }
        if (model or "").startswith("gemini-3.7"):
            payload["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": "low",
            }
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        if temperature is not None:
            payload["generationConfig"]["temperature"] = temperature
        return payload

    def generate(self, messages, model=None, max_tokens=300, temperature=None):
        chosen_model = model or self.config.chat_model
        url = f"{self.base_url}/models/{chosen_model}:generateContent"
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            json=self._payload_from_messages(
                messages,
                model=chosen_model,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
            timeout=(5, 20),
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)


def create_chat_provider(config):
    if config.ai_provider == "gemini" or config.ai_provider.startswith("gemini-"):
        return GeminiChatProvider(config)
    return OpenAICompatibleChatProvider(config)
