"use client";

import { type FormEvent, type ReactNode, useState } from "react";
import Link from "next/link";
import {
  Bot,
  Check,
  CheckCircle2,
  GitBranch,
  Loader2,
  MessageSquare,
  Send,
  ShieldAlert,
  Sparkles,
  Wand2
} from "lucide-react";
import { apiFetch, getStoredAuthSession } from "../../lib/api";

type TopologyType = "single_agent" | "workflow_agent" | "manager_subagents" | "multi_agent_workflow";

type Recommendation = {
  topology_type: TopologyType;
  confidence: number;
  reason: string;
  suggested_agents: string[];
  suggested_questions: string[];
};

type Subagent = {
  subagent_id: string;
  name: string;
  specialty: string;
  description: string;
  human_approval_required: boolean;
};

type WorkflowNode = {
  node_id: string;
  name: string;
  node_type: string;
  assigned_agent_id?: string | null;
  approval_required: boolean;
};

type Blueprint = {
  system_id: string;
  name: string;
  description: string;
  primary_goal: string;
  expected_outputs: string[];
  topology_type: TopologyType;
  mother_agent?: {
    name: string;
    responsibility: string;
    allowed_subagents: string[];
  } | null;
  subagents: Subagent[];
  workflow_nodes: WorkflowNode[];
  tool_requirements: string[];
  memory_requirements: string[];
  evaluation_requirements: string[];
  approval_requirements: string[];
  risk_level: string;
  release_policy: string;
};

type SessionResponse = {
  session_id: string;
  assistant_message: string;
  clarifying_questions: string[];
  topology_recommendation: Recommendation;
  current_blueprint: Blueprint;
};

type CandidateResponse = {
  workflow_package: {
    workflow_id: string;
    name: string;
    version: string;
  };
  saved_as_current: boolean;
};

const topologyLabels: Record<TopologyType, string> = {
  single_agent: "单 Agent",
  workflow_agent: "流程型 Agent",
  manager_subagents: "母 Agent 协作",
  multi_agent_workflow: "多 Agent 工作流"
};

const examples = [
  "帮我做一个客户跟进 Agent：自动整理沟通记录，提醒下一步，并输出周报",
  "我想要一个学习助手，能拆解目标、安排每日任务，并检查复盘",
  "做一个投研 Agent，跟踪新闻和财报，只输出机会、风险和观察清单"
];

function nextCandidateVersion() {
  const now = new Date();
  const stamp = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    String(now.getHours()).padStart(2, "0"),
    String(now.getMinutes()).padStart(2, "0")
  ].join("");
  return `0.1.0-chat-${stamp}`;
}

export default function AgentSystemsPage() {
  const [requestText, setRequestText] = useState("");
  const [submittedText, setSubmittedText] = useState("");
  const [candidateVersion, setCandidateVersion] = useState(nextCandidateVersion);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [candidate, setCandidate] = useState<CandidateResponse | null>(null);
  const [loading, setLoading] = useState<"session" | "candidate" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const blueprint = session?.current_blueprint;

  async function startSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedRequest = requestText.trim();
    if (!trimmedRequest) {
      setError("先告诉我你想让 Agent 做什么");
      return;
    }
    setLoading("session");
    setError(null);
    setCandidate(null);
    try {
      const authSession = getStoredAuthSession("workflow-admin");
      if (!authSession) {
        setSubmittedText(trimmedRequest);
        setCandidateVersion(nextCandidateVersion());
        setSession(createLocalTestSession(trimmedRequest));
        return;
      }

      const response = await apiFetch<SessionResponse>("/api/agent-systems/sessions", {
        method: "POST",
        headers: { Authorization: `Bearer ${authSession.accessToken}` },
        body: JSON.stringify({ user_request: trimmedRequest })
      });
      setSubmittedText(trimmedRequest);
      setCandidateVersion(nextCandidateVersion());
      setSession(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "会话创建失败");
    } finally {
      setLoading(null);
    }
  }

  async function saveCandidate() {
    if (!session) {
      return;
    }
    setLoading("candidate");
    setError(null);
    try {
      if (session.session_id.startsWith("local-test-")) {
        setCandidate({
          workflow_package: {
            workflow_id: session.current_blueprint.system_id,
            name: session.current_blueprint.name,
            version: candidateVersion
          },
          saved_as_current: false
        });
        return;
      }

      const authSession = getStoredAuthSession("workflow-admin");
      if (!authSession) {
        throw new Error("当前是本地测试模式，不会写入真实后端");
      }

      const response = await apiFetch<CandidateResponse>(`/api/agent-systems/sessions/${session.session_id}/candidate`, {
        method: "POST",
        headers: { Authorization: `Bearer ${authSession.accessToken}` },
        body: JSON.stringify({ version: candidateVersion })
      });
      setCandidate(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "候选版本保存失败");
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="chat-workspace">
      <section className="chat-thread" aria-label="Agent Builder conversation">
        <div className="chat-hero">
          <span className="agent-avatar">
            <Bot className="h-7 w-7" aria-hidden />
          </span>
          <div>
            <h1>告诉我你想让 Agent 做什么</h1>
            <p>用自然语言说目标就行，我会自动整理成可运行的 Agent 方案，不让你填技术表格</p>
          </div>
        </div>

        <div className="message-list">
          <MessageBubble role="assistant">
            你可以直接说：“帮我做一个客户跟进 Agent”，我会判断该用单 Agent、母 Agent 还是多 Agent 流程
          </MessageBubble>

          {session ? <MessageBubble role="user">{submittedText}</MessageBubble> : null}

          {session ? (
            <MessageBubble role="assistant">
              {session.assistant_message}
              {blueprint ? <PlanPreview blueprint={blueprint} recommendation={session.topology_recommendation} /> : null}
            </MessageBubble>
          ) : null}

          {session?.clarifying_questions.length ? (
            <MessageBubble role="assistant">
              <p className="text-sm font-semibold text-ink">我还会自动追问这几件事</p>
              <div className="mt-3 grid gap-2">
                {session.clarifying_questions.slice(0, 3).map((question) => (
                  <button
                    key={question}
                    type="button"
                    className="question-row"
                    onClick={() => setRequestText((current) => `${current.trim()}\n${question}：`)}
                  >
                    <MessageSquare className="h-4 w-4" aria-hidden />
                    <span>{question}</span>
                  </button>
                ))}
              </div>
            </MessageBubble>
          ) : null}

          {candidate ? (
            <MessageBubble role="assistant">
              <div className="success-strip">
                <CheckCircle2 className="h-5 w-5" aria-hidden />
                <div>
                  <p className="font-semibold">候选版本已保存</p>
                  <p>
                    {candidate.workflow_package.workflow_id}@{candidate.workflow_package.version}
                  </p>
                </div>
              </div>
              {candidate.workflow_package.workflow_id.startsWith("local-test-") ? (
                <p className="mt-3 text-sm text-muted">本地测试候选已生成，没有写入真实后台。</p>
              ) : (
                <Link className="inline-flex-link mt-3" href={`/workflows/${candidate.workflow_package.workflow_id}`}>
                  打开高级详情
                </Link>
              )}
            </MessageBubble>
          ) : null}
        </div>

        {error ? <div className="chat-error">{error}</div> : null}

        <form onSubmit={startSession} className="composer">
          {!session ? (
            <div className="quick-prompts" aria-label="Examples">
              {examples.map((example) => (
                <button key={example} type="button" onClick={() => setRequestText(example)}>
                  <Wand2 className="h-4 w-4" aria-hidden />
                  <span>{example}</span>
                </button>
              ))}
            </div>
          ) : null}

          <div className="composer-row">
            <textarea
              aria-label="告诉我你想让 Agent 做什么"
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
              placeholder={session ? "继续补充你的想法..." : "描述你想让 Agent 帮你完成的事..."}
              rows={2}
            />
            <button className="icon-send" type="submit" disabled={loading !== null} aria-label="生成方案">
              {loading === "session" ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> : <Send className="h-5 w-5" aria-hidden />}
            </button>
          </div>

          <div className="composer-actions">
            <span>{session ? "方案已生成，可以继续补充，也可以保存候选版本" : "不会直接上线，先生成可审查方案"}</span>
            <button className="save-candidate-button" type="button" onClick={saveCandidate} disabled={!session || loading !== null}>
              {loading === "candidate" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Sparkles className="h-4 w-4" aria-hidden />}
              保存候选版本
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

function createLocalTestSession(userRequest: string): SessionResponse {
  const systemId = `local-test-agent-${Date.now()}`;
  const hasApproval = /审批|权限|发送|发布|写入|删除|下单|付款|通知/.test(userRequest);
  const hasResearch = /研究|新闻|财报|资料|搜索|分析|报告|观察/.test(userRequest);
  const hasFollowUp = /跟进|提醒|客户|沟通|订单|任务|复盘/.test(userRequest);
  const topologyType: TopologyType = hasResearch || hasFollowUp ? "manager_subagents" : "workflow_agent";

  const workflowNodes: WorkflowNode[] = [
    {
      node_id: "understand-goal",
      name: "理解目标",
      node_type: "reasoning",
      assigned_agent_id: "coordinator",
      approval_required: false
    },
    {
      node_id: "collect-context",
      name: hasResearch ? "整理资料" : "整理上下文",
      node_type: "tool",
      assigned_agent_id: hasResearch ? "research-agent" : "coordinator",
      approval_required: false
    },
    {
      node_id: "make-plan",
      name: hasFollowUp ? "生成跟进计划" : "生成执行方案",
      node_type: "reasoning",
      assigned_agent_id: "coordinator",
      approval_required: false
    },
    {
      node_id: "final-output",
      name: "输出结果",
      node_type: "final",
      assigned_agent_id: "coordinator",
      approval_required: hasApproval
    }
  ];

  return {
    session_id: `local-test-${Date.now()}`,
    assistant_message: "先用本地测试模式生成一版方案，不需要账号密码，也不会写入真实后端。你可以继续补充需求，我再帮你调整。",
    clarifying_questions: ["主要给谁使用？", "输出结果希望是什么格式？", "哪些动作必须先经过你确认？"],
    topology_recommendation: {
      topology_type: topologyType,
      confidence: 0.82,
      reason: "本地测试模式根据需求关键词做轻量判断，真实后端接入后会使用完整 Builder Plane 校验。",
      suggested_agents: topologyType === "manager_subagents" ? ["协调 Agent", "资料整理 Agent", "输出审查 Agent"] : ["流程 Agent"],
      suggested_questions: ["主要给谁使用？", "输出结果希望是什么格式？", "哪些动作必须先经过你确认？"]
    },
    current_blueprint: {
      system_id: systemId,
      name: inferAgentName(userRequest),
      description: userRequest,
      primary_goal: userRequest,
      expected_outputs: ["执行方案", "阶段性结果", "风险或确认事项"],
      topology_type: topologyType,
      mother_agent: topologyType === "manager_subagents" ? {
        name: "协调 Agent",
        responsibility: "理解目标、拆解任务、调用合适的子 Agent，并在关键动作前等待确认。",
        allowed_subagents: ["research-agent", "review-agent"]
      } : null,
      subagents: topologyType === "manager_subagents" ? [
        {
          subagent_id: "research-agent",
          name: hasResearch ? "资料整理 Agent" : "执行整理 Agent",
          specialty: hasResearch ? "资料收集与摘要" : "任务拆解与跟进",
          description: hasResearch ? "整理输入资料，提取关键信息和风险点。" : "把目标拆成可执行步骤，并生成提醒或复盘结构。",
          human_approval_required: false
        },
        {
          subagent_id: "review-agent",
          name: "输出审查 Agent",
          specialty: "质量和风险检查",
          description: "检查结果是否完整、是否需要人工确认、是否存在高风险动作。",
          human_approval_required: hasApproval
        }
      ] : [],
      workflow_nodes: workflowNodes,
      tool_requirements: ["本地测试先不调用真实外部工具"],
      memory_requirements: ["保存用户偏好和常用输出格式"],
      evaluation_requirements: ["检查输出是否覆盖目标、下一步、风险"],
      approval_requirements: hasApproval ? ["涉及写入、通知、发布等动作前需要确认"] : ["默认不直接执行外部写操作"],
      risk_level: hasApproval ? "medium" : "low",
      release_policy: "本地测试只生成候选方案，不直接上线"
    }
  };
}

function inferAgentName(userRequest: string) {
  if (/投研|投资|财报|新闻/.test(userRequest)) {
    return "投研观察 Agent";
  }
  if (/客户|跟进|沟通/.test(userRequest)) {
    return "客户跟进 Agent";
  }
  if (/学习|复盘|目标/.test(userRequest)) {
    return "学习复盘 Agent";
  }
  return "自定义 Agent";
}

function MessageBubble({ role, children }: { role: "assistant" | "user"; children: ReactNode }) {
  return (
    <div className={role === "user" ? "message message-user" : "message message-assistant"}>
      {role === "assistant" ? (
        <span className="message-icon">
          <Bot className="h-4 w-4" aria-hidden />
        </span>
      ) : null}
      <div className="message-body">{children}</div>
    </div>
  );
}

function PlanPreview({ blueprint, recommendation }: { blueprint: Blueprint; recommendation: Recommendation }) {
  const steps = blueprint.workflow_nodes.slice(0, 4);
  const subagents = blueprint.subagents.slice(0, 3);

  return (
    <div className="plan-preview">
      <div className="plan-header">
        <div>
          <p className="plan-eyebrow">生成方案</p>
          <h2>{blueprint.name}</h2>
        </div>
        <span className="soft-tag">可运行预览</span>
      </div>

      <p className="plan-goal">{blueprint.primary_goal || blueprint.description}</p>

      <div className="plan-facts">
        <PlanFact icon={GitBranch} label="结构" value={topologyLabels[blueprint.topology_type]} />
        <PlanFact icon={Check} label="置信度" value={`${Math.round(recommendation.confidence * 100)}%`} />
        <PlanFact icon={ShieldAlert} label="风险" value={blueprint.risk_level} />
      </div>

      <div className="plan-section">
        <p className="plan-section-title">它会怎么工作</p>
        <div className="step-list">
          {steps.map((node, index) => (
            <div key={node.node_id} className="step-row">
              <span>{index + 1}</span>
              <div>
                <p>{node.name}</p>
                <small>
                  {node.assigned_agent_id || "默认 Agent"}
                  {node.approval_required ? " · 需要审批" : ""}
                </small>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="plan-section compact">
        <p className="plan-section-title">Agent 分工</p>
        <div className="agent-chip-row">
          {blueprint.mother_agent ? <span>{blueprint.mother_agent.name}</span> : null}
          {subagents.map((subagent) => (
            <span key={subagent.subagent_id}>{subagent.name}</span>
          ))}
          {!blueprint.mother_agent && subagents.length === 0 ? <span>单 Agent 先跑通</span> : null}
        </div>
      </div>
    </div>
  );
}

function PlanFact({ icon: Icon, label, value }: { icon: typeof GitBranch; label: string; value: string }) {
  return (
    <div>
      <Icon className="h-4 w-4" aria-hidden />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
