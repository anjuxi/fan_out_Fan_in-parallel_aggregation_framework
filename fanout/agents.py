# 三种分支 Agent：评审、方案、投票。每个只管一个视角，互相不读对方的结果。

import asyncio;
from typing import Dict, List;

from .core import BranchAgent, BranchOutput, FanTask;
from .llm import BaseLLM;


class ReviewAgent(BranchAgent):
    # 一个视角的评审。只出本视角的意见，别的分支说什么它不知道。
    def __init__(self, branch_id: str, name: str, focus: str, llm: BaseLLM) -> None:
        super().__init__(branch_id, name, f"{focus}评审");
        self.focus = focus;
        self.llm = llm;

    async def run(self, task: FanTask) -> BranchOutput:
        # 提示词里把视角钉死，要求结构化发现项，方便后面按议题归并。
        data = await asyncio.to_thread(
            self.llm.chat_json,
            f"你是{self.focus}领域的评审专家，只从{self.focus}视角给出结论。",
            f"评审任务：{task.question}\n\n待评审文档：\n{task.content}\n\n"
            "只输出 JSON，字段为 overall 和 findings。"
            "findings 是数组，每项包含 topic、stance、severity、detail，"
            "stance 只能是 通过、整改、拒绝 三种之一。",
            {"role": "review", "label": self.focus},
        );
        findings: List[Dict[str, str]] = [];
        for item in data.get("findings", []) if isinstance(data.get("findings"), list) else []:
            if not isinstance(item, dict):
                continue;
            findings.append(
                {
                    "topic": str(item.get("topic", "未命名议题")),
                    "stance": str(item.get("stance", "通过")),
                    "severity": str(item.get("severity", "低")),
                    "detail": str(item.get("detail", "")),
                }
            );
        if not findings:
            findings = [
                {
                    "topic": "整体结论",
                    "stance": "通过",
                    "severity": "低",
                    "detail": data.get("overall", "分支未返回结构化发现项。"),
                }
            ];
        overall = str(data.get("overall", ""));
        return BranchOutput(text=overall or "评审完成", data={"overall": overall, "findings": findings});


class SolutionAgent(BranchAgent):
    # 一个取向的方案。三个分支各写各的，自评分也各打各的。
    def __init__(self, branch_id: str, name: str, angle: str, llm: BaseLLM) -> None:
        super().__init__(branch_id, name, f"{angle}方案");
        self.angle = angle;
        self.llm = llm;

    async def run(self, task: FanTask) -> BranchOutput:
        # 自评分只是排序参考，不代表客观质量，最终排名由汇聚节点定。
        data = await asyncio.to_thread(
            self.llm.chat_json,
            f"你是方案设计专家，本次必须围绕「{self.angle}」展开，不要面面俱到。",
            f"任务：{task.question}\n\n约束与背景：\n{task.content}\n\n"
            "只输出 JSON，字段为 title、summary、score、steps、risks。"
            "score 是 0 到 100 的自评分，steps 和 risks 都是字符串数组。",
            {"role": "solution", "label": self.angle},
        );
        title = str(data.get("title", f"{self.angle}方案"));
        steps = [str(item) for item in data.get("steps", [])] if isinstance(data.get("steps"), list) else [];
        risks = [str(item) for item in data.get("risks", [])] if isinstance(data.get("risks"), list) else [];
        try:
            score = float(data.get("score", 0));
        except (TypeError, ValueError):
            score = 0.0;
        return BranchOutput(
            text=str(data.get("summary", title)),
            data={
                "title": title,
                "summary": str(data.get("summary", "")),
                "score": score,
                "steps": steps,
                "risks": risks,
            },
        );


class VotingAgent(BranchAgent):
    # 一个投票模型。只回答题目，不跟别人商量。
    def __init__(self, branch_id: str, name: str, llm: BaseLLM) -> None:
        super().__init__(branch_id, name, f"{name}独立投票");
        self.llm = llm;

    async def run(self, task: FanTask) -> BranchOutput:
        # 选项统一编成 A/B/C，方便后面统计票数。
        options = task.context.get("options", []);
        option_text = "\n".join(f"{chr(ord('A') + index)}. {item}" for index, item in enumerate(options));
        data = await asyncio.to_thread(
            self.llm.chat_json,
            "你是独立投票员，只根据题目本身做判断，不假设其他投票者的立场。",
            f"题目：{task.question}\n\n选项：\n{option_text}\n\n"
            "只输出 JSON，字段为 answer 和 reason。answer 必须是选项字母或选项原文之一。",
            {"role": "vote", "label": self.name},
        );
        answer = str(data.get("answer", "")).strip();
        reason = str(data.get("reason", "")).strip();
        return BranchOutput(text=reason or answer, data={"answer": answer, "reason": reason});
