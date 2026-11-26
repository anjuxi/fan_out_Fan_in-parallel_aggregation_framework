import argparse;
import asyncio;

from fanout.aggregators import ConflictMarkAggregator, ScoreAggregator, VoteAggregator;
from fanout.agents import ReviewAgent, SolutionAgent, VotingAgent;
from fanout.core import FanTask;
from fanout.llm import make_llm;
from fanout.pipeline import FanoutPipeline;

V_评审文档 = """数据中台建设方案 v1.2

一、建设目标
统一各业务线的数据口径，建设一套数据中台，支撑实时报表与用户画像。
计划分两期上线，一期覆盖核心交易与会员数据，二期接入营销与客服数据。

二、数据流转
业务库数据经同步链路进入数据中台，清洗后写入分析库。
为降低访问延迟，部分热点数据会缓存到境外节点，缓存保留 30 天。
用户标识在同步过程中使用内部 ID 映射，但境外缓存保留明文手机号，便于客服查询。

三、权限与审计
内部员工按角色访问，管理员可以导出明细数据。
审计日志统一留存 180 天，操作记录包含账号、时间、导出范围。
第三方数据服务商通过接口读取脱敏后的标签数据，供应商清单和协议编号尚未归档。

四、接口与容量
对外接口统一走网关，网关层配置了基础限流。
大促期间峰值预计是日常的 6 倍，扩容方案依赖云厂商弹性伸缩，内部服务之间没有单独的限流和熔断。
监控覆盖接口成功率与延迟，暂未覆盖数据一致性和缓存命中率。
""";
V_方案背景 = """公司希望在半年的预算周期内完成一次核心系统升级。
现状是三套系统各自维护账号和入口，用户完成任务平均需要跳转 5 次。
数据同步延迟在高峰期达到 20 分钟，报表查询偶发超时，客服反馈集中在查不到最新订单。
约束：预算只能覆盖一条主链路的深度改造，其余链路做局部优化；合规要求数据出境前完成评估。
""";
V_投票题目 = "在预算有限的前提下，优先升级哪条链路";
V_投票选项 = [
    "数据同步链路",
    "报表查询链路",
    "通知推送链路",
];


def build_parser() -> argparse.ArgumentParser:
    # 参数不多，够用就行；默认离线，--real 才去读密钥。
    parser = argparse.ArgumentParser(description="Fan-out / Fan-in 并行汇聚架构演示");
    parser.add_argument(
        "scenario",
        nargs="?",
        default="all",
        choices=["review", "solutions", "vote", "graph", "all"],
        help="要运行的场景，默认全部运行",
    );
    parser.add_argument("--real", action="store_true", help="使用真实模型接口，读取 FANOUT_API_KEY 等环境变量");
    parser.add_argument("--model", default=None, help="模型名称，默认取 FANOUT_MODEL 或 gpt-4o-mini");
    parser.add_argument("--timeout", type=float, default=30.0, help="单条分支超时秒数");
    parser.add_argument("--retries", type=int, default=1, help="分支失败后的重试次数");
    return parser;


def build_review_branches(args: argparse.Namespace) -> tuple:
    # 三个评审视角各建一个 Agent，共用一个模型客户端。
    llm = make_llm(args.model, offline=not args.real);
    branches = [
        ReviewAgent("review-compliance", "合规评审", "合规", llm),
        ReviewAgent("review-tech", "技术评审", "技术", llm),
        ReviewAgent("review-security", "安全评审", "安全", llm),
    ];
    return branches, llm;


async def demo_review(args: argparse.Namespace) -> None:
    # 三个视角并行评审，汇聚节点只标共识和冲突。
    branches, _ = build_review_branches(args);
    pipeline = FanoutPipeline(
        "多专家并行评审",
        branches,
        ConflictMarkAggregator(),
        args.timeout,
        args.retries,
    );
    outcome = await pipeline.run(FanTask(question="评审《数据中台建设方案 v1.2》", content=V_评审文档));
    print(outcome.aggregated.summary);
    print("✅ 评审报告生成完毕！");


async def demo_solutions(args: argparse.Namespace) -> None:
    # 三个取向各写一套方案，汇聚节点排名。
    llm = make_llm(args.model, offline=not args.real);
    branches = [
        SolutionAgent("solution-cost", "成本优先方案", "成本优先", llm),
        SolutionAgent("solution-ux", "体验优先方案", "体验优先", llm),
        SolutionAgent("solution-risk", "风险优先方案", "风险优先", llm),
    ];
    pipeline = FanoutPipeline(
        "多方案并行生成",
        branches,
        ScoreAggregator(),
        args.timeout,
        args.retries,
    );
    outcome = await pipeline.run(FanTask(question="制定核心系统半年升级方案", content=V_方案背景));
    print(outcome.aggregated.summary);
    print("✅ 方案对比完成，推荐方案已输出！");


async def demo_vote(args: argparse.Namespace) -> None:
    # 三个投票员独立作答，汇聚节点按票数统计。
    llm = make_llm(args.model, offline=not args.real);
    branches = [
        VotingAgent("vote-a", "模型甲", llm),
        VotingAgent("vote-b", "模型乙", llm),
        VotingAgent("vote-c", "模型丙", llm),
    ];
    pipeline = FanoutPipeline(
        "多模型并行投票",
        branches,
        VoteAggregator(),
        args.timeout,
        args.retries,
    );
    outcome = await pipeline.run(FanTask(question=V_投票题目, context={"options": V_投票选项}));
    print(outcome.aggregated.summary);
    print("✅ 投票统计完成！");


async def demo_graph(args: argparse.Namespace) -> None:
    # 同一组评审分支改用 LangGraph 编排，对照耗时和结果。
    try:
        from fanout.langgraph_adapter import run_fanout_graph;
    except ImportError:
        print("未安装 langgraph，跳过图编排演示！");
        return;
    branches, _ = build_review_branches(args);
    task = FanTask(question="评审《数据中台建设方案 v1.2》", content=V_评审文档);
    output = await run_fanout_graph(branches, ConflictMarkAggregator(), task, args.timeout, args.retries);
    print("——" * 50);
    print("【LangGraph 并行边编排】分支结果！");
    for item in output["state"].get("branch_results", []):
        state = "成功" if item.ok else ("超时降级" if item.timed_out else "失败降级");
        print(f"  图节点 {item.name}｜{state}｜尝试 {item.attempts} 次｜耗时 {item.duration:.3f}s！");
    print(f"图编排总耗时 {output['wall_seconds']:.3f}s！");
    print(output["state"].get("summary", ""));
    print("✅ LangGraph 版本演示完成！");


async def main() -> None:
    # 默认依次跑四个演示，也可以只跑一个。
    args = build_parser().parse_args();
    scenarios = ["review", "solutions", "vote", "graph"] if args.scenario == "all" else [args.scenario];
    for scenario in scenarios:
        if scenario == "review":
            await demo_review(args);
        elif scenario == "solutions":
            await demo_solutions(args);
        elif scenario == "vote":
            await demo_vote(args);
        elif scenario == "graph":
            await demo_graph(args);
    print("✅ 演示结束，全部场景执行完成！");


if __name__ == "__main__":
    asyncio.run(main());
