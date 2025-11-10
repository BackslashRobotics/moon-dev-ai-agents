"""
xAI Grok Model Implementation
"""

import os
from openai import OpenAI

# FORCE termcolor to always output ANSI codes when FORCE_COLOR is set
_force_color = os.getenv('FORCE_COLOR') == '1'
if _force_color:
    # Color code mappings for ANSI
    _COLOR_CODES = {
        'grey': 30, 'red': 31, 'green': 32, 'yellow': 33,
        'blue': 34, 'magenta': 35, 'cyan': 36, 'white': 37,
    }
    
    def cprint(text, color=None, on_color=None, attrs=None, **kwargs):
        """Drop-in replacement for termcolor.cprint that ALWAYS outputs ANSI codes"""
        if color and color in _COLOR_CODES:
            code = _COLOR_CODES[color]
            print(f'\x1b[{code}m{text}\x1b[0m', **kwargs)
        else:
            print(text, **kwargs)
else:
    from termcolor import cprint

from .base_model import BaseModel, ModelResponse

class XAIModel(BaseModel):
    """Implementation for xAI's Grok models"""

    AVAILABLE_MODELS = {
        "grok-code-fast-1": {
            "description": "Fast code-specialized model",
            "context_window": "256K tokens",
            "pricing": "$0.20 - $1.50 per million tokens",
            "rate_limits": "2M tpm, 480 rpm"
        },
        "grok-4-fast-reasoning": {
            "description": "Grok 4 fast with reasoning capabilities",
            "context_window": "2M tokens",
            "pricing": "$0.20 - $0.50 per million tokens",
            "rate_limits": "4M tpm, 480 rpm"
        },
        "grok-4-fast-non-reasoning": {
            "description": "Grok 4 fast without reasoning",
            "context_window": "2M tokens",
            "pricing": "$0.20 - $0.50 per million tokens",
            "rate_limits": "4M tpm, 480 rpm"
        },
        "grok-4-0709": {
            "description": "Grok 4 flagship model (most intelligent)",
            "context_window": "256K tokens",
            "pricing": "$3.00 - $15.00 per million tokens",
            "rate_limits": "2M tpm, 480 rpm"
        },
        "grok-3-mini": {
            "description": "Compact Grok 3 model",
            "context_window": "131K tokens",
            "pricing": "$0.30 - $0.50 per million tokens",
            "rate_limits": "480 rpm"
        },
        "grok-3": {
            "description": "Grok 3 flagship model",
            "context_window": "131K tokens",
            "pricing": "$3.00 - $15.00 per million tokens",
            "rate_limits": "600 rpm"
        }
    }

    def __init__(self, api_key: str, model_name: str = "grok-4-fast-reasoning", base_url: str = "https://api.x.ai/v1", **kwargs):
        self.model_name = model_name
        self.base_url = base_url
        super().__init__(api_key, **kwargs)

    def initialize_client(self, **kwargs) -> None:
        """Initialize the xAI Grok client"""
        try:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            cprint(f"Initialized xAI Grok model: {self.model_name}", "green")

            # Show model info if available
            model_info = self.AVAILABLE_MODELS.get(self.model_name, {})
            if model_info:
                if "context_window" in model_info:
                    cprint(f"Context window: {model_info['context_window']}", "cyan")
                if "pricing" in model_info:
                    cprint(f"Pricing: {model_info['pricing']}", "cyan")
                if "rate_limits" in model_info:
                    cprint(f"Rate limits: {model_info['rate_limits']}", "cyan")

        except Exception as e:
            cprint(f"Failed to initialize xAI Grok model: {str(e)}", "red")
            self.client = None

    def generate_response(self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs
    ) -> ModelResponse:
        """Generate a response using xAI Grok"""
        try:
            cprint(f"{self.model_name} is thinking...", "yellow")

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False
            )

            content = response.choices[0].message.content.strip()
            
            cprint("Grok response received!", "green")

            return ModelResponse(
                content=content,
                raw_response=response,
                model_name=self.model_name,
                usage=response.usage.model_dump() if hasattr(response, 'usage') else None
            )

        except Exception as e:
            cprint(f"xAI Grok generation error: {str(e)}", "red")
            raise

    def is_available(self) -> bool:
        """Check if xAI Grok is available"""
        return self.client is not None

    @property
    def model_type(self) -> str:
        return "xai"
