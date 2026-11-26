# 只测编排行为，不打网络。耗时和失败都用假分支模拟。

import asyncio;
import time;

import pytest;

from fanout.aggregators import ConflictMarkAggregator, VoteAggregator;
from fanout.core import BranchAgent, BranchOutput, BranchResult, FanTask;
from fanout.executor import run_parallel;
from fanout.pipeline import FanoutPipeline;


class SleepAgent(BranchAgent):
    # 按指定秒数休眠；前几次调用可以指定直接抛错。
    def __init__(self, branch_id: str, name: str, role: str, seconds: float = 0.05, fail_times: int = 0) -> None:
        super().__init__(branch_id, name, role);
        self.seconds = seconds;
        self.fail_times = fail_times;
        self.calls = 0;

    async def run(self, task: FanTask) -> BranchOutput:
        # 记调用次数，后面断言重试有没有发生。
        self.calls += 1;
        await asyncio.sleep(self.seconds);
        if self.calls <= self.fail_times:
            raise RuntimeError("模拟失败");
        return BranchOutput(text=f"{self.name}结果", data={"name": self.name});


def build_result(name: str, findings=None, answer=None, reason="") -> BranchResult:
    # 手工拼一条成功结果，省得每次都跑 Agent。
    data = {"findings": findings or []};
    if answer is not None:
        data["answer"] = answer;
        data["reason"] = reason;
    return BranchResult(
        branch_id=name,
        name=name,
        role=name,
        ok=True,
        output="",
        data=data,
        started_at=0.0,
        finished_at=1.0,
        attempts=1,
    );


def test_parallel_branches_share_one_wall_clock():
    # 三条各睡 0.3 秒，并行总耗时应该明显小于串行的 0.9 秒。
    agents = [SleepAgent(f"b{index}", f"分支{index}", f"角色{index}", 0.3) for index in range(3)];
    report = asyncio.run(run_parallel(agents, FanTask("测试"), branch_timeout=2, retries=0, global_timeout=5));
    assert report.ok_count == 3;
    assert report.wall_seconds < 0.8;


def test_timeout_branch_degrades_without_blocking():
    # 慢分支超时降级，快分支照常返回，整体不被拖住。
    agents = [
        SleepAgent("fast", "快分支", "快", 0.05),
        SleepAgent("slow", "慢分支", "慢", 3.0),
    ];
    started_at = time.perf_counter();
    report = asyncio.run(run_parallel(agents, FanTask("测试"), branch_timeout=0.2, retries=0, global_timeout=3));
    elapsed = time.perf_counter() - started_at;
    slow = [item for item in report.results if item.branch_id == "slow"][0];
    assert slow.timed_out is True;
    assert slow.degraded is True;
    assert slow.ok is False;
    assert report.failed_count == 1;
    assert elapsed < 1.0;


def test_retry_recovers_transient_failure():
    # 第一次失败第二次成功，结果应该是成功，尝试次数 2。
    agent = SleepAgent("flaky", "抖动分支", "抖动", 0.01, fail_times=1);
    report = asyncio.run(run_parallel([agent], FanTask("测试"), branch_timeout=1, retries=1, global_timeout=3));
    assert report.results[0].ok is True;
    assert report.results[0].attempts == 2;


def test_conflict_is_marked_not_flattened():
    # 同一议题立场不同，必须进冲突列表，不能合并掉。
    results = [
        build_result("合规", [{"topic": "数据出境", "stance": "整改", "severity": "高", "detail": "需要评估"}]),
        build_result("技术", [{"topic": "数据出境", "stance": "通过", "severity": "低", "detail": "区域化部署"}]),
    ];
    aggregated = asyncio.run(ConflictMarkAggregator().aggregate(FanTask("评审"), results));
    assert len(aggregated.conflicts) == 1;
    assert "冲突" in aggregated.summary;
    assert "数据出境" in aggregated.summary;


def test_vote_majority_and_agreement_rate():
    # 两票对一票，结论落在多数票，一致率 66.7%。
    results = [
        build_result("甲", answer="A", reason="上游瓶颈"),
        build_result("乙", answer="A", reason="延迟会放大"),
        build_result("丙", answer="B", reason="用户反馈多"),
    ];
    aggregated = asyncio.run(VoteAggregator().aggregate(FanTask("选择"), results));
    assert aggregated.data["winner"] == "A";
    assert aggregated.data["majority"] is True;
    assert aggregated.data["agreement_rate"] == pytest.approx(2 / 3, rel=1e-3);
    assert aggregated.conflicts == [];
    assert "丙" in aggregated.summary;


def test_vote_tie_reports_conflict():
    # 平票不算多数，必须留冲突记录。
    results = [build_result("甲", answer="A"), build_result("乙", answer="B")];
    aggregated = asyncio.run(VoteAggregator().aggregate(FanTask("选择"), results));
    assert aggregated.data["majority"] is False;
    assert aggregated.data["winner"] == "";
    assert len(aggregated.conflicts) == 1;


def test_pipeline_rejects_duplicate_roles():
    # 职责重复在构造阶段就拦下。
    agents = [SleepAgent("a1", "甲", "同一职责"), SleepAgent("a2", "乙", "同一职责")];
    with pytest.raises(ValueError):
        FanoutPipeline("测试", agents, ConflictMarkAggregator());
