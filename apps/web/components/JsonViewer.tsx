type JsonViewerProps = {
  data: unknown;
};

export function JsonViewer({ data }: JsonViewerProps) {
  return (
    <pre className="max-h-[520px] overflow-auto rounded-md border border-line bg-white p-4 text-xs leading-5 text-slate-800">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
