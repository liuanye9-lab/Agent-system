type JsonViewerProps = {
  data: unknown;
};

export function JsonViewer({ data }: JsonViewerProps) {
  return (
    <pre className="code-panel">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
