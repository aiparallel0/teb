"""
teb Performance Benchmark Suite
================================

Measures core performance characteristics across the Goal → Decompose →
Execute → Measure loop. All benchmarks use a temporary SQLite database
and require zero AI keys (template mode only).

Run from the repo root:

    python -m benchmarks.bench

"""
from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ─── Result container ─────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """Stores timing data for a single benchmark."""
    name: str
    iterations: int
    latencies_ns: List[float] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(self.latencies_ns) / 1e9

    @property
    def ops_per_sec(self) -> float:
        total = self.total_seconds
        return self.iterations / total if total > 0 else float("inf")

    @property
    def avg_ms(self) -> float:
        return (statistics.mean(self.latencies_ns) / 1e6) if self.latencies_ns else 0.0

    @property
    def p50_ms(self) -> float:
        return self._percentile(50)

    @property
    def p95_ms(self) -> float:
        return self._percentile(95)

    @property
    def p99_ms(self) -> float:
        return self._percentile(99)

    def _percentile(self, pct: float) -> float:
        if not self.latencies_ns:
            return 0.0
        sorted_vals = sorted(self.latencies_ns)
        idx = math.ceil(len(sorted_vals) * pct / 100) - 1
        idx = max(0, min(idx, len(sorted_vals) - 1))
        return sorted_vals[idx] / 1e6


# ─── Runner helpers ───────────────────────────────────────────────────────────

def _time_op(fn: Callable[[], Any]) -> float:
    """Run fn once and return elapsed nanoseconds."""
    start = time.perf_counter_ns()
    fn()
    return time.perf_counter_ns() - start


def run_benchmark(name: str, fn: Callable[[], Any], n: int, setup: Optional[Callable[[], None]] = None) -> BenchmarkResult:
    """Execute *fn* N times, collecting per-iteration latencies."""
    if setup:
        setup()
    result = BenchmarkResult(name=name, iterations=n)
    for _ in range(n):
        result.latencies_ns.append(_time_op(fn))
    return result


# ─── Temporary DB context ─────────────────────────────────────────────────────

class _TempDB:
    """Context manager that creates an isolated SQLite database for benchmarks.

    Optionally seeds a test user (needed for any operation that references
    user_id with a foreign-key constraint, such as CSV/Trello imports).
    """

    def __init__(self, *, seed_user: bool = False) -> None:
        self._tmpdir: Optional[tempfile.TemporaryDirectory] = None
        self._seed_user = seed_user
        self.user_id: Optional[int] = None

    def __enter__(self) -> "_TempDB":
        from teb import storage
        from teb.models import User

        self._tmpdir = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmpdir.name) / "bench.db")
        storage.set_db_path(db_path)
        storage.init_db()

        if self._seed_user:
            user = storage.create_user(User(
                email="bench@teb.local",
                password_hash="$2b$12$benchmarkplaceholderhash000000000000000000000000",
            ))
            self.user_id = user.id

        return self

    def __exit__(self, *exc: Any) -> None:
        from teb import storage

        storage.set_db_path(None)  # type: ignore[arg-type]
        if self._tmpdir:
            self._tmpdir.cleanup()


# ─── Individual benchmarks ────────────────────────────────────────────────────

def bench_decompose_template(n: int = 200) -> BenchmarkResult:
    """Measure template-mode decomposition speed (no AI required)."""
    from teb import storage
    from teb.decomposer import decompose_template
    from teb.models import Goal

    with _TempDB():
        # Pre-create goals of different types
        goals = []
        titles = [
            "earn $1000 freelancing online",
            "learn Python programming from scratch",
            "build a SaaS product for task management",
            "launch a personal blog",
            "get fit and lose 20 pounds",
        ]
        for title in titles:
            g = Goal(title=title, description=f"Goal: {title}")
            g = storage.create_goal(g)
            goals.append(g)

        idx = 0

        def op() -> None:
            nonlocal idx
            decompose_template(goals[idx % len(goals)])
            idx += 1

        return run_benchmark("Decompose (template)", op, n)


def bench_task_create(n: int = 1000) -> BenchmarkResult:
    """Measure task creation throughput."""
    from teb import storage
    from teb.models import Goal, Task

    with _TempDB():
        goal = storage.create_goal(Goal(title="bench", description="benchmark goal"))
        counter = 0

        def op() -> None:
            nonlocal counter
            counter += 1
            storage.create_task(Task(
                goal_id=goal.id,
                title=f"Task {counter}",
                description="Benchmark task",
                estimated_minutes=30,
                order_index=counter,
            ))

        return run_benchmark("Task CREATE", op, n)


def bench_task_read(n: int = 2000) -> BenchmarkResult:
    """Measure single-task read latency."""
    from teb import storage
    from teb.models import Goal, Task

    with _TempDB():
        goal = storage.create_goal(Goal(title="bench", description="benchmark goal"))
        task_ids: List[int] = []
        for i in range(100):
            t = storage.create_task(Task(
                goal_id=goal.id, title=f"Task {i}", description="d", order_index=i,
            ))
            task_ids.append(t.id)

        idx = 0

        def op() -> None:
            nonlocal idx
            storage.get_task(task_ids[idx % len(task_ids)])
            idx += 1

        return run_benchmark("Task READ", op, n)


def bench_task_update(n: int = 500) -> BenchmarkResult:
    """Measure task update throughput (with optimistic concurrency)."""
    from teb import storage
    from teb.models import Goal, Task

    with _TempDB():
        goal = storage.create_goal(Goal(title="bench", description="benchmark goal"))
        tasks: List[Task] = []
        for i in range(50):
            t = storage.create_task(Task(
                goal_id=goal.id, title=f"Task {i}", description="d", order_index=i,
            ))
            tasks.append(t)

        idx = 0

        def op() -> None:
            nonlocal idx
            t = tasks[idx % len(tasks)]
            t.description = f"updated-{idx}"
            storage.update_task(t)
            idx += 1

        return run_benchmark("Task UPDATE", op, n)


def bench_task_delete(n: int = 500) -> BenchmarkResult:
    """Measure task deletion throughput."""
    from teb import storage
    from teb.models import Goal, Task

    with _TempDB():
        goal = storage.create_goal(Goal(title="bench", description="benchmark goal"))
        task_ids: List[int] = []
        for i in range(n):
            t = storage.create_task(Task(
                goal_id=goal.id, title=f"Task {i}", description="d", order_index=i,
            ))
            task_ids.append(t.id)

        idx = 0

        def op() -> None:
            nonlocal idx
            storage.delete_task(task_ids[idx])
            idx += 1

        return run_benchmark("Task DELETE", op, n)


def bench_goal_create(n: int = 500) -> BenchmarkResult:
    """Measure goal creation throughput."""
    from teb import storage
    from teb.models import Goal

    with _TempDB():
        counter = 0

        def op() -> None:
            nonlocal counter
            counter += 1
            storage.create_goal(Goal(
                title=f"Goal {counter}",
                description=f"Benchmark goal number {counter}",
            ))

        return run_benchmark("Goal CREATE", op, n)


def bench_goal_read(n: int = 2000) -> BenchmarkResult:
    """Measure single-goal read latency."""
    from teb import storage
    from teb.models import Goal

    with _TempDB():
        goal_ids: List[int] = []
        for i in range(100):
            g = storage.create_goal(Goal(title=f"Goal {i}", description="d"))
            goal_ids.append(g.id)

        idx = 0

        def op() -> None:
            nonlocal idx
            storage.get_goal(goal_ids[idx % len(goal_ids)])
            idx += 1

        return run_benchmark("Goal READ", op, n)


def bench_goal_update(n: int = 500) -> BenchmarkResult:
    """Measure goal update throughput."""
    from teb import storage
    from teb.models import Goal

    with _TempDB():
        goals: List[Goal] = []
        for i in range(50):
            g = storage.create_goal(Goal(title=f"Goal {i}", description="d"))
            goals.append(g)

        idx = 0

        def op() -> None:
            nonlocal idx
            g = goals[idx % len(goals)]
            g.description = f"updated-{idx}"
            storage.update_goal(g)
            idx += 1

        return run_benchmark("Goal UPDATE", op, n)


def bench_goal_delete(n: int = 200) -> BenchmarkResult:
    """Measure goal deletion throughput."""
    from teb import storage
    from teb.models import Goal

    with _TempDB():
        goal_ids: List[int] = []
        for i in range(n):
            g = storage.create_goal(Goal(title=f"Goal {i}", description="d"))
            goal_ids.append(g.id)

        idx = 0

        def op() -> None:
            nonlocal idx
            storage.delete_goal(goal_ids[idx])
            idx += 1

        return run_benchmark("Goal DELETE", op, n)


def bench_dag_validate(n: int = 500) -> BenchmarkResult:
    """Measure DAG validation speed over a 50-task graph."""
    from teb.dag import validate_dag
    from teb.models import Task

    # Build a synthetic task graph: 50 tasks with linear + fan-out deps
    tasks: List[Task] = []
    for i in range(50):
        deps: List[int] = []
        if i > 0:
            deps.append(i)  # depends on previous task
        if i > 5:
            deps.append(i - 5)  # fan-out dependency
        t = Task(
            goal_id=1, title=f"Task {i+1}", description="d",
            id=i + 1, order_index=i,
            depends_on=json.dumps(deps),
        )
        tasks.append(t)

    def op() -> None:
        validate_dag(tasks)

    return run_benchmark("DAG validate", op, n)


def bench_dag_execution_plan(n: int = 500) -> BenchmarkResult:
    """Measure execution plan generation for 50 tasks."""
    from teb.dag import build_execution_plan
    from teb.models import Task

    tasks: List[Task] = []
    for i in range(50):
        deps: List[int] = []
        if i > 0:
            deps.append(i)
        t = Task(
            goal_id=1, title=f"Task {i+1}", description="d",
            id=i + 1, order_index=i, status="todo",
            depends_on=json.dumps(deps),
        )
        tasks.append(t)

    def op() -> None:
        build_execution_plan(tasks)

    return run_benchmark("DAG exec plan", op, n)


def bench_dag_critical_path(n: int = 500) -> BenchmarkResult:
    """Measure critical path computation for 50 tasks."""
    from teb.dag import get_critical_path
    from teb.models import Task

    tasks: List[Task] = []
    for i in range(50):
        deps: List[int] = []
        if i > 0:
            deps.append(i)
        t = Task(
            goal_id=1, title=f"Task {i+1}", description="d",
            id=i + 1, order_index=i,
            depends_on=json.dumps(deps),
        )
        tasks.append(t)

    def op() -> None:
        get_critical_path(tasks)

    return run_benchmark("DAG critical path", op, n)


def bench_search(n: int = 300) -> BenchmarkResult:
    """Measure quick_search latency (LIKE fallback) over 200 rows."""
    from teb import storage
    from teb.models import Goal, Task
    from teb.search import quick_search

    with _TempDB():
        # Seed data: 20 goals × 10 tasks each = 200 searchable rows
        for gi in range(20):
            g = storage.create_goal(Goal(
                title=f"Goal about topic {gi} with keywords alpha beta gamma",
                description=f"Detailed description for benchmark goal number {gi}",
            ))
            for ti in range(10):
                storage.create_task(Task(
                    goal_id=g.id,
                    title=f"Task {ti} research delta epsilon for goal {gi}",
                    description=f"Step-by-step instructions for task {ti}",
                    order_index=ti,
                ))

        queries = ["alpha", "research", "goal 5", "epsilon", "nonexistent_xyz"]
        idx = 0

        def op() -> None:
            nonlocal idx
            quick_search(queries[idx % len(queries)])
            idx += 1

        return run_benchmark("Search (LIKE)", op, n)


def bench_event_bus_publish(n: int = 5000) -> BenchmarkResult:
    """Measure EventBus publish throughput (no subscribers)."""
    from teb.events import EventBus

    bus = EventBus()
    counter = 0

    def op() -> None:
        nonlocal counter
        counter += 1
        bus.publish(1, "task_completed", {
            "task_id": counter, "task_title": f"Task {counter}", "goal_id": 1,
        })

    return run_benchmark("EventBus publish (no sub)", op, n)


def bench_event_bus_with_subscribers(n: int = 2000) -> BenchmarkResult:
    """Measure EventBus publish throughput with 10 subscribers."""
    loop = asyncio.new_event_loop()

    from teb.events import EventBus

    bus = EventBus()
    # Simulate 10 concurrent subscribers — subscribe() needs an event loop
    # because it creates asyncio.Queue objects
    for _ in range(10):
        bus.subscribe(1)

    counter = 0

    def op() -> None:
        nonlocal counter
        counter += 1
        bus.publish(1, "task_completed", {
            "task_id": counter, "task_title": f"Task {counter}", "goal_id": 1,
        })

    result = run_benchmark("EventBus publish (10 subs)", op, n)
    loop.close()
    return result


def bench_event_bus_serialize(n: int = 5000) -> BenchmarkResult:
    """Measure SSEEvent serialization speed."""
    from teb.events import SSEEvent

    event = SSEEvent(
        event_type="task_completed",
        data={"task_id": 42, "task_title": "Research competitors", "goal_id": 1},
        id="12345",
    )

    def op() -> None:
        event.serialize()

    return run_benchmark("SSE serialize", op, n)


def bench_csv_import_small(n: int = 50) -> BenchmarkResult:
    """Measure CSV import speed — 10 tasks per import."""
    from teb.importers import import_from_csv

    header = "title,description,status,due_date\n"
    rows = "".join(
        f"Task {i},Description for task {i},todo,2025-12-{(i % 28) + 1:02d}\n"
        for i in range(10)
    )
    csv_text = header + rows

    with _TempDB(seed_user=True) as db:
        uid = db.user_id

        def op() -> None:
            import_from_csv(user_id=uid, csv_text=csv_text)

        return run_benchmark("CSV import (10 rows)", op, n)


def bench_csv_import_medium(n: int = 20) -> BenchmarkResult:
    """Measure CSV import speed — 100 tasks per import."""
    from teb.importers import import_from_csv

    header = "title,description,status,due_date\n"
    rows = "".join(
        f"Task {i},Description for task {i},todo,2025-12-{(i % 28) + 1:02d}\n"
        for i in range(100)
    )
    csv_text = header + rows

    with _TempDB(seed_user=True) as db:
        uid = db.user_id

        def op() -> None:
            import_from_csv(user_id=uid, csv_text=csv_text)

        return run_benchmark("CSV import (100 rows)", op, n)


def bench_csv_import_large(n: int = 5) -> BenchmarkResult:
    """Measure CSV import speed — 1000 tasks per import."""
    from teb.importers import import_from_csv

    header = "title,description,status,due_date\n"
    rows = "".join(
        f"Task {i},Description for task {i},todo,2025-12-{(i % 28) + 1:02d}\n"
        for i in range(1000)
    )
    csv_text = header + rows

    with _TempDB(seed_user=True) as db:
        uid = db.user_id

        def op() -> None:
            import_from_csv(user_id=uid, csv_text=csv_text)

        return run_benchmark("CSV import (1000 rows)", op, n)


# ─── Reporting ────────────────────────────────────────────────────────────────

_HEADER = (
    "Benchmark", "Iterations", "ops/sec", "avg (ms)", "p50 (ms)", "p95 (ms)", "p99 (ms)",
)


def _fmt_num(val: float, decimals: int = 2) -> str:
    """Format a number with thousands separators."""
    if val >= 1_000_000:
        return f"{val:,.0f}"
    if val >= 100:
        return f"{val:,.1f}"
    return f"{val:,.{decimals}f}"


def print_results(results: List[BenchmarkResult]) -> None:
    """Print a clean ASCII table of benchmark results."""

    # Build rows
    rows: List[Tuple[str, ...]] = []
    for r in results:
        rows.append((
            r.name,
            str(r.iterations),
            _fmt_num(r.ops_per_sec, 1),
            _fmt_num(r.avg_ms, 3),
            _fmt_num(r.p50_ms, 3),
            _fmt_num(r.p95_ms, 3),
            _fmt_num(r.p99_ms, 3),
        ))

    # Calculate column widths
    col_widths = [len(h) for h in _HEADER]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Alignment: name left, everything else right
    def _fmt_row(cells: Tuple[str, ...]) -> str:
        parts: List[str] = []
        for i, cell in enumerate(cells):
            w = col_widths[i]
            if i == 0:
                parts.append(cell.ljust(w))
            else:
                parts.append(cell.rjust(w))
        return "  │  ".join(parts)

    separator = "──┼──".join("─" * w for w in col_widths)

    print()
    print("=" * (sum(col_widths) + 5 * (len(col_widths) - 1)))
    print("  teb Performance Benchmarks")
    print("=" * (sum(col_widths) + 5 * (len(col_widths) - 1)))
    print()
    print(_fmt_row(_HEADER))
    print(separator)
    for row in rows:
        print(_fmt_row(row))
    print(separator)
    print()

    # Summary
    total_ops = sum(r.iterations for r in results)
    total_time = sum(r.total_seconds for r in results)
    print(f"  Total: {total_ops:,} operations in {total_time:.2f}s")
    print(f"  Python {sys.version.split()[0]} | SQLite WAL mode | Template-only (no AI)")
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    """Run all benchmarks and print results."""
    print("\n⏱  Running teb benchmarks …\n")

    benchmarks: List[Callable[[], BenchmarkResult]] = [
        # Decomposition
        bench_decompose_template,
        # Goal CRUD
        bench_goal_create,
        bench_goal_read,
        bench_goal_update,
        bench_goal_delete,
        # Task CRUD
        bench_task_create,
        bench_task_read,
        bench_task_update,
        bench_task_delete,
        # DAG planning
        bench_dag_validate,
        bench_dag_execution_plan,
        bench_dag_critical_path,
        # Search
        bench_search,
        # Events
        bench_event_bus_publish,
        bench_event_bus_with_subscribers,
        bench_event_bus_serialize,
        # Import adapters
        bench_csv_import_small,
        bench_csv_import_medium,
        bench_csv_import_large,
    ]

    results: List[BenchmarkResult] = []
    total = len(benchmarks)
    for i, bench_fn in enumerate(benchmarks, 1):
        label = bench_fn.__doc__.strip().split("\n")[0] if bench_fn.__doc__ else bench_fn.__name__
        print(f"  [{i:2d}/{total}] {label} …", end="", flush=True)
        result = bench_fn()
        print(f"  {_fmt_num(result.ops_per_sec, 1)} ops/sec")
        results.append(result)

    print_results(results)


if __name__ == "__main__":
    main()
