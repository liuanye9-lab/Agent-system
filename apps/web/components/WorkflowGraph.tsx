import { ArrowRight, CheckCircle2, FileJson, GitBranch, ShieldCheck, Wrench } from "lucide-react";
import type { ReactNode } from "react";

type WorkflowProcessNode = {
  node_id: string;
  name: string;
  node_type: string;
  owner_role: string;
  description?: string;
  done_condition?: string;
  requires_approval?: boolean;
  input_contract_id: string;
  output_contract_id: string;
  tool_ids: string[];
};

type WorkflowProcessEdge = {
  source_node_id: string;
  target_node_id: string;
  condition?: string | null;
  edge_type?: string;
};

type WorkflowProcessSpec = {
  nodes: WorkflowProcessNode[];
  edges: WorkflowProcessEdge[];
  entry_node_id: string;
  terminal_node_ids: string[];
};

type WorkflowToolPolicy = {
  tool_id: string;
  name: string;
  permission_level: string;
  risk_level: string;
  adapter?: string;
  requires_approval?: boolean;
};

type WorkflowDataContract = {
  contract_id: string;
  name: string;
};

type WorkflowGraphProps = {
  processSpec: WorkflowProcessSpec;
  toolPolicies: WorkflowToolPolicy[];
  dataContracts: WorkflowDataContract[];
};

export function WorkflowGraph({ processSpec, toolPolicies, dataContracts }: WorkflowGraphProps) {
  const toolsById = new Map(toolPolicies.map((tool) => [tool.tool_id, tool]));
  const contractsById = new Map(dataContracts.map((contract) => [contract.contract_id, contract]));
  const outgoingEdges = processSpec.edges.reduce<Record<string, WorkflowProcessEdge[]>>((acc, edge) => {
    acc[edge.source_node_id] = [...(acc[edge.source_node_id] ?? []), edge];
    return acc;
  }, {});
  const approvalNodeCount = processSpec.nodes.filter((node) => node.requires_approval).length;

  return (
    <section className="surface p-5">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-accent">Workflow graph / 工作流图</p>
          <h2 className="mt-1 text-base font-semibold text-ink">Execution topology / 执行拓扑</h2>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="status-pill">{processSpec.nodes.length} nodes / 节点</span>
          <span className="status-pill">{processSpec.edges.length} edges / 边</span>
          <span className="status-pill">{approvalNodeCount} approvals / 审批点</span>
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
        <div className="space-y-3">
          {processSpec.nodes.map((node, index) => {
            const nodeTools = node.tool_ids.map((toolId) => toolsById.get(toolId)).filter(Boolean) as WorkflowToolPolicy[];
            const inputContract = contractsById.get(node.input_contract_id);
            const outputContract = contractsById.get(node.output_contract_id);
            const edges = outgoingEdges[node.node_id] ?? [];
            return (
              <div key={node.node_id} className="rounded-md border border-line bg-white">
                <div className="grid gap-4 p-4 lg:grid-cols-[56px_1fr]">
                  <div className="flex lg:block">
                    <span className="flex h-10 w-10 items-center justify-center rounded-md bg-field text-sm font-semibold text-accent">
                      {index + 1}
                    </span>
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="truncate text-sm font-semibold text-ink">{node.name}</h3>
                          <span className="status-pill">{node.node_type}</span>
                          {node.requires_approval ? (
                            <span className="status-pill border-amber-200 bg-amber-50 text-amber-700">approval / 审批</span>
                          ) : null}
                        </div>
                        <p className="mt-1 break-all text-xs text-slate-500">{node.node_id}</p>
                      </div>
                      <span className="status-pill">{node.owner_role}</span>
                    </div>

                    {node.description ? <p className="mt-3 text-sm leading-6 text-slate-600">{node.description}</p> : null}

                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      <NodeInfo icon={<FileJson className="h-4 w-4" aria-hidden />} label="Input / 输入" value={inputContract?.name ?? node.input_contract_id} />
                      <NodeInfo icon={<FileJson className="h-4 w-4" aria-hidden />} label="Output / 输出" value={outputContract?.name ?? node.output_contract_id} />
                    </div>

                    {nodeTools.length > 0 ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {nodeTools.map((tool) => (
                          <span key={tool.tool_id} className="inline-flex items-center gap-2 rounded-sm border border-line bg-field px-2 py-1 text-xs text-slate-700">
                            <Wrench className="h-3.5 w-3.5 text-accent" aria-hidden />
                            {tool.name}
                            <span className="text-slate-500">{tool.permission_level}</span>
                            <RiskBadge risk={tool.risk_level} />
                          </span>
                        ))}
                      </div>
                    ) : null}

                    {node.done_condition ? (
                      <p className="mt-4 flex gap-2 text-xs leading-5 text-slate-600">
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-accent" aria-hidden />
                        <span>{node.done_condition}</span>
                      </p>
                    ) : null}

                    {edges.length > 0 ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {edges.map((edge) => (
                          <span key={`${edge.source_node_id}-${edge.target_node_id}-${edge.condition ?? "default"}`} className="inline-flex items-center gap-2 rounded-sm border border-line bg-white px-2 py-1 text-xs text-slate-700">
                            <ArrowRight className="h-3.5 w-3.5 text-accent" aria-hidden />
                            {edge.target_node_id}
                            {edge.condition ? <span className="text-slate-500">{edge.condition}</span> : null}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <aside className="space-y-3">
          <GraphSideCard
            icon={<GitBranch className="h-4 w-4" aria-hidden />}
            title="Entry / 入口"
            value={processSpec.entry_node_id}
          />
          <GraphSideCard
            icon={<ShieldCheck className="h-4 w-4" aria-hidden />}
            title="Terminal / 终止节点"
            value={processSpec.terminal_node_ids.join(", ")}
          />
          <div className="rounded-md border border-line bg-field p-4">
            <h3 className="text-sm font-semibold text-ink">Edge map / 边映射</h3>
            <div className="mt-3 space-y-2">
              {processSpec.edges.length > 0 ? (
                processSpec.edges.map((edge) => (
                  <div key={`${edge.source_node_id}-${edge.target_node_id}-${edge.condition ?? "default"}`} className="rounded-sm bg-white px-3 py-2 text-xs text-slate-700">
                    <span className="font-medium text-ink">{edge.source_node_id}</span>
                    <span className="mx-2 text-accent">→</span>
                    <span className="font-medium text-ink">{edge.target_node_id}</span>
                    <span className="ml-2 text-slate-500">{edge.edge_type ?? "default"}</span>
                  </div>
                ))
              ) : (
                <p className="text-sm text-slate-600">No explicit edges. 未配置显式边。</p>
              )}
            </div>
          </div>
        </aside>
      </div>
    </section>
  );
}

function NodeInfo({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-sm border border-line bg-field px-3 py-2">
      <div className="flex items-center gap-2 text-xs font-medium uppercase text-slate-500">
        {icon}
        {label}
      </div>
      <p className="mt-1 truncate text-sm font-medium text-ink">{value}</p>
    </div>
  );
}

function GraphSideCard({ icon, title, value }: { icon: ReactNode; title: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-field p-4">
      <div className="flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">
        {icon}
        {title}
      </div>
      <p className="mt-2 break-all text-sm font-medium text-ink">{value}</p>
    </div>
  );
}

function RiskBadge({ risk }: { risk: string }) {
  const className =
    risk === "high"
      ? "border-red-200 bg-red-50 text-red-700"
      : risk === "medium"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "border-emerald-200 bg-emerald-50 text-emerald-700";
  return <span className={`rounded-sm border px-1.5 py-0.5 ${className}`}>{risk}</span>;
}
