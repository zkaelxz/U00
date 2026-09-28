export function StagePlaceholder({ title }: { title: string }) {
  return (
    <section className="panel" aria-label={title}>
      <h2>{title}</h2>
      <p className="muted">Coming soon in the React app. It still lives in the Streamlit app.</p>
    </section>
  )
}
