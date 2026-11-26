# 并行调度。每条分支单独包超时，失败按次数重试，最终失败就降级收口。
# 全局期限是最后一道保险，SDK 卡死也能保证汇聚节点被触发。

import asyncio;
import statistics;
import time;
from typing import List, Sequence;

from .core import (
    BranchAgent,
    BranchResult,
    FanoutReport,
    FanTask,
    V_默认分支超时秒,
    V_默认全局超时秒,
    V_默认重试次数,
);


async def run_single(agent: BranchAgent, task: FanTask, timeout: float, retries: int) -> BranchResult:
    # 单条分支。一次尝试算一次，超时和异常都走重试，次数用完返回降级结果。
    started_at = time.perf_counter();
    attempts = 0;
    last_error = "";
    for attempt in range(retries + 1):
        attempts = attempt + 1;
        try:
            output = await asyncio.wait_for(agent.run(task), timeout=timeout);
            return BranchResult(
                branch_id=agent.branch_id,
                name=agent.name,
                role=agent.role,
                ok=True,
                output=output.text,
                data=output.data,
                started_at=started_at,
                finished_at=time.perf_counter(),
                attempts=attempts,
            );
        except asyncio.TimeoutError:
            last_error = f"分支超时（单次上限 {timeout:g} 秒）";
        except asyncio.CancelledError:
            raise;
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}";
    return BranchResult(
        branch_id=agent.branch_id,
        name=agent.name,
        role=agent.role,
        ok=False,
        error=last_error,
        started_at=started_at,
        finished_at=time.perf_counter(),
        attempts=attempts,
        timed_out="超时" in last_error,
        degraded=True,
    );


async def run_parallel(
    agents: Sequence[BranchAgent],
    task: FanTask,
    branch_timeout: float = V_默认分支超时秒,
    retries: int = V_默认重试次数,
    global_timeout: float = V_默认全局超时秒,
) -> FanoutReport:
    # 全部分支一起启动，等全局期限。没结束的取消掉，记成降级。
    started_at = time.perf_counter();
    futures = {
        asyncio.create_task(run_single(agent, task, branch_timeout, retries), name=f"branch-{agent.branch_id}"): agent
        for agent in agents
    };
    done, pending = await asyncio.wait(list(futures.keys()), timeout=global_timeout);
    if pending:
        # 取消后要把它们等回来，不然事件循环里会挂着没退出的任务。
        for future in pending:
            future.cancel();
        await asyncio.gather(*pending, return_exceptions=True);
    results: List[BranchResult] = [];
    for future, agent in futures.items():
        if future in done and not future.cancelled():
            results.append(future.result());
            continue;
        results.append(
            BranchResult(
                branch_id=agent.branch_id,
                name=agent.name,
                role=agent.role,
                ok=False,
                error="全局超时，分支被取消",
                started_at=started_at,
                finished_at=time.perf_counter(),
                timed_out=True,
                degraded=True,
            )
        );
    wall_seconds = time.perf_counter() - started_at;
    durations = [item.duration for item in results if item.ok];
    median_seconds = statistics.median(durations) if durations else 0.0;
    slowest = max(results, key=lambda item: item.duration) if results else None;
    # 中位数两倍以上算长尾，分支少于三条不判。
    long_tail = bool(
        len(results) >= 3
        and median_seconds > 0
        and slowest is not None
        and slowest.duration > 2 * median_seconds
    );
    return FanoutReport(
        results=results,
        wall_seconds=wall_seconds,
        median_seconds=median_seconds,
        slowest_name=slowest.name if slowest else "",
        long_tail=long_tail,
    );
