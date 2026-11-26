# 模型调用都收在这里。默认离线，配了密钥再切 OpenAI 兼容接口。
# 离线答案是写死的，按角色取，主要为了让新克隆的项目不配密钥也能跑完整流程。

import json;
import os;
import re;
import time;
from typing import Any, Dict, Optional;

V_默认模型 = "gpt-4o-mini";
V_离线延迟秒 = 0.45;
V_离线脚本 = {
    ("review", "合规"): {
        "overall": "整体框架合规，但跨境数据与授权链路存在缺口。",
        "findings": [
            {
                "topic": "用户数据出境",
                "stance": "整改",
                "severity": "高",
                "detail": "文档没有说明跨境传输前的安全评估与单独授权流程。",
            },
            {
                "topic": "日志留存周期",
                "stance": "通过",
                "severity": "低",
                "detail": "日志留存 180 天，满足常规审计要求。",
            },
            {
                "topic": "第三方共享清单",
                "stance": "整改",
                "severity": "中",
                "detail": "缺少第三方共享方清单和对应的数据处理协议编号。",
            },
        ],
    },
    ("review", "技术"): {
        "overall": "架构方案清晰，但降级路径和容量验证还缺细节。",
        "findings": [
            {
                "topic": "用户数据出境",
                "stance": "通过",
                "severity": "低",
                "detail": "采用区域化部署，技术上可以做到数据不出境。",
            },
            {
                "topic": "接口限流策略",
                "stance": "整改",
                "severity": "中",
                "detail": "没有给出突发流量下的限流阈值和熔断策略。",
            },
            {
                "topic": "日志留存周期",
                "stance": "通过",
                "severity": "低",
                "detail": "日志接入统一采集，保留周期可配置。",
            },
        ],
    },
    ("review", "安全"): {
        "overall": "安全基线基本达标，密钥管理和越权风险需要整改。",
        "findings": [
            {
                "topic": "用户数据出境",
                "stance": "整改",
                "severity": "高",
                "detail": "境外节点缓存了用户明文标识，存在实际出境风险。",
            },
            {
                "topic": "接口限流策略",
                "stance": "整改",
                "severity": "中",
                "detail": "限流只做在网关层，内部服务之间没有任何保护。",
            },
            {
                "topic": "日志留存周期",
                "stance": "通过",
                "severity": "低",
                "detail": "审计日志留存 180 天，满足追溯要求。",
            },
        ],
    },
    ("solution", "成本优先"): {
        "title": "渐进式灰度改造方案",
        "summary": "先复用现有组件做低成本灰度，把预算压在瓶颈链路上。",
        "score": 78,
        "steps": [
            "盘点现有组件，只在数据同步链路增加轻量适配层。",
            "按 5% 流量灰度，观察两周后再决定是否扩大。",
            "把省下的预算投入到监控和回滚能力的补齐。",
        ],
        "risks": [
            "改造范围小，历史技术债会继续保留。",
        ],
    },
    ("solution", "体验优先"): {
        "title": "体验驱动的统一入口方案",
        "summary": "先统一入口和交互，再逐步下沉能力，用户感知提升最快。",
        "score": 88,
        "steps": [
            "统一工作台入口，收敛三套重复的账号体系。",
            "把高频操作前置到首页，减少两次以上的跳转层级。",
            "灰度 10% 用户，用留存和任务完成率验证效果。",
        ],
        "risks": [
            "前期设计与改造投入集中，需要设计资源提前到位。",
        ],
    },
    ("solution", "风险优先"): {
        "title": "合规兜底的双轨方案",
        "summary": "新老链路并行，先把合规和数据一致性做扎实，再切换主链路。",
        "score": 84,
        "steps": [
            "保留旧链路作为兜底，新链路先承接只读流量。",
            "补齐数据校验、对账和回滚脚本。",
            "连续两周对账无差异后再切主链路。",
        ],
        "risks": [
            "双轨期间维护成本较高，需要明确下线时间点。",
        ],
    },
    ("vote", "模型甲"): {
        "answer": "A",
        "reason": "数据同步链路是上游瓶颈，先修它收益最大。",
    },
    ("vote", "模型乙"): {
        "answer": "A",
        "reason": "同步链路的延迟会放大到所有下游，优先级最高。",
    },
    ("vote", "模型丙"): {
        "answer": "B",
        "reason": "报表查询是用户反馈最多的入口，体验收益更直接。",
    },
};
V_离线延迟表 = {
    ("review", "合规"): 0.45,
    ("review", "技术"): 0.55,
    ("review", "安全"): 1.25,
    ("solution", "成本优先"): 0.50,
    ("solution", "体验优先"): 0.60,
    ("solution", "风险优先"): 0.55,
    ("vote", "模型甲"): 0.40,
    ("vote", "模型乙"): 0.50,
    ("vote", "模型丙"): 0.45,
};


def parse_json(raw: str) -> Dict[str, Any]:
    # 模型爱在 JSON 外面包 ``` 或者加两句废话，这里把花括号那段抠出来。
    text = (raw or "").strip();
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S);
    if fenced:
        text = fenced.group(1);
    start = text.find("{");
    end = text.rfind("}");
    if start == -1 or end == -1 or end <= start:
        return {};
    try:
        data = json.loads(text[start : end + 1]);
    except json.JSONDecodeError:
        return {};
    return data if isinstance(data, dict) else {};


class BaseLLM:
    # 模型接口。meta 只给离线 mock 用，真实接口直接忽略。
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name;

    def chat(self, system: str, user: str, meta: Optional[Dict[str, Any]] = None) -> str:
        # 返回纯文本。
        raise NotImplementedError;

    def chat_json(self, system: str, user: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 要 JSON 的调用。解析不出来就返回空字典，让上层走兜底，不在这里抛异常。
        return parse_json(self.chat(system, user, meta));


class OfflineLLM(BaseLLM):
    # 离线模型。按 meta 取预置答案，sleep 一下模拟真实延迟。
    def __init__(self, model_name: str = "offline-mock", delay: float = V_离线延迟秒) -> None:
        super().__init__(model_name);
        self.delay = delay;

    def chat(self, system: str, user: str, meta: Optional[Dict[str, Any]] = None) -> str:
        # 同样的输入永远得到同样的输出，测试和演示都可复现。
        meta = meta or {};
        key = (str(meta.get("role", "")), str(meta.get("label", "")));
        time.sleep(V_离线延迟表.get(key, self.delay));
        if key in V_离线脚本:
            return json.dumps(V_离线脚本[key], ensure_ascii=False);
        return json.dumps({"overall": "离线模拟回复，未匹配到预置脚本。", "findings": []}, ensure_ascii=False);


class OpenAICompatibleLLM(BaseLLM):
    # 真实模型。走 OpenAI SDK，base_url 指向哪家都行。
    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: Optional[str] = None,
        temperature: float = 0.3,
    ) -> None:
        super().__init__(model_name);
        try:
            from openai import OpenAI;
        except ImportError as exc:
            raise RuntimeError("缺少 openai 依赖，先执行 pip install -r requirements.txt") from exc;
        self.temperature = temperature;
        self.client = OpenAI(api_key=api_key, base_url=base_url);

    def chat(self, system: str, user: str, meta: Optional[Dict[str, Any]] = None) -> str:
        # 同步调用，Agent 那边用线程池包着，不会卡事件循环。
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
        );
        return response.choices[0].message.content or "";


def make_llm(model_name: Optional[str] = None, offline: Optional[bool] = None) -> BaseLLM:
    # offline 不传就看有没有密钥。没密钥自动离线，保证 clone 下来能直接跑。
    api_key = os.getenv("FANOUT_API_KEY") or os.getenv("OPENAI_API_KEY");
    if offline is None:
        offline = not bool(api_key);
    if offline:
        return OfflineLLM(model_name or "offline-mock");
    if not api_key:
        raise RuntimeError("没有找到 API Key，请先设置 FANOUT_API_KEY 或 OPENAI_API_KEY。");
    base_url = os.getenv("FANOUT_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None;
    return OpenAICompatibleLLM(model_name or os.getenv("FANOUT_MODEL") or V_默认模型, api_key, base_url);
