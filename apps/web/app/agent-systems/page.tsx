"use client";

import { type FormEvent, useState } from "react";
import Link from "next/link";
import { Bot, CheckCircle2, GitBranch, MessageSquare, Save, ShieldAlert, Sparkles, Workflow } from "lucide-react";
import { JsonViewer } from "../../components/JsonViewer";
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
  when_to_use: string;
  allowed_tools: string[];
  context_policy: string;
  human_approval_required: boolean;
};

type WorkflowNode = {
  node_id: string;
  name: string;
  node_type: string;
  assigned_agent_id?: string | null;
  dependencies: string[];
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
    agent_id: string;
    name: string;
    role: string;
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
  extracted_brief: Record<string, unknown>;
  topology_recommendation: Recommendation;
  current_blueprint: Blueprint;
};

type CandidateResponse = {
  workflow_package: {
    workflow_id: string;
    name: string;
    version: string;
  };
  validation_report: unknown;
  saved_as_current: boolean;
};

const topologyLabels: Record<TopologyType, string> = {
  single_agent: "单 Agent",
  workflow_agent: "单 Agent 工作流",
  manager_subagents: "母 Agent + Subagents",
  multi_agent_workflow: "多 Agent 工作流"
};

export default function AgentSystemsPage() {
  const [requestText, setRequestText] = useState("我想做一个帮我做投资研究的 Agent，能看新闻和财报，最后输出风险提示和观察报告");
  const [version, setVersion] = useState("0.2.0-agent-system");
  const [session, setSession] = useState<SessionResponse | null>(null);
  const [candidate, setCandidate] = useState<CandidateResponse | null>(null);
  const [loading, setLoading] = useState<"session" | "candidate" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function startSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading("session");
    setError(null);
    setCandidate(null);
    try {
      const response = await apiFetch<SessionResponse>("/api/agent-systems/sessions", {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify({ user_request: requestText })
      });
      setSession(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent System session failed / 会话创建失败");
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
        body: JSON.stringify({ version })
      });
      setCandidate(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Candidate save failed / 候选版本保存失败");
    } finally {
      setLoading(null);
    }
  }

  const blueprint = session?.current_blueprint;

  return (
    <div className="space-y-5">
      <section className="page-band">
        <div>
          <p className="section-kicker">Agent System Builder / 智能体系统搭建器</p>
          <h1 className="page-heading">Chat-first Agent system creation / 对话式创建智能体系统</h1>
          <p className="page-subtitle">
            默认入口只需要描述需求。系统会先反问、判断形态，再生成可审查的 Agent 系统蓝图，确认后只保存 candidate。
          </p>
        </div>
        <span className="status-pill status-accent">Candidate only / 不直接上线</span>
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,560px)_1fr]">
        <form onSubmit={startSession} className="section-panel space-y-4">
          <div className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5 text-accent" aria-hidden />
            <h2 className="section-title">默认对话窗口</h2>
          </div>
          <label className="form-label">
            <span className="form-label-text">你的需求</span>
            <textarea
              className="control-textarea min-h-44"
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap items-end gap-3">
            <label className="form-label min-w-52">
              <span className="form-label-text">Candidate version</span>
              <input className="control-input w-full" value={version} onChange={(event) => setVersion(event.target.value)} />
            </label>
            <button className="control-button-primary" type="submit" disabled={loading !== null}>
              <Sparkles className="h-4 w-4" aria-hidden />
              生成蓝图
            </button>
            <button className="control-button" type="button" onClick={saveCandidate} disabled={!session || loading !== null}>
              <Save className="h-4 w-4" aria-hidden />
              保存 candidate
            </button>
          </div>
          {error ? <p className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}
          {session ? (
            <div className="rounded-md border border-line bg-field p-3 text-sm leading-6 text-slate-700">
              {session.assistant_message}
            </div>
          ) : null}
        </form>

        <div className="space-y-4">
          {blueprint ? (
            <>
              <div className="grid gap-4 md:grid-cols-3">
                <InfoCard title="当前理解" icon={Bot} value={blueprint.name} detail={blueprint.primary_goal} />
                <InfoCard
                  title="推荐形态"
                  icon={GitBranch}
                  value={topologyLabels[blueprint.topology_type]}
                  detail={`${Math.round(session.topology_recommendation.confidence * 100)}% confidence`}
                />
                <InfoCard title="上线风险" icon={ShieldAlert} value={blueprint.risk_level} detail={blueprint.release_policy} />
              </div>

              <section className="section-panel">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <h2 className="section-title">还缺什么信息</h2>
                  <span className="status-pill">{session.clarifying_questions.length} questions</span>
                </div>
                <div className="grid gap-2 md:grid-cols-3">
                  {session.clarifying_questions.map((question, index) => (
                    <div key={question} className="rounded-md border border-line bg-[#fbfcfd] p-3 text-sm leading-5 text-slate-700">
                      <span className="mb-2 block text-xs font-semibold text-accent">Q{index + 1}</span>
                      {question}
                    </div>
                  ))}
                </div>
              </section>

              <section className="grid gap-4 lg:grid-cols-[360px_1fr]">
                <div className="section-panel">
                  <h2 className="section-title">母 Agent</h2>
                  {blueprint.mother_agent ? (
                    <div className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
                      <p className="font-semibold text-ink">{blueprint.mother_agent.name}</p>
                      <p>{blueprint.mother_agent.responsibility}</p>
                      <p className="text-xs text-slate-500">Allowed subagents: {blueprint.mother_agent.allowed_subagents.length}</p>
                    </div>
                  ) : (
                    <p className="mt-3 text-sm text-slate-500">当前形态不需要母 Agent。</p>
                  )}
                </div>
                <div className="section-panel">
                  <h2 className="section-title">Subagents</h2>
                  <div className="mt-3 grid gap-3 md:grid-cols-2">
                    {blueprint.subagents.length > 0 ? blueprint.subagents.map((subagent) => (
                      <div key={subagent.subagent_id} className="rounded-md border border-line bg-white p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-semibold text-ink">{subagent.name}</p>
                            <p className="text-xs text-slate-500">{subagent.specialty}</p>
                          </div>
                          {subagent.human_approval_required ? <span className="status-pill status-warning">approval</span> : null}
                        </div>
                        <p className="mt-3 text-sm leading-6 text-slate-700">{subagent.description}</p>
                        <p className="mt-2 text-xs text-slate-500">Tools: {subagent.allowed_tools.join(", ") || "none"} · Context: {subagent.context_policy}</p>
                      </div>
                    )) : (
                      <p className="text-sm text-slate-500">当前建议从单 Agent 开始，不强行拆分。</p>
                    )}
                  </div>
                </div>
              </section>

              <section className="section-panel">
                <div className="mb-4 flex items-center gap-2">
                  <Workflow className="h-5 w-5 text-accent" aria-hidden />
                  <h2 className="section-title">简单 Agent 蓝图流程</h2>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  {blueprint.workflow_nodes.map((node) => (
                    <div key={node.node_id} className="rounded-md border border-line bg-[#fbfcfd] p-3">
                      <p className="text-sm font-semibold text-ink">{node.name}</p>
                      <p className="mt-1 text-xs text-slate-500">{node.node_type}</p>
                      <p className="mt-2 text-xs text-slate-600">Agent: {node.assigned_agent_id || "default"}</p>
                      {node.approval_required ? <span className="mt-3 status-pill status-warning">人工审批</span> : null}
                    </div>
                  ))}
                </div>
              </section>

              <section className="grid gap-4 lg:grid-cols-4">
                <ListCard title="工具需求" items={blueprint.tool_requirements} />
                <ListCard title="记忆需求" items={blueprint.memory_requirements} />
                <ListCard title="评测需求" items={blueprint.evaluation_requirements} />
                <ListCard title="审批需求" items={blueprint.approval_requirements} />
              </section>

              {candidate ? (
                <section className="section-panel">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h2 className="section-title">Candidate saved / 候选版本已保存</h2>
                      <p className="mt-1 text-sm text-slate-600">
                        {candidate.workflow_package.workflow_id}@{candidate.workflow_package.version} · saved_as_current={String(candidate.saved_as_current)}
                      </p>
                    </div>
                    <Link className="control-button" href={`/workflows/${candidate.workflow_package.workflow_id}`}>
                      查看工作流
                    </Link>
                  </div>
                  <div className="mt-4">
                    <JsonViewer data={candidate.validation_report} />
                  </div>
                </section>
              ) : null}
            </>
          ) : (
            <section className="section-panel flex min-h-96 items-center justify-center text-center">
              <div>
                <CheckCircle2 className="mx-auto h-10 w-10 text-accent" aria-hidden />
                <p className="mt-3 text-sm text-slate-600">输入需求后，这里会显示推荐形态、母 Agent、Subagents、节点、工具、记忆、评测和风险。</p>
              </div>
            </section>
          )}
        </div>
      </section>
    </div>
  );
}

function InfoCard({ title, value, detail, icon: Icon }: { title: string; value: string; detail: string; icon: typeof Bot }) {
  return (
    <div className="metric-card">
      <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
        <Icon className="h-4 w-4 text-accent" aria-hidden />
        {title}
      </div>
      <p className="mt-2 text-base font-semibold text-ink">{value}</p>
      <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-600">{detail}</p>
    </div>
  );
}

function ListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="section-panel">
      <h2 className="section-title">{title}</h2>
      <ul className="mt-3 space-y-2 text-sm text-slate-700">
        {(items.length ? items : ["待确认"]).map((item) => (
          <li key={item} className="flex gap-2">
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
