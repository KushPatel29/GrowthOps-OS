"""Local, keyless sentence embeddings (all-MiniLM-L6-v2 on ONNX Runtime).

Ported from Ask Your Data's ``engine/vector_index.py`` so both apps share one
audited approach: the model archive is downloaded once, checksum-pinned, and
extracted as an allow-list of regular files; inference runs in-process with
low-memory settings. No question or business data leaves the machine.

Optional: needs ``pip install -e ".[rag]"`` (onnxruntime, tokenizers, numpy).
Without it, retrieval runs in keyword-only mode and says so.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.request
from collections.abc import Sequence
from pathlib import Path

MODEL_NAME = "all-MiniLM-L6-v2"
MODEL_URL = "https://chroma-onnx-models.s3.amazonaws.com/all-MiniLM-L6-v2/onnx.tar.gz"
MODEL_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
MODEL_FILE_SHA256 = {
    "config.json": "b567c7d5a55b636c95186aaf993f9a8920842b7e05a9e703e68b23cab2c3a670",
    "model.onnx": "4f148ba8ae9c2c7fbee4af2b132db8d06c6a6545b47fc83bbb98c3d22b8393e6",
    "special_tokens_map.json": "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    "tokenizer_config.json": "7702051bbc4953b94d47fa1d61b42ed4cbb3c71b501a8dd7183a823f8bea1f20",
    "tokenizer.json": "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0",
    "vocab.txt": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
}
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_TOKENS = 128  # questions and corpus entries are short
DIMENSIONS = 384

_lock = threading.RLock()
_embedder = None


def cache_root() -> Path:
    configured = os.getenv("GROWTHOPS_EMBEDDING_CACHE_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".cache" / "growthops" / "models"


def model_dir() -> Path:
    return cache_root() / MODEL_NAME / "onnx"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _complete(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() and _sha256(path / name) == digest
                                 for name, digest in MODEL_FILE_SHA256.items())


def runtime_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def model_ready() -> bool:
    return _complete(model_dir())


def ensure_model() -> Path:
    """Return a verified local model directory, downloading it once if needed."""
    target = model_dir()
    if _complete(target):
        return target
    with _lock:
        if _complete(target):
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="minilm-", dir=target.parent))
        try:
            archive = work / "model.tar.gz"
            request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "growthops-os"})
            size = 0
            with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise RuntimeError("embedding model archive exceeded the size limit")
                    out.write(chunk)
            if _sha256(archive) != MODEL_SHA256:
                raise RuntimeError("embedding model archive failed SHA-256 verification")
            extracted = work / "onnx"
            extracted.mkdir()
            with tarfile.open(archive, mode="r:gz") as bundle:
                for member in bundle.getmembers():
                    name = Path(member.name).name
                    if name in MODEL_FILE_SHA256 and member.isfile():
                        source = bundle.extractfile(member)
                        if source is not None:
                            with source, (extracted / name).open("wb") as out:
                                shutil.copyfileobj(source, out)
            if not _complete(extracted):
                raise RuntimeError("embedding model files failed SHA-256 verification")
            if target.exists():
                shutil.rmtree(target)
            os.replace(extracted, target)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return target


class MiniLMEmbedder:
    def __init__(self) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        directory = ensure_model()
        tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=MAX_TOKENS)
        tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=MAX_TOKENS)
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.enable_cpu_mem_arena = False  # small corpus, sequential inference: keep memory flat
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._tokenizer = tokenizer
        self._session = ort.InferenceSession(str(directory / "model.onnx"), providers=["CPUExecutionProvider"],
                                             sess_options=options)

    def __call__(self, documents: Sequence[str], batch_size: int = 32):
        import numpy as np

        if not documents:
            return np.empty((0, DIMENSIONS), dtype=np.float32)
        batches = []
        for start in range(0, len(documents), batch_size):
            encoded = [self._tokenizer.encode(str(text)) for text in documents[start:start + batch_size]]
            ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            mask = np.asarray([item.attention_mask for item in encoded], dtype=np.int64)
            hidden = self._session.run(None, {"input_ids": ids, "attention_mask": mask,
                                              "token_type_ids": np.zeros_like(ids)})[0]
            weights = np.expand_dims(mask, -1)
            pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            batches.append((pooled / np.clip(norms, 1e-12, None)).astype(np.float32))
        return np.concatenate(batches, axis=0)


def embed(documents: Sequence[str]):
    global _embedder
    with _lock:
        if _embedder is None:
            _embedder = MiniLMEmbedder()
        model = _embedder
    return model(documents)


def main() -> None:
    """``python -m growthops.embeddings``: download and verify the model (used at Docker build time)."""
    print(ensure_model())


if __name__ == "__main__":
    main()
