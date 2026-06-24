"use client";

import { type FormEvent, type ReactNode, useState } from "react";
import Link from "next/link";
import {
  Bot,
  Check,
  CheckCircle2,
  GitBranch,
  Layers3,
  Loader2,
  MessageSquare,
  PackageCheck,
  Send,
  ShieldAlert,
  Sparkles,
  Wand2
} from "lucide-react";
import { apiFetch, getWorkflowAdminHeaders } from "../../lib/api";

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
    agent_id?: string;
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

type ReadinessReport = {
  overall_score: number;
  ready_for_candidate: boolean;
  blocking_gaps: string[];
  next_questions: string[];
  dimensions: Array<{
    name: string;
    score: number;
    blocker: boolean;
    notes: string;
  }>;
};

type SkillPackage = {
  skill_id: string;
  name: string;
  agent_id: string;
  trigger_scenarios: string[];
  tool_permissions: string[];
  memory_scope: string;
  failure_policy: string;
  usage_notes: string;
};

type BuildMessage = {
  role: "assistant" | "user" | string;
  content: string;
};

type SessionResponse = {
  session_id: string;
  assistant_message: string;
  clarifying_questions: string[];
  messages: BuildMessage[];
  topology_recommendation: Recommendation;
  current_blueprint: Blueprint;
  readiness_report: ReadinessReport;
  skill_packages: SkillPackage[];
  change_log: Array<{ summary: string; changed_sections: string[]; created_at: string }>;
};

type CandidateResponse = {
  workflow_package: {
    workflow_id: string;
    name: string;
    version: string;
  };
  skill_packages: SkillPackage[];
  readiness_report: ReadinessReport;
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
  const [candidateVersion, setCandidateVersion] = useState(nextCandidateVersion);
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [candidate, setCandidate] = useState<CandidateResponse | null>(null);
  const [loading, setLoading] = useState<"session" | "candidate" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submitMessage(event: FormEvent<HTMLFormElement>) {
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
      const response = session
        ? await apiFetch<SessionResponse>(`/api/agent-systems/sessions/${session.session_id}/messages`, {
            method: "POST",
            headers: getWorkflowAdminHeaders(),
            body: JSON.stringify({ message: trimmedRequest })
          })
        : await apiFetch<SessionResponse>("/api/agent-systems/sessions", {
            method: "POST",
            headers: getWorkflowAdminHeaders(),
            body: JSON.stringify({ user_request: trimmedRequest })
          });
      setCandidateVersion(nextCandidateVersion());
      setSession(response);
      setRequestText("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent 方案生成失败");
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
        headers: getWorkflowAdminHeaders(),
        body: JSON.stringify({ version: candidateVersion })
      });
      setCandidate(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "候选版本保存失败");
    } finally {
      setLoading(null);
    }
  }

  const blueprint = session?.current_blueprint;
  const ready = Boolean(session?.readiness_report.ready_for_candidate);

  return (
    <div className="chat-workspace">
      <section className="chat-thread" aria-label="Agent Builder conversation">
        <div className="chat-hero">
          <span className="agent-avatar">
            <Bot className="h-7 w-7" aria-hidden />
          </span>
          <div>
            <h1>告诉我你想让 Agent 做什么</h1>
            <p>我会连续追问、更新蓝图、生成 Skill 包，并在成熟度达标后保存候选版本</p>
          </div>
        </div>

        <div className="message-list">
          {!session ? (
            <MessageBubble role="assistant">
              直接说目标就行。我会把需求沉淀成生产级 Agent 方案，包括输入输出、工具权限、记忆策略、评估用例、审批边界和 Skill 包草案。
            </MessageBubble>
          ) : null}

          {session?.messages.map((message, index) => (
            <MessageBubble key={`${message.role}-${index}`} role={message.role === "user" ? "user" : "assistant"}>
              {message.content}
              {message.role === "assistant" && index === session.messages.length - 1 && blueprint ? (
                <>
                  <ReadinessPreview report={session.readiness_report} />
                  <PlanPreview blueprint={blueprint} recommendation={session.topology_recommendation} />
                  <SkillPreview skillPackages={session.skill_packages} />
                  <ChangePreview changes={session.change_log} />
                </>
              ) : null}
            </MessageBubble>
          ))}

          {session?.clarifying_questions.length ? (
            <MessageBubble role="assistant">
              <p className="text-sm font-semibold text-ink">下一轮优先确认</p>
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
                  <p className="font-semibold">候选版本和 Skill 包草案已保存</p>
                  <p>
                    {candidate.workflow_package.workflow_id}@{candidate.workflow_package.version} · {candidate.skill_packages.length} 个 Skill
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

        <form onSubmit={submitMessage} className="composer">
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
              placeholder={session ? "继续补充事实、限制、工具、输出格式或审批边界..." : "描述你想让 Agent 帮你完成的事..."}
              rows={2}
            />
            <button className="icon-send" type="submit" disabled={loading !== null} aria-label="发送">
              {loading === "session" ? <Loader2 className="h-5 w-5 animate-spin" aria-hidden /> : <Send className="h-5 w-5" aria-hidden />}
            </button>
          </div>

          <div className="composer-actions">
            <span>
              {session ? `生产成熟度 ${session.readiness_report.overall_score}%${ready ? "，可保存候选版本" : "，继续对话补齐阻塞项"}` : "先生成可审查方案，不会直接上线"}
            </span>
            <button className="save-candidate-button" type="button" onClick={saveCandidate} disabled={!session || !ready || loading !== null}>
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

function ReadinessPreview({ report }: { report: ReadinessReport }) {
  return (
    <div className="plan-preview">
      <div className="plan-header">
        <div>
          <p className="plan-eyebrow">生产成熟度</p>
          <h2>{report.overall_score}%</h2>
        </div>
        <span className={report.ready_for_candidate ? "soft-tag" : "status-pill status-warning"}>
          {report.ready_for_candidate ? "可保存候选" : "继续补齐"}
        </span>
      </div>
      <div className="plan-facts">
        {report.dimensions.slice(0, 4).map((dimension) => (
          <div key={dimension.name}>
            <Check className="h-4 w-4" aria-hidden />
            <span>{dimension.name}</span>
            <strong>{dimension.score}%</strong>
          </div>
        ))}
      </div>
      {report.blocking_gaps.length ? (
        <p className="plan-goal">阻塞项：{report.blocking_gaps.join("、")}</p>
      ) : null}
    </div>
  );
}

function PlanPreview({ blueprint, recommendation }: { blueprint: Blueprint; recommendation: Recommendation }) {
  const steps = blueprint.workflow_nodes.slice(0, 5);
  const subagents = blueprint.subagents.slice(0, 4);

  return (
    <div className="plan-preview">
      <div className="plan-header">
        <div>
          <p className="plan-eyebrow">Agent 蓝图</p>
          <h2>{blueprint.name}</h2>
        </div>
        <span className="soft-tag">候选草案</span>
      </div>

      <p className="plan-goal">{blueprint.primary_goal || blueprint.description}</p>

      <div className="plan-facts">
        <PlanFact icon={GitBranch} label="结构" value={topologyLabels[blueprint.topology_type]} />
        <PlanFact icon={Check} label="置信度" value={`${Math.round(recommendation.confidence * 100)}%`} />
        <PlanFact icon={ShieldAlert} label="风险" value={blueprint.risk_level} />
      </div>

      <div className="plan-section">
        <p className="plan-section-title">工作流节点</p>
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

function SkillPreview({ skillPackages }: { skillPackages: SkillPackage[] }) {
  return (
    <div className="plan-preview">
      <div className="plan-header">
        <div>
          <p className="plan-eyebrow">Skill 包草案</p>
          <h2>{skillPackages.length} 个可保存 Skill</h2>
        </div>
        <PackageCheck className="h-5 w-5 text-accent" aria-hidden />
      </div>
      <div className="plan-section compact">
        <div className="agent-chip-row">
          {skillPackages.slice(0, 8).map((skill) => (
            <span key={skill.skill_id}>{skill.name}</span>
          ))}
          {!skillPackages.length ? <span>等待蓝图生成</span> : null}
        </div>
      </div>
    </div>
  );
}

function ChangePreview({ changes }: { changes: SessionResponse["change_log"] }) {
  const latest = changes.at(-1);
  if (!latest) {
    return null;
  }
  return (
    <div className="plan-preview">
      <div className="plan-header">
        <div>
          <p className="plan-eyebrow">迭代记录</p>
          <h2>{latest.summary}</h2>
        </div>
        <Layers3 className="h-5 w-5 text-accent" aria-hidden />
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
