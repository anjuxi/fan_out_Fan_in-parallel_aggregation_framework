# 汇聚策略。同一批结果换种策略，出来的东西完全不一样，按业务选。
# 冲突类的结果只做标记，不替人下结论。

from collections import Counter;
from typing import Any, Dict, List;

from .core import AggregatedResult, Aggregator, BranchResult, FanTask;

V_风险等级权重 = {"高": 3, "中": 2, "低": 1};


class MergeAggregator(Aggregator):
    # 合并去重。最简单的策略，先把链路跑通用。
    def __init__(self) -> None:
        super().__init__("合并去重");

    async def aggregate(self, task: FanTask, results: List[BranchResult]) -> AggregatedResult:
        # 每行前面挂上分支名，整行重复的丢掉。
        lines: List[str] = [];
        seen = set();
        for item in results:
            if not item.ok:
                continue;
            for line in (item.output or "").splitlines():
                stripped = line.strip();
                if not stripped or stripped in seen:
                    continue;
                seen.add(stripped);
                lines.append(f"{item.name}：{stripped}");
        failed = [item for item in results if not item.ok];
        summary = "\n".join(lines) if lines else "全部分支都没有产出可用结果，请检查模型接口。";
        if failed:
            summary += "\n\n### 失败分支\n" + "\n".join(f"- {item.name}：{item.error}" for item in failed);
        return AggregatedResult(
            summary=f"## 汇总结果：{task.question}\n\n{summary}",
            valid_branches=len(results) - len(failed),
            failed_branches=len(failed),
        );


class ConflictMarkAggregator(Aggregator):
    # 评审用。按议题把各分支的立场摆在一起，不一样的挑出来。
    def __init__(self) -> None:
        super().__init__("冲突标记");

    async def aggregate(self, task: FanTask, results: List[BranchResult]) -> AggregatedResult:
        # 先归并，再分共识和冲突；冲突只列依据，不合成单一结论。
        valid = [item for item in results if item.ok];
        failed = [item for item in results if not item.ok];
        topics: Dict[str, List[Dict[str, str]]] = {};
        for item in valid:
            for finding in item.data.get("findings", []):
                topic = str(finding.get("topic", "未命名议题"));
                topics.setdefault(topic, []).append(
                    {
                        "branch": item.name,
                        "stance": str(finding.get("stance", "通过")),
                        "severity": str(finding.get("severity", "低")),
                        "detail": str(finding.get("detail", "")),
                    }
                );
        conflicts: List[Dict[str, Any]] = [];
        consensus: List[Dict[str, Any]] = [];
        for topic, entries in topics.items():
            if len({entry["stance"] for entry in entries}) > 1:
                conflicts.append({"topic": topic, "entries": entries});
            else:
                consensus.append({"topic": topic, "stance": entries[0]["stance"], "entries": entries});
        lines = [f"## 评审汇总：{task.question}", ""];
        lines.append(f"有效分支 {len(valid)} 条，失败 {len(failed)} 条，共识别议题 {len(topics)} 个。");
        lines.append("");
        lines.append("### 冲突议题（需人工裁决）");
        if conflicts:
            for conflict in conflicts:
                stances = "；".join(
                    f"{entry['branch']}＝{entry['stance']}（{entry['severity']}）" for entry in conflict["entries"]
                );
                details = " ".join(f"{entry['branch']}：{entry['detail']}" for entry in conflict["entries"]);
                lines.append(f"- **{conflict['topic']}**：{stances}");
                lines.append(f"  依据：{details}");
            lines.append("");
            lines.append("上面几项先别急着合并，按依据逐条确认。");
        else:
            lines.append("- 没有发现立场冲突。");
        lines.append("");
        lines.append("### 共识议题");
        if consensus:
            for item in consensus:
                severities = [entry["severity"] for entry in item["entries"]];
                top_severity = max(severities, key=lambda level: V_风险等级权重.get(level, 0));
                lines.append(f"- **{item['topic']}**：{item['stance']}（最高风险 {top_severity}）");
        else:
            lines.append("- 暂无可归并的共识项。");
        lines.append("");
        lines.append("### 分支整体结论");
        for item in valid:
            overall = str(item.data.get("overall", "")).strip() or item.output or "未填写整体结论。";
            lines.append(f"- {item.name}：{overall}");
        if failed:
            lines.append("");
            lines.append("### 失败分支");
            for item in failed:
                lines.append(f"- {item.name}：{item.error}");
            lines.append("失败分支没参与结论，上游得补上。");
        return AggregatedResult(
            summary="\n".join(lines),
            data={
                "conflict_count": len(conflicts),
                "consensus_count": len(consensus),
                "topics": sorted(topics),
            },
            conflicts=conflicts,
            valid_branches=len(valid),
            failed_branches=len(failed),
        );


class ScoreAggregator(Aggregator):
    # 方案用。按自评分排名，分差小的时候提示融合。
    def __init__(self) -> None:
        super().__init__("评分择优");

    async def aggregate(self, task: FanTask, results: List[BranchResult]) -> AggregatedResult:
        # 只收有 title 的分支。前两名差 5 分以内不直接淘汰，建议合成。
        valid = [item for item in results if item.ok and item.data.get("title")];
        failed = [item for item in results if not item.ok];
        ranked = sorted(valid, key=lambda item: float(item.data.get("score", 0)), reverse=True);
        if not ranked:
            return AggregatedResult(
                summary="所有方案分支都没有产出可用内容，请检查模型接口。",
                valid_branches=0,
                failed_branches=len(failed),
            );
        best = ranked[0];
        best_score = float(best.data.get("score", 0));
        lines = [f"## 方案汇总：{task.question}", ""];
        lines.append(f"### 推荐方案：{best.data.get('title')}（自评 {best_score:.0f} 分，来自 {best.name}）");
        lines.append(str(best.data.get("summary", "")));
        steps = best.data.get("steps", []);
        if steps:
            lines.append("");
            lines.append("实施步骤：");
            lines.extend(f"- {step}" for step in steps);
        risks = best.data.get("risks", []);
        if risks:
            lines.append("");
            lines.append("主要风险：");
            lines.extend(f"- {risk}" for risk in risks);
        lines.append("");
        lines.append("### 方案排名");
        for index, item in enumerate(ranked, start=1):
            lines.append(f"{index}. {item.data.get('title')}，{float(item.data.get('score', 0)):.0f} 分（{item.name}）");
        if len(ranked) >= 2:
            gap = best_score - float(ranked[1].data.get("score", 0));
            if gap <= 5:
                lines.append("");
                lines.append(
                    f"前两名只差 {gap:.0f} 分，建议把「{ranked[1].data.get('title')}」的亮点并进推荐方案。"
                );
        if failed:
            lines.append("");
            lines.append("### 失败分支");
            lines.extend(f"- {item.name}：{item.error}" for item in failed);
        return AggregatedResult(
            summary="\n".join(lines),
            data={
                "ranking": [
                    {"title": item.data.get("title"), "score": float(item.data.get("score", 0)), "branch": item.name}
                    for item in ranked
                ]
            },
            valid_branches=len(valid),
            failed_branches=len(failed),
        );


class VoteAggregator(Aggregator):
    # 投票用。过半才算结论，平票和分散都记成冲突。
    def __init__(self) -> None:
        super().__init__("多数投票");

    async def aggregate(self, task: FanTask, results: List[BranchResult]) -> AggregatedResult:
        # 严格过半才宣布结果；票型分散时把反方理由列全，交给人看。
        valid = [item for item in results if item.ok and str(item.data.get("answer", "")).strip()];
        failed = [item for item in results if not item.ok];
        if not valid:
            return AggregatedResult(
                summary="没有有效投票，无法形成结论。",
                valid_branches=0,
                failed_branches=len(failed),
            );
        counter = Counter(str(item.data.get("answer", "")).strip() for item in valid);
        ranking = counter.most_common();
        winner, winner_count = ranking[0];
        total = len(valid);
        majority = winner_count > total / 2;
        agreement_rate = winner_count / total;
        conflicts: List[Dict[str, Any]] = [];
        if not majority:
            conflicts.append(
                {"topic": "投票结论", "detail": f"最高票 {winner} 只有 {winner_count}/{total} 票，未过半数。"}
            );
        lines = [f"## 投票汇总：{task.question}", ""];
        if majority:
            lines.append(f"结论：{winner}（{winner_count}/{total} 票，一致率 {agreement_rate:.1%}）。");
        else:
            lines.append(f"没过半数。最高票 {winner} 只有 {winner_count}/{total} 票，不构成结论，需要补充投票或人工判断。");
        lines.append("");
        lines.append("### 票型分布");
        for answer, count in ranking:
            voters = [item.name for item in valid if str(item.data.get("answer", "")).strip() == answer];
            lines.append(f"- {answer}：{count} 票（{'、'.join(voters)}）");
        dissent = [item for item in valid if str(item.data.get("answer", "")).strip() != winner];
        if dissent:
            lines.append("");
            lines.append("### 分歧理由");
            for item in dissent:
                lines.append(f"- {item.name} 选择 {item.data.get('answer')}：{item.data.get('reason', '未给出理由')}");
        if failed:
            lines.append("");
            lines.append("### 失败分支");
            lines.extend(f"- {item.name}：{item.error}" for item in failed);
        return AggregatedResult(
            summary="\n".join(lines),
            data={
                "winner": winner if majority else "",
                "top_answer": winner,
                "agreement_rate": agreement_rate,
                "majority": majority,
                "votes": dict(ranking),
            },
            conflicts=conflicts,
            valid_branches=len(valid),
            failed_branches=len(failed),
        );
