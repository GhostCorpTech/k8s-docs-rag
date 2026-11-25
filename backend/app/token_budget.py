from bisect import bisect_left
from pathlib import Path

from tokenizers import Tokenizer


class TokenBudget:
    """Use the cached E5 tokenizer, including passage prefix and special tokens."""

    def __init__(self, tokenizer: Tokenizer, max_tokens: int = 512, overlap: int = 64):
        self.tokenizer = tokenizer
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.max_tokens = max_tokens
        self.overlap = overlap

    @classmethod
    def from_cache(cls, cache_dir: Path, model_name: str) -> "TokenBudget":
        model_dir = cache_dir / ("models--" + model_name.replace("/", "--"))
        paths = sorted((model_dir / "snapshots").glob("*/tokenizer.json"))
        if not paths:
            raise FileNotFoundError(
                f"Cached tokenizer for {model_name} not found in {model_dir}. "
                "Download the local embedding model before indexing."
            )
        # Do not fetch a tokenizer or silently fall back to character estimates.
        return cls(Tokenizer.from_file(str(paths[-1])))

    def count(self, text: str) -> int:
        return len(self.tokenizer.encode("passage: " + text).ids)

    def windows(self, text: str, prefix: str = "") -> list[tuple[int, int, str]]:
        if not text.strip():
            return []
        offsets = self.tokenizer.encode(text, add_special_tokens=False).offsets
        available = self.max_tokens - self.count(prefix) - 4
        if available <= self.overlap:
            raise ValueError("Heading/title prefix leaves insufficient embedding context")
        result = []
        starts = [offset[0] for offset in offsets]
        index = 0
        while index < len(offsets):
            end_index = min(index + available, len(offsets))
            start = 0 if index == 0 else offsets[index][0]
            end = len(text) if end_index == len(offsets) else offsets[end_index][0]
            if end_index < len(offsets):
                floor = offsets[index + int((end_index - index) * 0.65)][0]
                for separator in ("\n\n", "\n", " "):
                    boundary = text.rfind(separator, floor, end)
                    if boundary > start:
                        end = boundary
                        end_index = bisect_left(starts, end)
                        break
            part = prefix + text[start:end].strip()
            while self.count(part) > self.max_tokens:
                end_index -= 1
                if end_index <= index:
                    raise ValueError("Unable to fit text into the embedding token budget")
                end = offsets[end_index][0]
                part = prefix + text[start:end].strip()
            if part.strip():
                result.append((start, end, part))
            if end >= len(text):
                break
            index = max(index + 1, end_index - self.overlap)
        return result
