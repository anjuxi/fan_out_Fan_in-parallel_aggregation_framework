# 扇入扇出用到的基础对象都放这里：任务、分支结果、执行报告，还有两个抽象接口。
# 其他模块只认这些类型，换掉具体 Agent 不用改编排。

from __future__ import annotations;

from abc import ABC, abstractmethod;
from dataclasses import dataclass, field;
from typing import Any, Dict, List;


V_默认分支超时秒 = 30.0;
V_默认全局超时秒 = 90.0;
V_默认重试次数 = 1;


@dataclass
class FanTask:
    # 一次扇出的输入。question 是任务说明，content 是被处理的正文，context 放选项这类附加信息。
    question: str;
    content: str = "";
    context: Dict[str, Any] = field(default_factory=dict);


@dataclass
class BranchOutput:
    # 分支的返回。text 给人看，data 给汇聚节点按字段取。
    text: str;
    data: Dict[str, Any] = field(default_factory=dict);


@dataclass
class BranchResult:
    # 单条分支的执行记录。成功、失败、超时、降级分开记，排查时不用猜。
    branch_id: str;
    name: str;
    role: str;
    ok: bool;
    output: str = "";
    data: Dict[str, Any] = field(default_factory=dict);
    error: str = "";
    started_at: float = 0.0;
    finished_at: float = 0.0;
    attempts: int = 0;
    timed_out: bool = False;
    degraded: bool = False;

    @property
    def duration(self) -> float:
        # 开工到收尾的耗时，秒。
        return max(0.0, self.finished_at - self.started_at);


class BranchAgent(ABC):
    # 分支的统一接口。子类只实现 run，编排层不管它内部是调模型还是查库。

    def __init__(self, branch_id: str, name: str, role: str) -> None:
        # branch_id 用来认结果，role 用来查职责有没有撞。
        self.branch_id = branch_id;
        self.name = name;
        self.role = role;

    @abstractmethod
    async def run(self, task: FanTask) -> BranchOutput:
        # 跑自己的活，看不到其他分支的中间结果。
        raise NotImplementedError;


class Aggregator(ABC):
    # 汇聚节点接口。传进来的是全部分支的结果，失败的也在里面。

    def __init__(self, name: str) -> None:
        # 名字会打进日志，方便确认当前用的哪种策略。
        self.name = name;

    @abstractmethod
    async def aggregate(self, task: FanTask, results: List[BranchResult]) -> "AggregatedResult":
        # 汇总全部分支。个别失败也要出报告，不能整段丢掉。
        raise NotImplementedError;


@dataclass
class AggregatedResult:
    # 汇聚产出。冲突单独放在 conflicts，上层好判断要不要人工介入。
    summary: str;
    data: Dict[str, Any] = field(default_factory=dict);
    conflicts: List[Dict[str, Any]] = field(default_factory=list);
    valid_branches: int = 0;
    failed_branches: int = 0;


@dataclass
class FanoutReport:
    # 并行过程的观测数据，主要看墙钟、中位数和最慢分支。
    results: List[BranchResult];
    wall_seconds: float;
    median_seconds: float;
    slowest_name: str = "";
    long_tail: bool = False;

    @property
    def ok_count(self) -> int:
        # 成功分支数。
        return sum(1 for item in self.results if item.ok);

    @property
    def failed_count(self) -> int:
        # 失败、超时、被取消的分支数。
        return sum(1 for item in self.results if not item.ok);


@dataclass
class FanoutOutcome:
    # 一次完整执行的返回值，观测数据和最终报告都在里面。
    task: FanTask;
    report: FanoutReport;
    aggregated: AggregatedResult;
