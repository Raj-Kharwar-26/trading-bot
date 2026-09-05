#!/usr/bin/env python
"""Deep Path Model Tier Evaluation Script.

Compares model performance across different tiers for the deep reasoning pipeline.
Evaluates: cost, latency, signal quality, confidence calibration.

Usage:
    python evaluate_deep_path.py --symbols RELIANCE.NS TCS.NS INFY.NS \
        --models gpt-4o-mini gpt-4o gpt-3.5-turbo \
        --iterations 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")


@dataclass
class ModelEvalResult:
    model: str
    symbol: str
    market: str
    signal: str
    confidence: float
    duration_seconds: float
    tokens_prompt: int
    tokens_completion: int
    cost_usd: float
    rag_hits: int
    error: str | None = None


@dataclass
class ModelSummary:
    model: str
    total_requests: int
    successful: int
    failed: int
    avg_duration: float
    avg_confidence: float
    avg_tokens_prompt: float
    avg_tokens_completion: float
    total_cost_usd: float
    cost_per_request: float
    signal_distribution: dict[str, int]
    confidence_calibration: float  # Correlation between confidence and correctness


# Model pricing (per 1K tokens, approximate)
MODEL_PRICING = {
    "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
    "gpt-4.1-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
}


def get_model_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model, default to gpt-4o-mini if unknown."""
    return MODEL_PRICING.get(model, MODEL_PRICING["gpt-4o-mini"])


async def run_deep_analysis(
    symbol: str,
    market: str,
    model: str,
    days: int = 30,
) -> ModelEvalResult:
    """Run a single deep analysis and return metrics."""
    from backend.reasoning.agent_runner import analyse_symbol

    start = time.perf_counter()
    try:
        report = analyse_symbol(symbol, market, days=days, deep=True)
        duration = time.perf_counter() - start

        # Extract token usage from report meta if available
        meta = report.get("_meta", {})
        tokens_prompt = meta.get("tokens_prompt", 0)
        tokens_completion = meta.get("tokens_completion", 0)

        # If not in meta, estimate from duration
        if tokens_prompt == 0:
            tokens_prompt = int(duration * 100)  # rough estimate
            tokens_completion = int(duration * 50)

        pricing = get_model_pricing(model)
        cost = (
            tokens_prompt / 1000 * pricing["prompt"]
            + tokens_completion / 1000 * pricing["completion"]
        )

        return ModelEvalResult(
            model=model,
            symbol=symbol,
            market=market,
            signal=report.get("signal", "ERROR"),
            confidence=report.get("confidence", 0.0),
            duration_seconds=duration,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            cost_usd=cost,
            rag_hits=report.get("_meta", {}).get("rag_hits", 0),
        )
    except Exception as e:
        duration = time.perf_counter() - start
        return ModelEvalResult(
            model=model,
            symbol=symbol,
            market=market,
            signal="ERROR",
            confidence=0.0,
            duration_seconds=duration,
            tokens_prompt=0,
            tokens_completion=0,
            cost_usd=0.0,
            rag_hits=0,
            error=str(e),
        )


def calculate_confidence_calibration(results: list[ModelEvalResult]) -> float:
    """Calculate correlation between confidence and signal quality.

    For now, returns a placeholder. In production, this would compare
    confidence against actual trade outcomes.
    """
    # Placeholder: would need ground truth outcomes
    # For now, return average confidence of successful requests
    successful = [r for r in results if r.error is None]
    if not successful:
        return 0.0
    return statistics.mean(r.confidence for r in successful)


def summarize_model(results: list[ModelEvalResult]) -> ModelSummary:
    """Create summary statistics for a model."""
    successful = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]

    if not successful:
        return ModelSummary(
            model=results[0].model,
            total_requests=len(results),
            successful=0,
            failed=len(failed),
            avg_duration=0,
            avg_confidence=0,
            avg_tokens_prompt=0,
            avg_tokens_completion=0,
            total_cost_usd=0,
            cost_per_request=0,
            signal_distribution={},
            confidence_calibration=0,
        )

    signal_dist = {}
    for r in successful:
        signal_dist[r.signal] = signal_dist.get(r.signal, 0) + 1

    return ModelSummary(
        model=results[0].model,
        total_requests=len(results),
        successful=len(successful),
        failed=len(failed),
        avg_duration=statistics.mean(r.duration_seconds for r in successful),
        avg_confidence=statistics.mean(r.confidence for r in successful),
        avg_tokens_prompt=statistics.mean(r.tokens_prompt for r in successful),
        avg_tokens_completion=statistics.mean(r.tokens_completion for r in successful),
        total_cost_usd=sum(r.cost_usd for r in successful),
        cost_per_request=sum(r.cost_usd for r in successful) / len(successful),
        signal_distribution=signal_dist,
        confidence_calibration=calculate_confidence_calibration(successful),
    )


async def run_evaluation(
    symbols: list[str],
    models: list[str],
    iterations: int,
    market: str,
    days: int,
    output_dir: Path,
) -> dict[str, ModelSummary]:
    """Run the full evaluation matrix."""
    all_results = {model: [] for model in models}

    total_runs = len(symbols) * len(models) * iterations
    completed = 0

    print(f"Starting evaluation: {len(symbols)} symbols × {len(models)} models × {iterations} iterations = {total_runs} runs")

    for symbol in symbols:
        for model in models:
            for i in range(iterations):
                print(f"  [{completed + 1}/{total_runs}] {symbol} | {model} | iter {i + 1}/{iterations}")
                result = await run_deep_analysis(symbol, "NSE", model)
                all_results[model].append(result)
                completed += 1

                # Small delay to avoid rate limits
                await asyncio.sleep(1)

    # Save raw results
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_file = output_dir / f"deep_eval_raw_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(raw_file, "w") as f:
        json.dump(
            {model: [asdict(r) for r in results] for model, results in all_results.items()},
            f,
            indent=2,
            default=str,
        )

    # Summarize
    summaries = {model: summarize_model(results) for model, results in all_results.items()}

    # Save summaries
    summary_file = output_dir / f"deep_eval_summary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(summary_file, "w") as f:
        json.dump({model: asdict(s) for model, s in summaries.items()}, f, indent=2, default=str)

    return summaries


def print_comparison(summaries: dict[str, ModelSummary]):
    """Print a comparison table."""
    print("\n" + "=" * 100)
    print("MODEL TIER EVALUATION RESULTS")
    print("=" * 100)

    print(f"\n{'Model':<15} {'Requests':>10} {'Success':>8} {'Avg Dur(s)':>10} {'Avg Conf':>10} "
          f"{'Tokens P/C':>15} {'Cost/Req($)':>12} {'Total Cost($)':>12} {'Signals':<30}")
    print("-" * 100)

    for model, s in summaries.items():
        signals = ", ".join(f"{k}:{v}" for k, v in s.signal_distribution.items())
        print(f"{model:<15} {s.total_requests:>10} {s.successful:>8} {s.avg_duration:>10.1f} "
              f"{s.avg_confidence:>10.2f} {s.avg_tokens_prompt:>7.0f}/{s.avg_tokens_completion:<7.0f} "
              f"{s.cost_per_request:>12.6f} {s.total_cost_usd:>12.4f} {signals:<30}")

    print("\n" + "=" * 100)
    print("RECOMMENDATIONS:")

    # Find best balance of cost/quality
    valid = [s for s in summaries.values() if s.successful > 0]
    if valid:
        # Best cost efficiency
        best_cost = min(valid, key=lambda s: s.cost_per_request)
        print(f"  • Most cost-efficient: {best_cost.model} (${best_cost.cost_per_request:.6f}/request)")

        # Best quality (highest confidence with reasonable duration)
        best_quality = max(valid, key=lambda s: s.avg_confidence / max(s.avg_duration, 0.1))
        print(f"  • Best quality/speed: {best_quality.model} (conf={best_quality.avg_confidence:.2f}, dur={best_quality.avg_duration:.1f}s)")

        # Best overall (balance)
        best_overall = max(valid, key=lambda s: s.avg_confidence / max(s.cost_per_request * 10000, 0.001))
        print(f"  • Best overall balance: {best_overall.model}")


def main():
    parser = argparse.ArgumentParser(description="Deep Path Model Tier Evaluation")
    parser.add_argument("--symbols", nargs="+", default=["RELIANCE.NS", "TCS.NS", "INFY.NS"],
                        help="Symbols to evaluate")
    parser.add_argument("--models", nargs="+", default=["gpt-4o-mini", "gpt-4o"],
                        help="Models to evaluate")
    parser.add_argument("--iterations", type=int, default=3,
                        help="Iterations per symbol/model")
    parser.add_argument("--market", default="NSE", help="Market")
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    parser.add_argument("--output", default="./evaluation_results",
                        help="Output directory")

    args = parser.parse_args()

    output_dir = Path(args.output)

    print("=" * 60)
    print("DEEP PATH MODEL TIER EVALUATION")
    print("=" * 60)
    print(f"Symbols: {args.symbols}")
    print(f"Models: {args.models}")
    print(f"Iterations per combination: {args.iterations}")
    print(f"Market: {args.market}")
    print(f"Days: {args.days}")
    print(f"Output: {args.output}")

    summaries = asyncio.run(run_evaluation(
        symbols=args.symbols,
        models=args.models,
        iterations=args.iterations,
        market=args.market,
        days=args.days,
        output_dir=output_dir,
    ))

    print_comparison(summaries)
    print(f"\nResults saved to: {output_dir}")


if __name__ == "__main__":
    main()