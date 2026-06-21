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
import { apiFetch, getLocalAuthHeaders } from "../../lib/api";

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
    if (!requestText.trim()) {
      setError("先告诉我你想让 Agent 做什么");
      return;
    }
    setLoading("session");
    setError(null);
    setCandidate(null);
    try {
      const response = await apiFetch<SessionResponse>("/api/agent-systems/sessions", {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({ user_request: requestText.trim() })
      });
      setSubmittedText(requestText.trim());
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
      const response = await apiFetch<CandidateResponse>(`/api/agent-systems/sessions/${session.session_id}/candidate`, {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
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
              <Link className="inline-flex-link mt-3" href={`/workflows/${candidate.workflow_package.workflow_id}`}>
                打开高级详情
              </Link>
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
