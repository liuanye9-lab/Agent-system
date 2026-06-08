import { AlertTriangle, CheckCircle2, CirclePause, CircleSlash } from "lucide-react";

type Trace = {
  node_id: string;
  status: string;
  error?: string | null;
  duration_ms?: number | null;
  input_snapshot?: unknown;
  output_snapshot?: unknown;
};

const statusIcon = {
  success: CheckCircle2,
  approval_required: CirclePause,
  failed: AlertTriangle,
  skipped: CircleSlash
};

export function TraceTimeline({ traces }: { traces: Trace[] }) {
  if (traces.length === 0) {
    return <p className="text-sm text-slate-600">No trace records yet. 暂无追踪记录。</p>;
  }

  return (
    <div className="space-y-3">
      {traces.map((trace, index) => {
        const Icon = statusIcon[trace.status as keyof typeof statusIcon] ?? CheckCircle2;
        return (
          <div key={`${trace.node_id}-${index}`} className="surface p-4">
            <div className="flex items-start gap-3">
              <Icon className="mt-0.5 h-4 w-4 text-accent" aria-hidden />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-ink">{trace.node_id}</h3>
                  <span className="status-pill">{trace.status}</span>
                </div>
                <p className="mt-1 text-xs text-slate-500">{trace.duration_ms ?? 0} ms</p>
                {trace.error ? <p className="mt-2 text-sm text-red-700">{trace.error}</p> : null}
                <div className="mt-3 grid gap-2 md:grid-cols-2">
                  <PayloadSummary title="Input / 输入" value={trace.input_snapshot} />
                  <PayloadSummary title="Output / 输出" value={trace.output_snapshot} />
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PayloadSummary({ title, value }: { title: string; value: unknown }) {
  const summary = summarizePayload(value);
  return (
    <div className="rounded-sm border border-line bg-[#fbfcfd] p-3 text-xs text-slate-600">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-ink">{title}</span>
        <span className="status-pill px-1.5 py-0.5">{summary.kind}</span>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-2">
        <div>
          <dt className="uppercase text-slate-500">Keys / 键</dt>
          <dd className="font-semibold text-ink">{summary.keyCount}</dd>
        </div>
        <div>
          <dt className="uppercase text-slate-500">Sensitive / 敏感</dt>
          <dd className="font-semibold text-ink">{summary.sensitiveKeyCount}</dd>
        </div>
      </dl>
      <p className="mt-2 break-words text-slate-500">{summary.preview}</p>
    </div>
  );
}

function summarizePayload(value: unknown) {
  if (value === null || value === undefined) {
    return { kind: "empty", keyCount: 0, sensitiveKeyCount: 0, preview: "No payload / 无载荷" };
  }
  if (Array.isArray(value)) {
    return {
      kind: "array",
      keyCount: value.length,
      sensitiveKeyCount: countSensitiveKeys(value),
      preview: `${value.length} items / 项`
    };
  }
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    const visibleKeys = keys.slice(0, 8);
    const hiddenCount = Math.max(keys.length - visibleKeys.length, 0);
    return {
      kind: "object",
      keyCount: keys.length,
      sensitiveKeyCount: countSensitiveKeys(value),
      preview: visibleKeys.length
        ? `${visibleKeys.join(", ")}${hiddenCount ? ` +${hiddenCount}` : ""}`
        : "No top-level keys / 无顶层键"
    };
  }
  return { kind: typeof value, keyCount: 1, sensitiveKeyCount: 0, preview: "Primitive value redacted / 基础值已摘要" };
}

function countSensitiveKeys(value: unknown): number {
  if (!value || typeof value !== "object") {
    return 0;
  }
  if (Array.isArray(value)) {
    return value.reduce((count, item) => count + countSensitiveKeys(item), 0);
  }
  return Object.entries(value as Record<string, unknown>).reduce((count, [key, nested]) => {
    const isSensitive = /token|password|secret|api[_-]?key|authorization|credential/i.test(key);
    return count + (isSensitive ? 1 : 0) + countSensitiveKeys(nested);
  }, 0);
}
