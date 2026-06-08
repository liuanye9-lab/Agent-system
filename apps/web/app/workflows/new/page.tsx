"use client";

import { type FormEvent, type ReactNode, useState } from "react";
import Link from "next/link";
import { Check, Plus, Sparkles, Trash2, Upload } from "lucide-react";
import { apiFetch, getLocalAuthHeaders } from "../../../lib/api";
import { JsonViewer } from "../../../components/JsonViewer";

type GenerateResponse = {
  workflow_package: {
    workflow_id: string;
    name: string;
    version: string;
  };
  clarifying_questions: string[];
};

type WorkflowPackageSummary = {
  workflow_id: string;
  name?: string;
  version: string;
};

type PackageOperationResponse = {
  valid?: boolean;
  workflow_id?: string;
  version?: string;
  workflow_package?: WorkflowPackageSummary;
  saved_as_current?: boolean;
  validation_report?: unknown;
  errors?: unknown[];
  warnings?: unknown[];
};

type ProcessNodeDraft = {
  id: string;
  name: string;
  node_type: "read_node" | "reasoning_node" | "review_node" | "write_node";
  owner_role: string;
  description: string;
  done_condition: string;
  requires_approval: boolean;
};

export default function NewWorkflowPage() {
  const [userRequest, setUserRequest] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [version, setVersion] = useState("0.1.0");
  const [saveAsCurrent, setSaveAsCurrent] = useState(true);
  const [name, setName] = useState("");
  const [businessGoal, setBusinessGoal] = useState("");
  const [startEvent, setStartEvent] = useState("");
  const [endState, setEndState] = useState("");
  const [targetUsersText, setTargetUsersText] = useState("");
  const [humanRolesText, setHumanRolesText] = useState("");
  const [successMetricsText, setSuccessMetricsText] = useState("");
  const [constraintsText, setConstraintsText] = useState("");
  const [risksText, setRisksText] = useState("");
  const [processNodes, setProcessNodes] = useState<ProcessNodeDraft[]>([]);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const [packageJsonText, setPackageJsonText] = useState("");
  const [importSaveAsCurrent, setImportSaveAsCurrent] = useState(false);
  const [packageResult, setPackageResult] = useState<PackageOperationResponse | null>(null);
  const [packageBusy, setPackageBusy] = useState<"validate" | "import" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const preparedNodes = processNodes
        .filter((node) => node.name.trim() && node.owner_role.trim())
        .map((node) => ({
          name: node.name.trim(),
          node_type: node.node_type,
          owner_role: node.owner_role.trim(),
          description: node.description.trim() || undefined,
          done_condition: node.done_condition.trim() || undefined,
          requires_approval: node.requires_approval
        }));
      const requestBody = {
        user_request: userRequest,
        version: version.trim() || "0.1.0",
        save_as_current: saveAsCurrent,
        workflow_id: workflowId.trim() || undefined,
        name: name.trim() || undefined,
        business_goal: businessGoal.trim() || undefined,
        start_event: startEvent.trim() || undefined,
        end_state: endState.trim() || undefined,
        target_users: lines(targetUsersText),
        human_roles: lines(humanRolesText),
        success_metrics: lines(successMetricsText),
        constraints: lines(constraintsText),
        risks: lines(risksText),
        process_nodes: preparedNodes.length > 0 ? preparedNodes : undefined
      };
      const response = await apiFetch<GenerateResponse>("/api/workflows/generate", {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify(requestBody)
      });
      setResult(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to generate workflow / 生成工作流失败");
    } finally {
      setLoading(false);
    }
  }

  function addNode() {
    const nextIndex = processNodes.length + 1;
    setProcessNodes([
      ...processNodes,
      {
        id: `node-${Date.now()}-${nextIndex}`,
        name: "",
        node_type: "reasoning_node",
        owner_role: "",
        description: "",
        done_condition: "",
        requires_approval: false
      }
    ]);
  }

  function updateNode(id: string, patch: Partial<ProcessNodeDraft>) {
    setProcessNodes(processNodes.map((node) => (node.id === id ? { ...node, ...patch } : node)));
  }

  function removeNode(id: string) {
    setProcessNodes(processNodes.filter((node) => node.id !== id));
  }

  function parsePackageJson() {
    let payload: unknown;
    try {
      payload = JSON.parse(packageJsonText);
    } catch {
      setError("Workflow package JSON must be valid JSON / 工作流包 JSON 必须是有效 JSON");
      return null;
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      setError("Workflow package JSON must be a JSON object / 工作流包 JSON 必须是 JSON 对象");
      return null;
    }
    return payload as Record<string, unknown>;
  }

  async function validatePackage() {
    setPackageBusy("validate");
    setError(null);
    setPackageResult(null);
    try {
      const payload = parsePackageJson();
      if (!payload) {
        return;
      }
      const response = await apiFetch<PackageOperationResponse>("/api/workflows/validate", {
        method: "POST",
        headers: await getLocalAuthHeaders("workflow-admin"),
        body: JSON.stringify(payload)
      });
      setPackageResult(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to validate package / 校验包失败");
    } finally {
      setPackageBusy(null);
    }
  }

  async function importPackage() {
    setPackageBusy("import");
    setError(null);
    setPackageResult(null);
    try {
      const payload = parsePackageJson();
      if (!payload) {
        return;
      }
      const response = await apiFetch<PackageOperationResponse>(
        `/api/workflows/import?save_as_current=${importSaveAsCurrent}`,
        {
          method: "POST",
          headers: await getLocalAuthHeaders("workflow-admin"),
          body: JSON.stringify(payload)
        }
      );
      setPackageResult(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to import package / 导入包失败");
    } finally {
      setPackageBusy(null);
    }
  }

  const importedWorkflowId = packageResult?.workflow_package?.workflow_id ?? packageResult?.workflow_id;
  const importedVersion = packageResult?.workflow_package?.version ?? packageResult?.version;

  return (
    <div className="grid gap-6 lg:grid-cols-[0.82fr_1.18fr]">
      <section className="section-panel">
        <div>
          <p className="section-kicker">Builder / 构建器</p>
          <h1 className="page-heading mt-1">New Workflow / 新建工作流</h1>
          <p className="mt-1 text-sm text-slate-600">Describe the business process or import a package JSON. 描述业务流程，或导入工作流包 JSON。</p>
        </div>
        <form className="mt-4 space-y-4" onSubmit={onSubmit}>
          <FieldLabel label="Request / 需求描述">
            <textarea
              className="control-textarea min-h-32"
              value={userRequest}
              onChange={(event) => setUserRequest(event.target.value)}
            />
          </FieldLabel>
          <div className="grid gap-3 md:grid-cols-2">
            <FieldLabel label="Workflow ID / 工作流 ID">
              <input
                className="control-input w-full"
                value={workflowId}
                onChange={(event) => setWorkflowId(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Version / 版本">
              <input
                className="control-input w-full"
                value={version}
                onChange={(event) => setVersion(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Name / 名称">
              <input
                className="control-input w-full"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </FieldLabel>
            <label className="flex items-center gap-2 pt-6 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={saveAsCurrent}
                onChange={(event) => setSaveAsCurrent(event.target.checked)}
                className="h-4 w-4 rounded border-line"
              />
              Save as current version / 保存为当前版本
            </label>
          </div>
          <FieldLabel label="Business Goal / 业务目标">
            <textarea
              className="control-textarea min-h-20"
              value={businessGoal}
              onChange={(event) => setBusinessGoal(event.target.value)}
            />
          </FieldLabel>
          <div className="grid gap-3 md:grid-cols-2">
            <FieldLabel label="Start Event / 触发事件">
              <textarea
                className="control-textarea min-h-20"
                value={startEvent}
                onChange={(event) => setStartEvent(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="End State / 结束状态">
              <textarea
                className="control-textarea min-h-20"
                value={endState}
                onChange={(event) => setEndState(event.target.value)}
              />
            </FieldLabel>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <FieldLabel label="Target Users / 目标用户">
              <textarea
                className="control-textarea min-h-20"
                value={targetUsersText}
                onChange={(event) => setTargetUsersText(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Human Roles / 人类角色">
              <textarea
                className="control-textarea min-h-20"
                value={humanRolesText}
                onChange={(event) => setHumanRolesText(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Success Metrics / 成功指标">
              <textarea
                className="control-textarea min-h-20"
                value={successMetricsText}
                onChange={(event) => setSuccessMetricsText(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Constraints / 约束">
              <textarea
                className="control-textarea min-h-20"
                value={constraintsText}
                onChange={(event) => setConstraintsText(event.target.value)}
              />
            </FieldLabel>
            <FieldLabel label="Risks / 风险">
              <textarea
                className="control-textarea min-h-20"
                value={risksText}
                onChange={(event) => setRisksText(event.target.value)}
              />
            </FieldLabel>
          </div>
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-ink">Process Nodes / 流程节点</h2>
              <button
                type="button"
                onClick={addNode}
                className="control-button"
              >
                <Plus className="h-4 w-4" aria-hidden />
                Add Node / 添加节点
              </button>
            </div>
            {processNodes.map((node) => (
              <div key={node.id} className="rounded-md border border-line bg-field p-3">
                <div className="grid gap-3 md:grid-cols-2">
                  <FieldLabel label="Node Name / 节点名称">
                    <input
                      className="control-input w-full"
                      value={node.name}
                      onChange={(event) => updateNode(node.id, { name: event.target.value })}
                    />
                  </FieldLabel>
                  <FieldLabel label="Node Type / 节点类型">
                    <select
                      className="control-input w-full"
                      value={node.node_type}
                      onChange={(event) =>
                        updateNode(node.id, { node_type: event.target.value as ProcessNodeDraft["node_type"] })
                      }
                    >
                      <option value="read_node">read_node</option>
                      <option value="reasoning_node">reasoning_node</option>
                      <option value="review_node">review_node</option>
                      <option value="write_node">write_node</option>
                    </select>
                  </FieldLabel>
                  <FieldLabel label="Owner Role / 负责人角色">
                    <input
                      className="control-input w-full"
                      value={node.owner_role}
                      onChange={(event) => updateNode(node.id, { owner_role: event.target.value })}
                    />
                  </FieldLabel>
                  <label className="flex items-center gap-2 pt-6 text-sm text-slate-700">
                    <input
                      type="checkbox"
                      checked={node.requires_approval}
                      onChange={(event) => updateNode(node.id, { requires_approval: event.target.checked })}
                      className="h-4 w-4 rounded border-line"
                    />
                    Requires approval / 需要审批
                  </label>
                  <FieldLabel label="Description / 描述">
                    <textarea
                      className="control-textarea min-h-20"
                      value={node.description}
                      onChange={(event) => updateNode(node.id, { description: event.target.value })}
                    />
                  </FieldLabel>
                  <FieldLabel label="Done Condition / 完成条件">
                    <textarea
                      className="control-textarea min-h-20"
                      value={node.done_condition}
                      onChange={(event) => updateNode(node.id, { done_condition: event.target.value })}
                    />
                  </FieldLabel>
                </div>
                <button
                  type="button"
                  onClick={() => removeNode(node.id)}
                  className="mt-3 inline-flex items-center gap-2 rounded-md border border-red-200 bg-white px-3 py-2 text-sm font-medium text-red-700 hover:border-red-700"
                >
                  <Trash2 className="h-4 w-4" aria-hidden />
                  Remove / 移除
                </button>
              </div>
            ))}
          </div>
          <button
            type="submit"
            className="control-button-primary"
            disabled={loading}
          >
            <Sparkles className="h-4 w-4" aria-hidden />
            {loading ? "Generating... / 生成中..." : "Generate Workflow Package / 生成工作流包"}
          </button>
        </form>
        {error ? <p className="mt-3 text-sm text-red-700">{error}</p> : null}
        {result ? (
          <div className="mt-5 rounded-md border border-line bg-white p-4">
            <h2 className="text-sm font-semibold text-ink">Clarifying Questions / 澄清问题</h2>
            <ul className="mt-3 space-y-2 text-sm text-slate-700">
              {result.clarifying_questions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
            <Link
              href={`/workflows/${result.workflow_package.workflow_id}`}
              className="mt-4 inline-flex text-sm font-medium text-accent hover:underline"
            >
              Open workflow detail / 打开工作流详情
            </Link>
          </div>
        ) : null}
        <div className="mt-6 space-y-4 border-t border-line pt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-ink">Package Import / 包导入</h2>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={importSaveAsCurrent}
                onChange={(event) => setImportSaveAsCurrent(event.target.checked)}
                className="h-4 w-4 rounded border-line"
              />
              Save as current version / 保存为当前版本
            </label>
          </div>
          <textarea
            className="control-textarea min-h-56 font-mono text-xs"
            value={packageJsonText}
            onChange={(event) => setPackageJsonText(event.target.value)}
            spellCheck={false}
          />
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={validatePackage}
              className="control-button"
              disabled={packageBusy !== null}
            >
              <Check className="h-4 w-4" aria-hidden />
              {packageBusy === "validate" ? "Validating... / 校验中..." : "Validate Package / 校验包"}
            </button>
            <button
              type="button"
              onClick={importPackage}
              className="control-button-primary"
              disabled={packageBusy !== null}
            >
              <Upload className="h-4 w-4" aria-hidden />
              {packageBusy === "import" ? "Importing... / 导入中..." : "Import Package / 导入包"}
            </button>
          </div>
          {packageResult ? (
            <div className="rounded-md border border-line bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold text-ink">Package Result / 包处理结果</h3>
                  <p className="mt-1 text-xs text-slate-500">
                    {packageResult.valid === false
                      ? "Validation failed / 校验失败"
                      : packageResult.saved_as_current === undefined
                        ? "Validation completed / 校验完成"
                        : packageResult.saved_as_current
                          ? "Saved as current version / 已保存为当前版本"
                          : "Saved as candidate version / 已保存为候选版本"}
                  </p>
                </div>
                {packageResult.valid !== false && packageResult.workflow_package && importedWorkflowId ? (
                  <Link
                    href={`/workflows/${importedWorkflowId}${importedVersion ? `?version=${importedVersion}` : ""}`}
                    className="text-sm font-medium text-accent hover:underline"
                  >
                    Open workflow detail / 打开工作流详情
                  </Link>
                ) : null}
              </div>
              <div className="mt-4">
                <JsonViewer data={packageResult} />
              </div>
            </div>
          ) : null}
        </div>
      </section>
      <section>
        <h2 className="mb-4 text-sm font-semibold text-ink">Workflow Package JSON / 工作流包 JSON</h2>
        <JsonViewer data={result?.workflow_package ?? { status: "waiting_for_generation" }} />
      </section>
    </div>
  );
}

function FieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="form-label">
      <span className="form-label-text">{label}</span>
      {children}
    </label>
  );
}

function lines(value: string) {
  const items = value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length > 0 ? items : undefined;
}
