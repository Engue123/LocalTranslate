"""
Benchmarking utilities to auto-tune batch size based on hardware.
"""
import time
from typing import List, Callable


def measure_throughput(translator_fn: Callable[[List[str]], List[str]], sample_texts: List[str], batch_sizes: List[int]) -> dict:
    """
    Test different batch sizes and return the fastest configuration.
    """
    results = {}
    for bs in batch_sizes:
        batches = [sample_texts[i:i+bs] for i in range(0, len(sample_texts), bs)]
        start = time.time()
        for b in batches:
            translator_fn(b)
        elapsed = time.time() - start
        results[bs] = {
            "total_time": elapsed,
            "per_batch": elapsed / len(batches) if batches else 0,
        }
    best = min(results, key=lambda k: results[k]["per_batch"])
    return {"best_batch_size": best, "results": results}
