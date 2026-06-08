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
                <details className="mt-3 text-xs text-slate-700">
                  <summary className="cursor-pointer">Input / Output / 输入与输出</summary>
                  <pre className="mt-2 rounded-sm bg-field p-3">
                    {JSON.stringify(
                      { input: trace.input_snapshot, output: trace.output_snapshot },
                      null,
                      2
                    )}
                  </pre>
                </details>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
