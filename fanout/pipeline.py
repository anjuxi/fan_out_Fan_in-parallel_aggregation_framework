# 把分发、并行、汇聚串成一条流水线，顺带打印耗时和异常。
# 拓扑在构造时就定死，运行时不改，和扇入扇出的定位一致。

from typing import List, Sequence;

from .core import (
    Aggregator,
    BranchAgent,
    FanTask,
    FanoutOutcome,
    V_默认分支超时秒,
    V_默认全局超时秒,
    V_默认重试次数,
);
from .executor import run_parallel;


class FanoutPipeline:
    # 一次扇出：一个分发口，一组分支，一个汇聚口。
    def __init__(
        self,
        stage_name: str,
        branches: Sequence[BranchAgent],
        aggregator: Aggregator,
        branch_timeout: float = V_默认分支超时秒,
        retries: int = V_默认重试次数,
        global_timeout: float = V_默认全局超时秒,
    ) -> None:
        self.stage_name = stage_name;
        self.branches: List[BranchAgent] = list(branches);
        self.aggregator = aggregator;
        self.branch_timeout = branch_timeout;
        self.retries = retries;
        self.global_timeout = global_timeout;
        self._validate();

    def _validate(self) -> None:
        # 编号和职责各查一遍。职责撞了只会白烧调用次数。
        if not self.branches:
            raise ValueError("至少需要一条分支才能扇出。");
        ids = [agent.branch_id for agent in self.branches];
        roles = [agent.role for agent in self.branches];
        if len(ids) != len(set(ids)):
            raise ValueError("分支编号重复，无法确定汇聚时的结果归属。");
        if len(roles) != len(set(roles)):
            raise ValueError("分支职责重复，请让每个分支负责一个正交视角。");

    async def run(self, task: FanTask) -> FanoutOutcome:
        # 任务同时甩给所有分支，全部结束后进汇聚。
        print("——" * 50);
        print(f"【{self.stage_name}】分发节点接收任务：{task.question}！");
        print(f"扇出：{len(self.branches)} 条分支同时开工，彼此不通信！");
        report = await run_parallel(
            self.branches,
            task,
            self.branch_timeout,
            self.retries,
            self.global_timeout,
        );
        for item in report.results:
            state = "成功" if item.ok else ("超时降级" if item.timed_out else "失败降级");
            print(
                f"  分支 {item.name}｜职责 {item.role}｜{state}｜尝试 {item.attempts} 次｜耗时 {item.duration:.3f}s！"
            );
        print(
            f"扇入：{report.ok_count} 条成功，{report.failed_count} 条异常，"
            f"实际总耗时 {report.wall_seconds:.3f}s，汇聚策略「{self.aggregator.name}」！"
        );
        if report.long_tail:
            print(f"长尾：最慢分支「{report.slowest_name}」拖住了整体耗时！");
        aggregated = await self.aggregator.aggregate(task, report.results);
        return FanoutOutcome(task=task, report=report, aggregated=aggregated);
