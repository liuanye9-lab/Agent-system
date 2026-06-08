type ProgressBarProps = {
  value: number;
  label?: string;
};

export function ProgressBar({ value, label }: ProgressBarProps) {
  const normalized = Math.max(0, Math.min(100, value));
  return (
    <div className="w-full">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-600">
        <span>{label ?? "Progress"}</span>
        <span>{normalized}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-sm bg-slate-200">
        <div className="h-full bg-accent" style={{ width: `${normalized}%` }} />
      </div>
    </div>
  );
}
