/** Placeholder cards shown while a page waits for the audit payload. */
export default function Skeleton({ cards = 8 }) {
  return (
    <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(210px,1fr))" }}>
      {Array.from({ length: cards }).map((_, i) => (
        <div
          key={i}
          className="card"
          style={{ height: 92, animation: "pulse 1.4s ease-in-out infinite" }}
        />
      ))}
      <style>{`@keyframes pulse{0%,100%{opacity:.45}50%{opacity:.8}}`}</style>
    </div>
  );
}
