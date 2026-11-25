from functools import lru_cache
from pathlib import Path
from threading import Lock

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

E5_BASE = "intfloat/multilingual-e5-base"
_MODEL_LOAD_LOCK = Lock()


def _register_custom_model(model_name: str) -> None:
    supported = {item["model"] for item in TextEmbedding.list_supported_models()}
    if model_name in supported:
        return
    if model_name != E5_BASE:
        raise ValueError(
            f"Local model {model_name!r} is not built into FastEmbed. "
            f"Only {E5_BASE!r} is registered automatically."
        )
    TextEmbedding.add_custom_model(
        model=model_name,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=model_name),
        dim=768,
        model_file="onnx/model.onnx",
        size_in_gb=1.11,
    )


@lru_cache(maxsize=2)
def get_local_model(model_name: str, cache_dir: str, threads: int) -> TextEmbedding:
    with _MODEL_LOAD_LOCK:
        _register_custom_model(model_name)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        cache = Path(cache_dir) / ("models--" + model_name.replace("/", "--")) / "snapshots"
        cached = any(
            (path / "onnx" / "model.onnx").is_file() and (path / "tokenizer.json").is_file()
            for path in cache.glob("*")
        )
        return TextEmbedding(
            model_name=model_name, cache_dir=cache_dir, threads=threads, local_files_only=cached
        )


def embed_local(
    texts: list[str],
    *,
    kind: str,
    model_name: str,
    cache_dir: Path,
    threads: int,
    batch_size: int,
) -> list[list[float]]:
    prefix = "query: " if kind == "query" else "passage: "
    prepared = [prefix + text for text in texts]
    model = get_local_model(model_name, str(cache_dir), threads)
    return [vector.tolist() for vector in model.embed(prepared, batch_size=batch_size)]
