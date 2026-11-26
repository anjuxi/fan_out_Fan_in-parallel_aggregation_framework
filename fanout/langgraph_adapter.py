# LangGraph 版的同一套流程，用来和原生实现对照。
# 超时重试直接复用 executor，免得两边行为对不上。

import operator;
import time;
from typing import Annotated, Any, Dict, List, Sequence, TypedDict;

from langgraph.graph import END, START, StateGraph;

from .core import (
    Aggregator,
    BranchAgent,
    BranchResult,
    FanTask,
    V_默认分支超时秒,
    V_默认重试次数,
);
from .executor import run_single;


class FanGraphState(TypedDict, total=False):
    # 图状态。branch_results 用 operator.add 合并，每个分支只管追加自己那条。
    question: str;
    content: str;
    context: Dict[str, Any];
    branch_results: Annotated[List[BranchResult], operator.add];
    summary: str;


def build_fanout_graph(
    branches: Sequence[BranchAgent],
    aggregator: Aggregator,
    branch_timeout: float = V_默认分支超时秒,
    retries: int = V_默认重试次数,
):
    # 每个分支一个节点，都从 START 出发，跑完汇到 gather。
    graph = StateGraph(FanGraphState);
    for agent in branches:
        async def branch_node(state: FanGraphState, agent: BranchAgent = agent) -> Dict[str, Any]:
            # 只跑自己的 Agent，超时重试和原生版本共用一套。
            task = FanTask(
                question=state.get("question", ""),
                content=state.get("content", ""),
                context=state.get("context", {}),
            );
            result = await run_single(agent, task, branch_timeout, retries);
            return {"branch_results": [result]};
        graph.add_node(agent.branch_id, branch_node);
        graph.add_edge(START, agent.branch_id);

    async def gather_node(state: FanGraphState) -> Dict[str, Any]:
        # 上游全结束后触发，汇聚策略也用同一个。
        task = FanTask(
            question=state.get("question", ""),
            content=state.get("content", ""),
            context=state.get("context", {}),
        );
        aggregated = await aggregator.aggregate(task, list(state.get("branch_results", [])));
        return {"summary": aggregated.summary};

    graph.add_node("gather", gather_node);
    graph.add_edge([agent.branch_id for agent in branches], "gather");
    graph.add_edge("gather", END);
    return graph.compile();


async def run_fanout_graph(
    branches: Sequence[BranchAgent],
    aggregator: Aggregator,
    task: FanTask,
    branch_timeout: float = V_默认分支超时秒,
    retries: int = V_默认重试次数,
) -> Dict[str, Any]:
    # 跑一次图，返回最终状态和耗时。
    graph = build_fanout_graph(branches, aggregator, branch_timeout, retries);
    started_at = time.perf_counter();
    state = await graph.ainvoke(
        {"question": task.question, "content": task.content, "context": task.context}
    );
    return {"state": state, "wall_seconds": time.perf_counter() - started_at};
