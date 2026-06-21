from __future__ import annotations


class ClarificationEngine:
    def questions_for(self, user_request: str, known_answers: dict[str, str] | None = None) -> list[str]:
        known_answers = known_answers or {}
        lower = user_request.lower()
        questions: list[str] = []

        if "target_user" not in known_answers:
            questions.append("谁会使用这个 Agent，是你个人、一个小团队，还是企业团队？")
        if "expected_output" not in known_answers:
            questions.append("最终你希望它输出什么：摘要、分析、报告、草稿、执行结果，还是其他格式？")
        if "tool_scope" not in known_answers:
            questions.append("它需要调用哪些工具或资料源，哪些动作必须先让你确认？")
        if any(keyword in lower for keyword in ["投资", "财报", "合规", "写入", "发布", "交易", "risk"]) and "approval" not in known_answers:
            questions[-1] = "它可以读取哪些资料，哪些高风险结论或写入动作必须先经过你确认？"
        if "memory" in lower and "memory" not in known_answers:
            questions.append("它需要记住哪些偏好、规则或历史案例？")
        return questions[:3]
