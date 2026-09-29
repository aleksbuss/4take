def summarise(samples):
    ok = [s for s in samples if s["ok"]]
    return {
        "count": len(samples),
        "success_rate": len(ok) / len(samples),
        "p50_ms": sorted(s["latency_ms"] for s in ok)[len(ok) // 2],
    }
