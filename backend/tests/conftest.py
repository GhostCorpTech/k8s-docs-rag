import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from app.docs import Chunker
from app.token_budget import TokenBudget


@pytest.fixture
def token_budget() -> TokenBudget:
    # Tiny byte tokenizer: tests require neither network access nor a downloaded model.
    alphabet = sorted(pre_tokenizers.ByteLevel.alphabet())
    vocab = {char: index for index, char in enumerate(alphabet)}
    vocab.update({"<s>": 256, "</s>": 257})
    tokenizer = Tokenizer(models.BPE(vocab=vocab, merges=[]))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.post_processor = processors.TemplateProcessing(
        single="<s> $A </s>", special_tokens=[("<s>", 256), ("</s>", 257)]
    )
    return TokenBudget(tokenizer)


@pytest.fixture
def chunker(token_budget: TokenBudget) -> Chunker:
    return Chunker(token_budget)
